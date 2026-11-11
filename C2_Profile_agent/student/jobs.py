"""대시보드 버튼 뒤에서 도는 일 두 가지 — 학과 목록 갱신 · 학사정보시스템 가져오기. 표준 라이브러리만.

master  교육과정검색 수집(표준 라이브러리) → data/master/departments.json. 백엔드 안의 스레드로 돈다(1분 남짓).
import  C3_Login_agent 의 .venv python 으로 `-m student.hakstd` 를 띄우고(브라우저·SSO 세션이 거기 있다),
        끝나면 결과 파일을 읽어 이 프로세스에서 프로필에 '자동'으로 넣는다 → DB 쓰기는 늘 백엔드 한 곳.
같은 일이 이미 돌고 있으면 새로 띄우지 않는다. 마지막 결과는 state/<일>.last.json 에 남겨 재시작해도 보인다.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from datetime import datetime
from typing import Any, Callable, Optional

from . import config as C
from . import master, master_crawl, service, store

on_change: Optional[Callable[[], None]] = None      # api.build_router 가 넣는다 — 가져오기로 프로필이 바뀌면 부른다

_lock = threading.Lock()
_state: dict[str, dict[str, Any]] = {
    name: {"running": False, "startedAt": None, "finishedAt": None, "ok": None, "result": None, "error": None,
           "needLogin": False}
    for name in ("master", "import")
}


def _last_file(name: str):
    return C.STATE_DIR / f"{name}.last.json"


def state(name: str) -> dict:
    with _lock:
        s = dict(_state[name])
    if not s["running"] and s["startedAt"] is None:
        try:
            s.update(json.loads(_last_file(name).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return s


def _finish(name: str, **kw) -> None:
    with _lock:
        _state[name].update(running=False, finishedAt=datetime.now().isoformat(timespec="seconds"), **kw)
        snap = dict(_state[name])
    C.STATE_DIR.mkdir(parents=True, exist_ok=True)
    _last_file(name).write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")


def _begin(name: str) -> Optional[dict]:
    """시작 표시. 이미 돌고 있으면 그 상태에 already_running 을 붙여 돌려준다."""
    with _lock:
        if _state[name]["running"]:
            return {**_state[name], "alreadyRunning": True}
        _state[name].update(running=True, startedAt=datetime.now().isoformat(timespec="seconds"), finishedAt=None,
                            ok=None, result=None, error=None, needLogin=False)
    return None


# ── 학과 목록 갱신 (C2-R02) ─────────────────────────────────

def _run_master(year: Optional[int]) -> None:
    lines: list[str] = []
    try:
        new = master_crawl.crawl(year, log=lines.append)
        old = master.load() if master.load().get("colleges") else None
        merged = master.merge_retired(new, old)
        master.save_local(merged)
        s = master.summary()
        _finish("master", ok=True, result={"colleges": s["colleges"], "count": s["count"], "year": s["year"]})
    except Exception as e:                          # noqa: BLE001 — 실패해도 기존 목록은 그대로 쓴다
        _finish("master", ok=False, error=str(e)[:300])


def start_master_sync(year: Optional[int] = None) -> dict:
    busy = _begin("master")
    if busy:
        return busy
    threading.Thread(target=_run_master, args=(year,), daemon=True).start()
    return state("master")


# ── 학사정보시스템 가져오기 (C2-R04) ─────────────────────────

def import_problem(interactive: bool) -> Optional[str]:
    """가져오기를 시작할 수 없는 이유. None 이면 시작해도 된다."""
    if C.chromium_state(C.C3_BROWSERS) == "installing":
        return "브라우저 엔진을 내려받는 중입니다 — 몇 분 뒤 다시 시도하세요"
    if not C.C3_BROWSERS.exists():
        return "브라우저 엔진이 없습니다 — 앱을 다시 실행하면 내려받습니다"
    if not interactive and not (C.C3_STATE.exists() or C.C3_CRED.exists() or C.HAKSTD_STATE.exists()):
        return "학교 로그인 기록이 없습니다 — '로그인 창 열기'로 가져오세요"
    return None


def _run_import(interactive: bool) -> None:
    C.STATE_DIR.mkdir(parents=True, exist_ok=True)
    C.IMPORT_OUT.unlink(missing_ok=True)
    cmd = C.module_cmd("student.hakstd", "--out", str(C.IMPORT_OUT))
    if interactive:
        cmd.append("--interactive")
    env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(C.C3_BROWSERS), "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1",
           "C3_AGENT_DIR": str(C.C3_AGENT_DIR), "C3_STATE_DIR": str(C.C3_STATE_DIR)}
    try:
        with open(C.IMPORT_LOG, "a", encoding="utf-8") as log:
            code = subprocess.run(cmd, cwd=str(C.ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
                                  timeout=C.INTERACTIVE_TIMEOUT if interactive else C.IMPORT_TIMEOUT).returncode
    except subprocess.TimeoutExpired:
        _finish("import", ok=False, error="시간이 초과되었습니다")
        return
    except Exception as e:                          # noqa: BLE001
        _finish("import", ok=False, error=f"실행하지 못했습니다: {e}"[:300])
        return
    if code == 2:
        _finish("import", ok=False, needLogin=True, error="학사정보시스템 로그인이 필요합니다")
        return
    if code != 0:
        _finish("import", ok=False, error="학사정보시스템에서 항목을 읽지 못했습니다 (형식 변경 의심 — state/import.log 확인)")
        return
    try:
        fetched = json.loads(C.IMPORT_OUT.read_text(encoding="utf-8"))["data"]
        with store.connect() as con:
            res = service.apply_import(con, fetched)
        _finish("import", ok=True, result=res)
        if res["changed"] and on_change:
            on_change()
    except Exception as e:                          # noqa: BLE001
        _finish("import", ok=False, error=f"가져온 값을 저장하지 못했습니다: {e}"[:300])
    finally:
        C.IMPORT_OUT.unlink(missing_ok=True)        # 성적 요약이 담긴 파일은 남기지 않는다


def start_import(interactive: bool = False) -> dict:
    busy = _begin("import")
    if busy:
        return busy
    threading.Thread(target=_run_import, args=(interactive,), daemon=True).start()
    return state("import")


def run_import_blocking(interactive: bool = False) -> dict:
    """명령줄용 — 끝날 때까지 기다린다."""
    busy = _begin("import")
    if busy:
        return busy
    _run_import(interactive)
    return state("import")
