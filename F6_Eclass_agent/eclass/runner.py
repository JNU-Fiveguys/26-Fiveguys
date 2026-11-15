"""실행기 — 잠금 → 수집(collect) → 원장 반영(reconcile) → 실행 이력. **playwright 가 있는 python(앱 실행 파일 / 개발 venv)으로 돈다.**

    python -m eclass sync [--source button|manual] [--dry-run] [--course ID] [--only PARTS] [--log FILE]
        한 번 수집. 대시보드 버튼·명령줄(cli.py eclass sync). 실패해도 재시도하지 않는다(사람이 보고 있다).
    python -m eclass tick [--log FILE]
        작업 스케줄러·launchd 가 부른다(앱 실행 파일 --run-module eclass tick). 가장 최근 정각 이후 실행이 없을 때만 돌고(schedule/catchup),
        네트워크 오류면 5 → 15 → 45분 뒤 다시(최대 3회, source=retry). 기다리는 동안 잠금을 잡지 않는다 —
        그 사이 누가 '지금 수집'을 누르면 그것이 우선하고, 성공하면 남은 재시도는 그만둔다 (F6 5절).

종료 코드: 0 성공 · 1 오류(설정·화면 구조) · 2 로그인 필요 · 3 다른 실행이 진행 중 · 4 네트워크 오류
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import datetime, timedelta
from typing import Optional

from . import config as C
from . import runs as R
from . import schedule, store


def redirect_output(path: str) -> None:
    """stdout/stderr(fd 1·2)를 path 에 덧붙인다. 파이썬이 열면 공유 모드라 여러 실행이 같은 로그를 써도 되고
    (cmd 의 >> 는 두 번째 실행이 열지 못하고 죽는다), fd 를 바꾸므로 Playwright 드라이버 같은 자식 출력도 들어간다."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.close(fd)
    sys.stdout = open(1, "w", encoding="utf-8", buffering=1, closefd=False)   # 줄 단위 flush → 진행 상황이 바로 보인다
    sys.stderr = open(2, "w", encoding="utf-8", buffering=1, closefd=False)


def run_once(source: str, attempt: int = 1, dry_run: bool = False, courses: Optional[list[str]] = None,
             only: Optional[set[str]] = None, slot: Optional[str] = None) -> dict:
    """한 번 돈다. 잠금을 못 잡으면 {'exit_code': 3} (이력에는 남기지 않는다 — 도는 쪽이 남긴다)."""
    if not R.acquire_lock(source, attempt):
        return {"exit_code": C.EXIT_BUSY, "source": source, "attempt": attempt}
    started = datetime.now()
    t0 = time.monotonic()
    print(f"-- 수집 시작 ({source}{f' · 재시도 {attempt - 1}회째' if attempt > 1 else ''})")
    rec = {"pid": os.getpid(), "started_at": started.isoformat(timespec="seconds"), "source": source,
           "attempt": attempt, "slot": slot, "dryRun": dry_run, "args": sys.argv[1:]}
    try:
        from . import collect                  # playwright · bs4 — 수집 프로세스에서만
        res = collect.run(dry_run=dry_run, courses_only=courses, only=only)
        code, counts, error, full = res.code, res.counts, res.error, res.full
        partial, warnings, error_kind = res.partial, res.warnings, res.error_kind
    except Exception as e:                      # noqa: BLE001 — 무엇이 터져도 이력은 남긴다
        traceback.print_exc()
        code, counts, error, full = C.EXIT_ERROR, {}, f"{type(e).__name__}: {str(e)[:200]}", False
        partial, warnings, error_kind = False, [], None
    ledger = None
    if code == C.EXIT_OK and not dry_run:
        try:
            with store.connect() as con:
                from . import reconcile
                ledger = reconcile.apply(con, full=full, force=True)
            print(f"-- 원장 반영: 새 과제 {ledger['new']} · 마감 변경 {ledger['changed']} · 사라짐 {ledger['removed']}"
                  f" · 제출 확인 {ledger['submitted']}")
        except Exception as e:                  # noqa: BLE001
            traceback.print_exc()
            error = f"원장 반영 실패: {type(e).__name__}: {str(e)[:160]}"
        try:                                    # 새 글·자료 (과제 원장과 따로 — 실패해도 수집 결과에 영향 없음)
            with store.connect() as con:
                from . import feed
                fr = feed.sync(con)
            if fr.get("new") or fr.get("baseline"):
                print(f"-- 새 글·자료: {fr.get('new', 0)}건" + (f" (처음 들여옴 {fr['baseline']}건은 기준선)" if fr.get("baseline") else ""))
            counts = {**counts, "feedNew": fr.get("new", 0)}
        except Exception:                       # noqa: BLE001
            traceback.print_exc()
    rec.update(finished_at=datetime.now().isoformat(timespec="seconds"), duration_s=round(time.monotonic() - t0, 1),
               exit_code=code, counts=counts, error=error, ledger=ledger,
               partial=partial, warnings=warnings, errorKind=error_kind)
    R.release_lock(rec)
    print(f"-- 수집 끝: 코드 {code}{f' ({error})' if error else ''} · {rec['duration_s']}초")
    return rec


def _wait_until(when: datetime, slot: datetime, interval: int) -> str:
    """재시도 시각까지 기다린다. 그 사이 다른 실행이 성공했거나 다음 정각이 오면 그만둔다 → 'ok' | 'succeeded' | 'next_slot'.
    절전으로 멈췄다 깨어나도 벽시계로 판단한다(30초마다 확인)."""
    while True:
        now = datetime.now()
        if schedule.latest_slot(now, interval) > slot:
            return "next_slot"
        last = next((r for r in R.list_runs(5) if r.get("exit_code") != C.EXIT_BUSY), None)
        if last and last.get("exit_code") == C.EXIT_OK and (last.get("started_at") or "") >= slot.isoformat(timespec="seconds"):
            return "succeeded"
        if now >= when:
            return "ok"
        time.sleep(min(30.0, max(1.0, (when - now).total_seconds())))


def tick(force: bool = False) -> int:
    """작업 스케줄러 진입점 — 이번 주기를 돌려야 하면 돌고, 네트워크 오류면 재시도 사슬을 이어 간다."""
    interval = R.settings()["intervalHours"]
    now = datetime.now()
    d = schedule.decide(now, interval, R.list_runs(20))
    print(f"-- 예약 확인 {now:%Y-%m-%d %H:%M} · 주기 {interval}시간 · {d['reason']}")
    if not d["run"] and not force:
        return C.EXIT_OK
    slot: datetime = d["slot"]
    source = d["source"] or "schedule"
    rec = run_once(source, 1, slot=slot.isoformat(timespec="seconds"))
    attempt = 1
    try:
        while rec["exit_code"] == C.EXIT_NETWORK and attempt <= len(C.RETRY_WAITS_MIN):
            wait = C.RETRY_WAITS_MIN[attempt - 1]
            when = datetime.now() + timedelta(minutes=wait)
            R.set_retry(attempt + 1, when, source, slot.isoformat(timespec="seconds"))
            print(f"-- 네트워크 오류 → {wait}분 뒤({when:%H:%M}) 다시 시도합니다 ({attempt}/{len(C.RETRY_WAITS_MIN)})")
            why = _wait_until(when, slot, interval)
            R.clear_retry()
            if why != "ok":
                print("-- 재시도 그만둠: " + ("그 사이 다른 실행이 성공했습니다" if why == "succeeded" else "다음 주기가 시작됐습니다"))
                return C.EXIT_OK if why == "succeeded" else rec["exit_code"]
            attempt += 1
            rec = run_once("retry", attempt, slot=slot.isoformat(timespec="seconds"))
            if rec["exit_code"] == C.EXIT_BUSY:
                print("-- 다른 실행이 진행 중이라 재시도를 넘깁니다")
                return C.EXIT_OK
    finally:
        R.clear_retry()
    return rec["exit_code"]


def sync(source: str = "manual", dry_run: bool = False, courses: Optional[list[str]] = None,
         only: Optional[set[str]] = None) -> int:
    return run_once(source if source in R.SOURCES else "manual", 1, dry_run=dry_run, courses=courses, only=only)["exit_code"]
