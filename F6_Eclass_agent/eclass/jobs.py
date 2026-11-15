"""대시보드 백엔드 쪽 실행 관리 — 표준 라이브러리만 (백엔드 프로세스 안에서 돈다).

  동기화 버튼   osenv.module_cmd 로 `-m eclass sync --source button` 을 띄운다 (F6-R50~R52)
                이미 누가 돌고 있으면(이 서버 · 잠금 파일의 살아 있는 pid · 잠금 없는 eclass 프로세스) 새로 띄우지 않는다 (R53·R54)
  로그인 창     osenv.module_cmd 로 `-m login` (브라우저 창) → 로그인되면 곧바로 수집 (F6-S10)
  예약 작업     작업 스케줄러 등록 상태 읽기 · 주기를 바꾸면 register-task.ps1 로 다시 등록 (F6-R10·R18)

환경변수 F6_TASKS=off 면 작업 스케줄러를 건드리지 않는다(격리 테스트 서버).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import config as C
from . import runs as R

_lock = threading.Lock()
_sync: dict[str, Any] = {"running": False, "started_at": None, "finished_at": None, "exit_code": None}
_login: dict[str, Any] = {"running": False, "started_at": None, "finished_at": None, "exit_code": None, "ok": None,
                          "error": None}
_task_cache: dict[str, Any] = {"at": 0.0, "value": None}
SYNC_TIMEOUT = 30 * 60
LOGIN_TIMEOUT = 12 * 60
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _env() -> dict:
    env = {**os.environ, "PYTHONUTF8": "1", "C3_AGENT_DIR": str(C.C3_AGENT_DIR),
           "F6_DATA_DIR": str(C.DATA_DIR), "F6_STATE_DIR": str(C.STATE_DIR)}
    env.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(C.C3_BROWSERS))
    return env


def c3() -> Optional[Any]:
    """C3_Login_agent 의 login 패키지 (가벼운 부분만 — status·problem). 없으면 None."""
    if not (C.C3_AGENT_DIR / "login" / "__init__.py").exists():
        return None
    if str(C.C3_AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(C.C3_AGENT_DIR))
    try:
        import login                                 # type: ignore[import-not-found]
        return login
    except Exception:                                # noqa: BLE001
        return None


def login_status() -> dict:
    lm = c3()
    if lm is None:
        return {"available": False, "installed": False, "browsers": False, "hasSession": False, "hasCreds": False,
                "problem": f"C3_Login_agent 가 없습니다: {C.C3_AGENT_DIR}"}
    return {"available": True, **lm.status(), "problem": lm.problem()}


def problem() -> Optional[str]:
    lm = c3()
    if lm is None:
        return f"C3_Login_agent 가 없습니다: {C.C3_AGENT_DIR}"
    return lm.problem(need_login_record=False)


# ---------------------------------------------------------------- 동기화 (수집)

def _sync_processes() -> list[int]:
    """`-m eclass sync|tick` 을 돌리는 python 프로세스 pid — 잠금 파일이 없어도(지웠거나 막 시작) 잡아낸다.
    Windows 는 PowerShell CIM 으로 명령줄을 본다 (1초쯤 걸리므로 버튼을 누를 때만 부른다)."""
    # 수집은 지금 도는 python(개발 모드 venv) / 묶인 앱 실행 파일(`--run-module eclass sync`)로 돈다 — 같은 폴더의 실행 파일만 센다
    name = Path(sys.executable).name if C.FROZEN else "python%"
    prefix = str(Path(sys.executable).parent)
    if sys.platform == "win32":
        script = (f"Get-CimInstance Win32_Process -Filter \"Name like '{name}'\" | Where-Object {{ "
                  "$_.CommandLine -match '(-m|--run-module)\\s+eclass\\s+(sync|tick)' -and "
                  "\"$($_.ExecutablePath)\".StartsWith($env:UNIVUS_PROC_PREFIX, 'OrdinalIgnoreCase') } | "
                  "ForEach-Object { $_.ProcessId }")
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
    else:
        cmd = ["pgrep", "-f", r"(-m|--run-module) eclass (sync|tick)"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                             env={**_env(), "UNIVUS_PROC_PREFIX": prefix}, creationflags=NO_WINDOW).stdout
    except Exception:                                # noqa: BLE001
        return []
    return [int(x) for x in out.split() if x.isdigit() and int(x) != os.getpid()]


def sync_state() -> dict:
    """진행 상태. source: button(이 서버가 띄움) / external(예약 작업·명령줄). 안 돌고 있으면 가장 최근 실행의 결과.
    runSource 는 실행 이력의 원래 값(schedule·catchup·retry·button·manual), retry 는 재시도 대기 중이면 {attempt, next_at}."""
    with _lock:
        mine = dict(_sync)
    retry = R.pending_retry()
    base = {"retry": retry, "pid": None, "attempt": 1, "runSource": None, "error": None, "counts": None,
            "partial": False, "warnings": [], "errorKind": None}
    if mine["running"]:
        return {**base, **mine, "source": "button", "runSource": "button"}
    ext = R.live_lock()
    if ext:
        return {**base, "running": True, "started_at": ext.get("started_at"), "finished_at": None, "exit_code": None,
                "source": "button" if ext.get("source") == "button" else "external", "runSource": ext.get("source"),
                "attempt": ext.get("attempt") or 1, "pid": ext.get("pid")}
    last = next(iter(R.list_runs(1)), None)
    if last and (last.get("started_at") or "") >= (mine["started_at"] or ""):
        return {**base, "running": False, "started_at": last.get("started_at"), "finished_at": last.get("finished_at"),
                "exit_code": last.get("exit_code"), "source": "button" if last.get("source") == "button" else "external",
                "runSource": last.get("source"), "attempt": last.get("attempt") or 1, "error": last.get("error"),
                "counts": last.get("counts"), "ledger": last.get("ledger"),
                "partial": last.get("partial", False), "warnings": last.get("warnings", []), "errorKind": last.get("errorKind")}
    return {**base, **mine, "source": "button" if mine["started_at"] else None, "runSource": "button" if mine["started_at"] else None}


def _run_sync() -> None:
    cmd = C.module_cmd("eclass", "sync", "--source", "button", "--log", str(C.LOG_FILE))
    try:
        proc = subprocess.run(cmd, cwd=str(C.ROOT), env=_env(), capture_output=True, timeout=SYNC_TIMEOUT,
                              creationflags=NO_WINDOW)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        code = C.EXIT_TIMEOUT
    except Exception:                                # noqa: BLE001
        code = C.EXIT_LAUNCH
    with _lock:
        _sync.update(running=False, finished_at=now_iso(), exit_code=code)


def start_sync() -> dict:
    """수집을 백그라운드로 띄운다. 이미 도는 중이면(이 서버·예약 작업·잠금 없는 프로세스) 띄우지 않고
    already_running 을 붙여 상태만 돌려준다 (F6-R52)."""
    if p := problem():
        return {**sync_state(), "error": p}
    st = sync_state()
    if st["running"]:
        return {**st, "already_running": True}
    if pids := _sync_processes():
        return {**st, "already_running": True,
                "error": f"e클래스 수집이 이미 돌고 있습니다 (pid {pids[0]}, 잠금 파일 없음) — 끝난 뒤 다시 누르세요."}
    with _lock:
        if _sync["running"]:
            return {**sync_state(), "already_running": True}
        _sync.update(running=True, started_at=now_iso(), finished_at=None, exit_code=None)
    C.ensure_dirs()
    threading.Thread(target=_run_sync, daemon=True).start()
    return sync_state()


def last_log_lines(n: int = 14) -> list[str]:
    try:
        lines = C.LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [l for l in lines if l.strip()][-n:]


# ---------------------------------------------------------------- 로그인 창 (F6-S10)

def login_state() -> dict:
    with _lock:
        return dict(_login)


def _run_login(then_sync: bool) -> None:
    cmd = C.module_cmd("login")
    log = C.STATE_DIR / "login.log"
    try:
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"======== {now_iso()} 로그인 창 ========\n")
            f.flush()
            code = subprocess.run(cmd, cwd=str(C.C3_AGENT_DIR), env=_env(), stdin=subprocess.DEVNULL, stdout=f,
                                  stderr=subprocess.STDOUT, timeout=LOGIN_TIMEOUT).returncode
    except subprocess.TimeoutExpired:
        code = C.EXIT_TIMEOUT
    except Exception:                                # noqa: BLE001
        code = C.EXIT_LAUNCH
    ok = code == 0
    err = None if ok else {1: "로그인 창이 닫혔습니다", 2: "시간 안에 로그인되지 않았습니다",
                           C.EXIT_TIMEOUT: "시간이 초과되었습니다"}.get(code, f"로그인 창을 열지 못했습니다 (코드 {code})")
    with _lock:
        _login.update(running=False, finished_at=now_iso(), exit_code=code, ok=ok, error=err)
    if ok and then_sync:
        start_sync()


def start_login(then_sync: bool = True) -> dict:
    """C3 로그인 창을 띄운다 (이 PC 화면에 브라우저 창). 로그인되면 저절로 닫히고, then_sync 면 바로 수집한다."""
    if p := problem():
        return {**login_state(), "error": p}
    with _lock:
        if _login["running"]:
            return {**_login, "already_running": True}
        _login.update(running=True, started_at=now_iso(), finished_at=None, exit_code=None, ok=None, error=None)
    C.ensure_dirs()
    threading.Thread(target=_run_login, args=(then_sync,), daemon=True).start()
    return login_state()


# ---------------------------------------------------------------- 예약 실행 (F6-R10 · R18)
# Windows 작업 스케줄러(register-task.ps1) / 맥 launchd(C0 osenv.launchd). 둘 다 정각 N시간마다 + 로그인 시 `-m eclass tick`.

def tasks_enabled() -> bool:
    return sys.platform in ("win32", "darwin") and os.environ.get("F6_TASKS", "").lower() not in ("off", "0", "false", "no")


# launchd plist 에 넘길 환경변수 — 격리 서버·테스트가 바꾼 폴더를 예약 실행도 그대로 쓰게 (전체 환경을 plist 에 적지는 않는다)
_PASS_ENV = ("C0_AGENT_DIR", "C3_AGENT_DIR", "C3_STATE_DIR", "F6_DATA_DIR", "F6_STATE_DIR")


def _task_args() -> list[str]:
    """작업이 부를 명령 (register-task.ps1 -Command) — 묶인 앱이면 앱 실행 파일 --run-module eclass tick.
    작업은 환경변수 없이 돈다 — 앱 실행 파일이 --run-module 일 때 앱 데이터 폴더를 스스로 잡는다 (desktop.py)."""
    return ["-Command", C.task_command("eclass", "tick", "--log", str(C.LOG_FILE))]


def _launchd_info() -> dict:
    from osenv import launchd                       # C0 — config 가 sys.path 에 붙여 두었다
    from . import schedule
    info = {**launchd.info(C.LAUNCHD_LABEL, C.ROOT), "legacy": []}
    if info.get("registered"):
        info["nextRun"] = schedule.next_slot(datetime.now(), R.settings()["intervalHours"]).isoformat(timespec="seconds")
        last = next((r for r in R.list_runs(50) if r.get("source") in ("schedule", "catchup", "retry")), None)
        info["lastRun"] = last.get("started_at") if last else None
    return info


def _launchd_register(interval: int) -> dict:
    from osenv import launchd
    env = {k: os.environ[k] for k in _PASS_ENV if os.environ.get(k)}
    env["PLAYWRIGHT_BROWSERS_PATH"] = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or str(C.C3_BROWSERS)
    C.ensure_dirs()
    return launchd.register(C.LAUNCHD_LABEL, C.module_cmd("eclass", "tick", "--log", str(C.LOG_FILE)),
                            workdir=C.ROOT, times=[(h, 0) for h in range(0, 24, interval)], env=env,
                            log=C.STATE_DIR / "launchd.log")


def task_info(force: bool = False) -> dict:
    """예약 작업 상태 {available, registered, name, state, nextRun, lastRun, lastResult, pathOk, legacy}. PowerShell 이라
    1~2초 걸려서 1분 동안 기억한다."""
    if not tasks_enabled():
        return {"available": False, "registered": False}
    if not force and _task_cache["value"] is not None and time.time() - _task_cache["at"] < 60:
        return _task_cache["value"]
    if sys.platform != "win32":
        info = _launchd_info()
        _task_cache.update(at=time.time(), value=info)
        return info
    names = [C.TASK_NAME, *C.LEGACY_TASK_NAMES]
    script = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
              "$out = @(); foreach ($n in @(" + ",".join(f"'{n}'" for n in names) + ")) { "
              "$t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue; if ($t) { "
              "$i = Get-ScheduledTaskInfo -TaskName $n; "
              "$out += [pscustomobject]@{ name = $n; state = [string]$t.State; "
              "args = (($t.Actions | ForEach-Object { \"$($_.Execute) $($_.Arguments)\" }) -join ' | '); "
              "next = if ($i.NextRunTime) { $i.NextRunTime.ToString('s') } else { $null }; "
              "last = if ($i.LastRunTime -and $i.LastRunTime.Year -gt 2000) { $i.LastRunTime.ToString('s') } else { $null }; "
              "result = $i.LastTaskResult } } }; ConvertTo-Json -InputObject $out -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                             timeout=20, creationflags=NO_WINDOW)
        found = json.loads(out.stdout.decode("utf-8", "replace") or "[]")
    except Exception as e:                           # noqa: BLE001
        return {"available": False, "registered": False, "error": f"작업 스케줄러를 읽지 못했습니다: {e}"[:200]}
    found = found if isinstance(found, list) else [found]
    root = sys.executable.lower()                                     # 작업이 지금 이 실행 파일(앱 / 개발 venv python)을 부르는가
    info: dict[str, Any] = {"available": True, "registered": False, "name": C.TASK_NAME, "legacy": []}
    for t in found:
        path_ok = root in str(t.get("args") or "").lower()
        if t.get("name") == C.TASK_NAME:
            info.update(registered=True, state=t.get("state"), nextRun=t.get("next"), lastRun=t.get("last"),
                        lastResult=t.get("result"), pathOk=path_ok)
        else:
            info["legacy"].append({"name": t.get("name"), "state": t.get("state"), "pathOk": path_ok})
    _task_cache.update(at=time.time(), value=info)
    return info


def register_task(interval: int) -> dict:
    """register-task.ps1 로 (다시) 등록 — 주기를 바꾸면 부른다. 예전 eclass_agent 작업이 있으면 스크립트가 지운다."""
    if not tasks_enabled():
        return {"ok": False, "error": "이 서버에서는 예약 실행을 쓰지 않습니다 (F6_TASKS=off)"}
    if sys.platform != "win32":
        res = _launchd_register(interval)
        _task_cache.update(at=0.0, value=None)
        return res
    script = C.ROOT / "register-task.ps1"
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
           "-IntervalHours", str(interval), *_task_args()]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=60, creationflags=NO_WINDOW)
        text = (p.stdout + p.stderr).decode("utf-8", "replace").strip()
        ok = p.returncode == 0
    except Exception as e:                           # noqa: BLE001
        ok, text = False, str(e)
    _task_cache.update(at=0.0, value=None)
    return {"ok": ok, "output": text[-800:], "error": None if ok else "작업 스케줄러에 등록하지 못했습니다"}


def unregister_task() -> dict:
    if not tasks_enabled():
        return {"ok": False, "error": "이 서버에서는 예약 실행을 쓰지 않습니다 (F6_TASKS=off)"}
    if sys.platform != "win32":
        from osenv import launchd
        res = launchd.unregister(C.LAUNCHD_LABEL)
        _task_cache.update(at=0.0, value=None)
        return res
    script = C.ROOT / "register-task.ps1"
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Remove"]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=60, creationflags=NO_WINDOW)
        ok = p.returncode == 0
        text = (p.stdout + p.stderr).decode("utf-8", "replace").strip()
    except Exception as e:                           # noqa: BLE001
        ok, text = False, str(e)
    _task_cache.update(at=0.0, value=None)
    return {"ok": ok, "output": text[-800:], "error": None if ok else "작업을 지우지 못했습니다"}
