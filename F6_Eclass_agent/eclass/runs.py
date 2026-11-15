"""실행 기록 — 잠금 파일 · 실행 이력 · 재시도 대기 · 설정 (표준 라이브러리만).

수집 프로세스(runner)와 대시보드 백엔드가 같은 파일을 본다.
  state/sync.lock     도는 동안 {pid, started_at, source, attempt, args} — 살아 있는 pid 면 다른 실행은 물러난다 (F6-R14·R53)
  state/runs.jsonl    한 번 돌 때마다 한 줄 {started_at, finished_at, duration_s, exit_code, source, attempt, counts, error, ledger}
  state/sync.last.json 마지막 실행 (예전 eclass_agent 모양 + 위 필드)
  state/retry.json    네트워크 오류 뒤 다음 재시도를 기다리는 중이면 {attempt, next_at, source, slot} (F6-R12)
  state/settings.json {intervalHours} — 주기 (F6-R18)

source: schedule(정각 예약) · catchup(놓친 주기 따라잡기) · retry(재시도) · button(대시보드) · manual(명령줄)
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import config as C
from .atomic import write_text as atomic_write_text

SOURCES = ("schedule", "catchup", "retry", "button", "manual")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, obj: Any) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- 프로세스

pid_alive = C.pid_alive          # 살아 있는 python 프로세스인가 — C0 osenv (Windows tasklist · 그 외 os.kill)


# ---------------------------------------------------------------- 잠금

def read_lock() -> tuple[dict, float]:
    """잠금 내용과 나이(초). 파일이 없으면 ({}, 0), 있는데 못 읽으면 ({}, 나이)."""
    try:
        age = time.time() - C.LOCK_FILE.stat().st_mtime
    except OSError:
        return {}, 0.0
    info = read_json(C.LOCK_FILE, {})
    return (info if isinstance(info, dict) else {}), age


def live_lock() -> Optional[dict]:
    """잠금이 있고 그 pid 가 살아 있으면 그 내용 (예약 실행·다른 창·대시보드 버튼 중 누군가 도는 중)."""
    info, _ = read_lock()
    pid = info.get("pid")
    if isinstance(pid, int) and pid_alive(pid):
        return info
    return None


def acquire_lock(source: str, attempt: int = 1, args: Optional[list] = None) -> bool:
    """state/sync.lock 을 원자적으로(O_EXCL) 만든다. 살아 있는 실행이 잡고 있으면 False."""
    C.LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        try:
            fd = os.open(C.LOCK_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            info, age = read_lock()
            pid = info.get("pid")
            if isinstance(pid, int) and pid_alive(pid):
                print(f"이미 실행 중입니다 (pid {pid}, {info.get('started_at', '?')} 시작) — 이번 실행은 건너뜁니다.")
                return False
            if not info and age < 30:
                time.sleep(1)          # 다른 실행이 막 만드는 중이거나 방금 사라진 것 → 잠깐 뒤 다시
                continue
            print(f"남아 있던 잠금 파일 정리 (pid {pid or '?'} 는 이미 종료)")
            C.LOCK_FILE.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "started_at": now_iso(), "source": source, "attempt": attempt,
                       "args": args if args is not None else sys.argv[1:]}, f, ensure_ascii=False)
        return True
    print(f"잠금 파일을 잡지 못했습니다 ({C.LOCK_FILE}) — 이번 실행은 건너뜁니다.")
    return False


def release_lock(record: dict) -> None:
    """내 잠금을 풀고 결과를 남긴다 (대시보드가 예약 실행의 결과도 볼 수 있게)."""
    info, _ = read_lock()
    if info.get("pid") != os.getpid():      # 내 것이 아니면 건드리지 않는다
        return
    append_run(record)
    C.LOCK_FILE.unlink(missing_ok=True)


# ---------------------------------------------------------------- 실행 이력 (F6-R16)

def append_run(record: dict) -> None:
    """실행 한 번을 이력에 더하고 sync.last.json 도 갱신한다. 최근 RUNS_KEEP 줄만 남긴다."""
    C.STATE_DIR.mkdir(parents=True, exist_ok=True)
    lines = []
    try:
        lines = C.RUNS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        pass
    lines.append(json.dumps(record, ensure_ascii=False))
    lines = [l for l in lines if l.strip()][-C.RUNS_KEEP:]
    atomic_write_text(C.RUNS_FILE, "\n".join(lines) + "\n")
    write_json(C.LAST_RUN_FILE, record)


def list_runs(limit: Optional[int] = None) -> list[dict]:
    """실행 이력, 최신 먼저. runs.jsonl 이 없던 예전 설치면 sync.last.json 한 줄이라도."""
    out: list[dict] = []
    try:
        for line in C.RUNS_FILE.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict) and r.get("started_at"):
                out.append(r)
    except OSError:
        last = read_json(C.LAST_RUN_FILE, None)
        if isinstance(last, dict) and last.get("started_at"):
            out.append({"source": "manual", "attempt": 1, **last})
    out.reverse()                                   # 같은 초에 시작한 실행은 나중에 적힌 것이 먼저 (정렬은 안정적)
    out.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return out[:limit] if limit else out


def failed(r: dict) -> bool:
    """실패로 칠 실행인가 — 성공(0)과 '다른 실행이 진행 중이라 건너뜀'(3)은 실패가 아니다."""
    return r.get("exit_code") not in (C.EXIT_OK, C.EXIT_BUSY)


def failure_streak(runs: list[dict]) -> dict:
    """최근부터 연속 실패 (F6-R15). 재시도 사슬(attempt 2~)은 첫 시도와 합쳐 한 번으로 센다 — 네트워크가 45분 끊겼다고
    곧장 '연속 3회'가 되지 않게. {count, since(첫 실패 시각), lastOkAt, lastError, lastCode}"""
    count = 0
    since = None
    last_error = None
    last_code = None
    last_ok = None
    for r in runs:                                   # 최신 먼저
        code = r.get("exit_code")
        if code == C.EXIT_BUSY:
            continue
        if code == C.EXIT_OK:
            last_ok = r.get("finished_at") or r.get("started_at")
            break
        if last_code is None:
            last_code, last_error = code, r.get("error")
        if int(r.get("attempt") or 1) <= 1:
            count += 1
            since = r.get("started_at")
    if last_ok is None:
        last_ok = next((r.get("finished_at") or r.get("started_at") for r in runs if r.get("exit_code") == C.EXIT_OK), None)
    return {"count": count, "since": since, "lastOkAt": last_ok, "lastError": last_error, "lastCode": last_code}


# ---------------------------------------------------------------- 재시도 대기

def set_retry(attempt: int, next_at: datetime, source: str, slot: Optional[str]) -> None:
    write_json(C.RETRY_FILE, {"attempt": attempt, "next_at": next_at.isoformat(timespec="seconds"), "source": source,
                              "slot": slot, "pid": os.getpid()})


def clear_retry() -> None:
    C.RETRY_FILE.unlink(missing_ok=True)


def pending_retry() -> Optional[dict]:
    """재시도를 기다리는 프로세스가 살아 있으면 {attempt, next_at, …}."""
    info = read_json(C.RETRY_FILE, None)
    if not isinstance(info, dict):
        return None
    pid = info.get("pid")
    if isinstance(pid, int) and not pid_alive(pid):
        return None
    return info


# ---------------------------------------------------------------- 설정 (F6-R18)

def settings() -> dict:
    s = read_json(C.SETTINGS_FILE, {})
    s = s if isinstance(s, dict) else {}
    h = s.get("intervalHours")
    if h not in C.INTERVAL_CHOICES:
        h = C.DEFAULT_INTERVAL_HOURS
    return {"intervalHours": h}


def save_settings(**kw) -> dict:
    cur = read_json(C.SETTINGS_FILE, {})
    cur = cur if isinstance(cur, dict) else {}
    cur.update({k: v for k, v in kw.items() if v is not None})
    write_json(C.SETTINGS_FILE, cur)
    return settings()
