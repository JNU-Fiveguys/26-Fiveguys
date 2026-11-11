"""대시보드 버튼 뒤에서 도는 일 두 가지 — 이수 내역 가져오기 · 교육과정 받기. 표준 라이브러리만.

import      C3_Login_agent 의 .venv python 으로 `-m graduation.hakstd` 를 띄우고(브라우저·SSO 세션이 거기 있다),
            끝나면 결과 파일을 읽어 이 프로세스에서 저장한다 → DB 쓰기는 늘 백엔드 한 곳. 결과 파일(성적)은 바로 지운다.
            같은 화면에서 읽은 평점·학년·취득학점은 on_profile 로 C2 프로필에 '자동'으로 넘긴다(내가 입력한 값은 C2 가 지킨다).
curriculum  교육과정검색 수집(표준 라이브러리) → data/curriculum/. 백엔드 안의 스레드로 돈다(학과 하나 10초 안팎).
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
from . import curriculum, service, store

on_change: Optional[Callable[[], None]] = None          # api.build_router 가 넣는다 — 이수 내역이 바뀌면 부른다
on_profile: Optional[Callable[[dict], Any]] = None      # 학사정보시스템에서 같이 읽은 평점·학년 → C2 프로필

_lock = threading.Lock()
_state: dict[str, dict[str, Any]] = {
    name: {"running": False, "startedAt": None, "finishedAt": None, "ok": None, "result": None, "error": None,
           "needLogin": False}
    for name in ("import", "curriculum")
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
    with _lock:
        if _state[name]["running"]:
            return {**_state[name], "alreadyRunning": True}
        _state[name].update(running=True, startedAt=datetime.now().isoformat(timespec="seconds"), finishedAt=None,
                            ok=None, result=None, error=None, needLogin=False)
    return None


# ── 이수 내역 가져오기 (F2-R01·R02) ──────────────────────────

def import_problem(interactive: bool) -> Optional[str]:
    """가져오기를 시작할 수 없는 이유. None 이면 시작해도 된다."""
    if C.chromium_state(C.C3_BROWSERS) == "installing":
        return "브라우저 엔진을 내려받는 중입니다 — 몇 분 뒤 다시 시도하세요"
    if not C.C3_BROWSERS.exists():
        return "브라우저 엔진이 없습니다 — 앱을 다시 실행하면 내려받습니다"
    if not (C.C2_AGENT_DIR / "student" / "hakstd.py").exists():
        return "C2_Profile_agent 가 없습니다 — 학사정보시스템 로그인 절차를 거기서 빌려 씁니다"
    if not interactive and not (C.C3_STATE.exists() or C.C3_CRED.exists() or C.C2_HAKSTD_STATE.exists()):
        return "학교 로그인 기록이 없습니다 — '로그인 창 열기'로 가져오세요"
    return None


def _run_import(interactive: bool) -> None:
    C.STATE_DIR.mkdir(parents=True, exist_ok=True)
    C.IMPORT_OUT.unlink(missing_ok=True)
    cmd = C.module_cmd("graduation.hakstd", "--out", str(C.IMPORT_OUT))
    if interactive:
        cmd.append("--interactive")
    env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(C.C3_BROWSERS), "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1",
           "C3_AGENT_DIR": str(C.C3_AGENT_DIR), "C3_STATE_DIR": str(C.C3_STATE_DIR), "C2_AGENT_DIR": str(C.C2_AGENT_DIR)}
    try:
        with open(C.IMPORT_LOG, "a", encoding="utf-8") as log:
            code = subprocess.run(cmd, cwd=str(C.ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
                                  timeout=C.INTERACTIVE_TIMEOUT if interactive else C.IMPORT_TIMEOUT).returncode
    except subprocess.TimeoutExpired:
        _finish("import", ok=False, error=f"시간이 초과되었습니다 (기록: {C.IMPORT_LOG})")
        return
    except Exception as e:                              # noqa: BLE001
        _finish("import", ok=False, error=f"실행하지 못했습니다: {e}"[:300])
        return
    if code == 2:
        _finish("import", ok=False, needLogin=True, error="학사정보시스템 로그인이 필요합니다")
        return
    if code != 0:
        _finish("import", ok=False, error="기이수성적 표를 읽지 못했습니다 (형식 변경 또는 오류 — 이전 이수 내역은 그대로 둡니다. "
                                          f"기록: {C.IMPORT_LOG})")
        return
    try:
        doc = json.loads(C.IMPORT_OUT.read_text(encoding="utf-8"))
        with store.connect() as con:
            res = service.apply_import(con, doc.get("courses") or [])
        profile_res = None
        if on_profile and doc.get("profile"):
            try:
                profile_res = on_profile(doc["profile"])
            except Exception as e:                      # noqa: BLE001 — 프로필 반영 실패가 이수 내역 저장을 되돌리지 않게
                profile_res = {"error": str(e)[:200]}
        _finish("import", ok=True, result={**res, "profile": profile_res})
        if on_change:
            on_change()
    except Exception as e:                              # noqa: BLE001
        _finish("import", ok=False, error=f"가져온 과목을 저장하지 못했습니다: {e}"[:300])
    finally:
        C.IMPORT_OUT.unlink(missing_ok=True)            # 성적이 담긴 파일은 남기지 않는다


def start_import(interactive: bool = False) -> dict:
    busy = _begin("import")
    if busy:
        return busy
    threading.Thread(target=_run_import, args=(interactive,), daemon=True).start()
    return state("import")


def run_import_blocking(interactive: bool = False) -> dict:
    busy = _begin("import")
    if busy:
        return busy
    _run_import(interactive)
    return state("import")


# ── 교육과정 받기 (F2-R16) ──────────────────────────────────

def _run_curriculum(college: str, codes: list[str], year: int) -> None:
    lines: list[str] = []
    try:
        http = curriculum.Http()
        got = []
        for code in codes:
            doc = curriculum.crawl(college, code, year, http=http, log=lines.append)
            if doc["courses"]:
                curriculum.save_local(doc)
                got.append({"code": code, "count": len(doc["courses"])})
        _finish("curriculum", ok=True, result={"year": year, "codes": got, "empty": [c for c in codes
                                                                                   if c not in {g['code'] for g in got}]})
        if on_change:
            on_change()
    except Exception as e:                              # noqa: BLE001 — 실패해도 계산은 과목 목록 없이 계속된다
        _finish("curriculum", ok=False, error=str(e)[:300])


def start_curriculum(college: Optional[str], codes: list[str], year: Optional[int]) -> dict:
    if not college or not codes or not year:
        return {**state("curriculum"), "error": "단과대·학과·입학년도가 있어야 교육과정을 받을 수 있습니다", "running": False}
    busy = _begin("curriculum")
    if busy:
        return busy
    threading.Thread(target=_run_curriculum, args=(college, codes, int(year)), daemon=True).start()
    return state("curriculum")
