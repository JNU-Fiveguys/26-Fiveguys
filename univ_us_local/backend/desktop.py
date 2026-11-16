"""데스크톱 앱 사이드카 — Tauri 창이 띄우는 로컬 서버. PyInstaller 로 묶으면 univus-backend 실행 파일 하나가 된다.

    desktop.py [--data-root DIR] [--port N] [--log FILE] [--exit-with-stdin] [--no-browser-install] [--dev] [--frontend-url URL]
        앱 데이터 폴더를 잡고(C0 osenv.appdata), 빈 포트에서 서버를 띄운 뒤 준비되면 표준출력에 한 줄을 쓴다:
            UNIVUS_READY {"port": 51234, "launchUrl": "http://127.0.0.1:51234/desktop/launch?code=…", "dataRoot": "…"}
        Tauri 는 이 줄을 읽어 창을 launchUrl 로 연다 → 서버가 한 번 쓰는 코드를 세션 쿠키로 바꿔 준다(app/main.py).
        --exit-with-stdin: 표준입력이 닫히면(앱이 끝나거나 죽으면) 서버도 끝낸다 — 고아 프로세스 방지.
        처음 실행이면 Playwright Chromium 을 앱 데이터 폴더에 백그라운드로 받는다(약 150MB, logs/browsers.log).

    desktop.py --self-check
        기능 폴더의 모든 모듈·백엔드가 import 되는지 점검 (build.py 가 묶은 실행 파일로 부른다 — PyInstaller 가 놓친 모듈 찾기).

    desktop.py --run-module <모듈> [인자…]
        `python -m <모듈>` 과 같다(현재 폴더를 import 경로 맨 앞에). 기능별 .venv 가 없어서
        수집기·로그인 창·예약 실행이 이 실행 파일을 다시 부른다 (osenv.module_cmd · register-task.ps1 -Command · launchd).

개발 모드(`desktop/` 에서 npm run dev = tauri dev)는 Tauri 디버그 빌드가 이 파일을 desktop/sidecar/.venv 의 python 으로
    desktop.py --dev --data-root desktop/.dev-data --port 8020 --frontend-url http://127.0.0.1:3000
처럼 띄운다 — 화면은 next dev(즉시 반영), 예약 실행은 꺼짐, 데이터는 설치된 앱과 따로 (desktop/README.md).
묶인 앱의 파일 배치는 저장소와 같은 모양이다 — <번들>/univus/{C0…F6 기능 폴더, univ_us_local/backend/app, …/frontend/out}
(desktop/sidecar/univus-backend.spec). 그래서 기능 config 의 PROJECT_ROOT 기본값이 그대로 맞는다.
"""
from __future__ import annotations

import argparse
import json
import os
import runpy
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

FROZEN = bool(getattr(sys, "frozen", False))
# 저장소(개발) = 이 파일에서 두 단계 위 / 묶인 앱 = PyInstaller 압축 해제 폴더 안의 univus/
BUNDLE = Path(getattr(sys, "_MEIPASS", "")) / "univus" if FROZEN else Path(__file__).resolve().parents[2]
BACKEND_DIR = BUNDLE / "univ_us_local" / "backend"
C0_DIR = BUNDLE / "C0_Platform_agent"
READY_PREFIX = "UNIVUS_READY "

if str(C0_DIR) not in sys.path:
    sys.path.append(str(C0_DIR))
from osenv import INSTALLING_MARK, appdata, chromium_state, module_cmd  # noqa: E402


def run_module(module: str, args: list[str]) -> int:
    """`python -m module args` 흉내. 예약 실행은 환경변수 없이 오므로 묶인 앱이면 앱 데이터 폴더를 스스로 잡는다."""
    if FROZEN:
        appdata.apply(appdata.default_root())
    sys.path.insert(0, os.getcwd())
    sys.argv = [module, *args]
    try:
        runpy.run_module(module, run_name="__main__", alter_sys=True)
    except SystemExit as e:                              # 모듈이 sys.exit(n) 으로 끝내는 것이 보통이다
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    return 0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for(url: str, timeout: float = 120.0) -> None:
    """개발 모드: next dev 가 포트를 열 때까지 기다린다 (그동안 앱 창은 시작 화면)."""
    u = urlsplit(url)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with socket.create_connection((u.hostname or "127.0.0.1", u.port or 80), timeout=1):
                return
        except OSError:
            time.sleep(0.3)
    print(f"desktop: {url} 이(가) {timeout:.0f}초 안에 열리지 않았습니다 — 그대로 엽니다", file=sys.stderr, flush=True)


def _announce(server, port: int, code: str, root: Path, frontend: str | None = None) -> None:
    """서버가 소켓을 열면 Tauri 에 준비 줄을 알린다. frontend(개발 모드 next dev)가 있으면 창을 그쪽으로 보낸다 —
    next.config 의 rewrites 가 /desktop/launch·/api 를 이 서버로 넘기므로 세션 쿠키는 next dev 주소에 붙는다."""
    while not server.started and not server.should_exit:
        time.sleep(0.05)
    if server.started:
        base = frontend.rstrip("/") if frontend else f"http://127.0.0.1:{port}"
        if frontend:
            _wait_for(base)
        msg = {"port": port, "launchUrl": f"{base}/desktop/launch?code={code}", "dataRoot": str(root)}
        print(READY_PREFIX + json.dumps(msg, ensure_ascii=False), flush=True)


def _detach_stdin():
    """Tauri 가 붙인 표준입력 파이프를 이 프로세스만 갖고, 자식 프로세스에는 NUL 을 물려준다 — 복제한 파이프를 돌려준다.
    Windows 에서는 한 스레드가 동기 파이프를 읽고 있는 동안 그 핸들을 물려받은 자식 python 이 시작하며 핸들을 건드리면
    (GetFileType) 멈춘다 — 수집기·가져오기·Chromium 내려받기가 시작하자마자 멈추던 원인 (2026-10-08).
    subprocess 는 stdin 을 안 주면 GetStdHandle 을 물려주므로, fd 0 과 표준 핸들을 둘 다 NUL 로 바꾼다."""
    pipe = os.fdopen(os.dup(0), "rb", buffering=0)      # os.dup 은 상속되지 않는 핸들 (PEP 446)
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    if sys.platform == "win32":
        import ctypes
        import msvcrt
        ctypes.windll.kernel32.SetStdHandle(-10, msvcrt.get_osfhandle(0))   # STD_INPUT_HANDLE
    return pipe


def _exit_with_stdin(server, pipe) -> None:
    try:
        while pipe.read(1):
            pass
    except Exception:                                    # noqa: BLE001
        pass
    server.should_exit = True


def _ensure_browsers(root: Path) -> None:
    """첫 실행이면 Chromium 을 앱 데이터 폴더에 받는다 — 그동안 C3·C2·F2 는 '내려받는 중'이라고 답한다."""
    target = Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])
    have = target.is_dir() and any(p.name.startswith("chromium") for p in target.iterdir())
    if have and chromium_state(target) != "installing":  # 표시가 남아 있으면 지난 실행이 받다가 끊긴 것 — 다시 받는다
        return
    target.mkdir(parents=True, exist_ok=True)
    mark = target / INSTALLING_MARK
    mark.write_text(str(os.getpid()), encoding="utf-8")
    log = root / "logs" / "browsers.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(log, "a", encoding="utf-8") as f:
            subprocess.run(module_cmd("playwright", "install", "chromium"), stdout=f, stderr=subprocess.STDOUT,
                           cwd=str(root), timeout=30 * 60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:                               # noqa: BLE001
        with open(log, "a", encoding="utf-8") as f:
            print(f"Chromium 내려받기 실패: {e}", file=f)
    finally:
        mark.unlink(missing_ok=True)


def self_check() -> int:
    """묶인 실행 파일 점검 — 기능 폴더의 모든 파이썬 모듈과 백엔드(app)가 import 되는가 (build.py·CI 가 부른다).
    PyInstaller 가 놓친 모듈(2026-10-08 osenv.creds)은 그 기능을 실제로 쓸 때에야 터지므로 빌드 때 한 번 다 불러 본다.
    데이터는 임시 폴더, 예약 실행은 끈다."""
    import importlib
    import tempfile
    import traceback
    os.environ.update(F1_TASKS="off", F6_TASKS="off", F11_AUTOSYNC="off", F12_AUTOSYNC="off", UNIVUS_NO_BROWSER_INSTALL="1")
    appdata.apply(Path(tempfile.mkdtemp(prefix="univus-check-")))
    roots = [d for d in sorted(BUNDLE.iterdir()) if d.is_dir() and (d.name[:1] in "CF") and d.name.endswith("_agent")]
    roots.extend(d for d in sorted((BUNDLE / "campus").glob("CA*_agent")) if d.is_dir())
    roots.append(BACKEND_DIR)
    names: list[str] = []
    for root in roots:
        if str(root) not in sys.path:
            sys.path.append(str(root))
        for pkg in sorted(p for p in root.iterdir() if (p / "__init__.py").exists()):
            names += [f"{pkg.name}.{f.stem}" if f.stem != "__init__" else pkg.name
                      for f in sorted(pkg.rglob("*.py")) if f.parent == pkg and f.stem != "__main__"]
    failed = []
    for name in names:
        try:
            importlib.import_module(name)
        except Exception:                                # noqa: BLE001 — 하나라도 실패하면 모아서 알린다
            failed.append((name, traceback.format_exc(limit=1).strip().splitlines()[-1]))
    # Importing ssl alone does not read its certificate policy data. Exercise
    # context creation so a missing frozen-runtime PEM fails the build check.
    try:
        import ssl
        tls = ssl.create_default_context()
        if tls.verify_mode != ssl.CERT_REQUIRED or not tls.check_hostname:
            raise RuntimeError("HTTPS certificate verification must remain enabled")
    except Exception:
        failed.append(("ssl.create_default_context", traceback.format_exc(limit=1).strip().splitlines()[-1]))
    for name, err in failed:
        print(f"self-check 실패: {name}: {err}", file=sys.stderr)
    print(f"self-check: 모듈 {len(names)}개 · 실패 {len(failed)}", file=sys.stderr, flush=True)
    return 1 if failed else 0


def serve(a: argparse.Namespace) -> int:
    # 재시도한 첨부 경로는 resolve()를 거친다. 모든 기능에 같은 정규 경로를 전달한다.
    root = (Path(a.data_root) if a.data_root else appdata.default_root()).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if a.dev:
        # 개발 모드는 설치된 앱과 같은 작업 이름(UnivUs-F1-…·UnivUs-F6-…)을 쓰므로 예약 실행을 건드리지 않는다
        os.environ.setdefault("F1_TASKS", "off")
        os.environ.setdefault("F6_TASKS", "off")
        os.environ.setdefault("F11_AUTOSYNC", "off")     # 장학 06·18시 자동 수집도 개발 모드에서는 끈다 ('지금 수집'은 된다)
        os.environ.setdefault("F12_AUTOSYNC", "off")     # 대외활동도 같다
    # 묶인 앱은 Tauri 가 stderr 를 버리므로 기본으로 앱 데이터 폴더/logs/backend.log 에 쓴다
    log = Path(a.log) if a.log else (root / "logs" / "backend.log" if FROZEN else None)
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        sys.stderr = open(log, "a", encoding="utf-8", buffering=1)        # noqa: SIM115 — 프로세스 끝까지 쓴다
    # 개발 모드는 Chromium 을 데이터 폴더가 아니라 venv(desktop/sidecar/.venv/pw-browsers)에 둔다 — .dev-data 를 지워도 다시 받지 않게
    if not FROZEN:
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(Path(sys.prefix) / "pw-browsers"))
    appdata.apply(root)

    port = a.port or _free_port()
    code = secrets.token_urlsafe(18)
    os.environ.update(UNIVUS_PORT=str(port), UNIVUS_SESSION_TOKEN=secrets.token_urlsafe(32), UNIVUS_LAUNCH_CODE=code)
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))

    import uvicorn
    from app import main as app_main                     # config 가 세션 값을 읽고 환경변수에서 지운다

    server = uvicorn.Server(uvicorn.Config(app_main.app, host="127.0.0.1", port=port, log_level="warning",
                                           access_log=False))
    threading.Thread(target=_announce, args=(server, port, code, root, a.frontend_url), daemon=True).start()
    if a.exit_with_stdin:                                # 자식 프로세스를 띄우기 전에 — 아래 Chromium 내려받기도 자식이다
        threading.Thread(target=_exit_with_stdin, args=(server, _detach_stdin()), daemon=True).start()
    if not (a.no_browser_install or os.environ.get("UNIVUS_NO_BROWSER_INSTALL") == "1"):   # 환경변수: CI·시험 실행용
        threading.Thread(target=_ensure_browsers, args=(root,), daemon=True).start()
    print(f"desktop: 서버 시작 127.0.0.1:{port} · 데이터 {root}", file=sys.stderr, flush=True)
    server.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--run-module"]:
        if len(argv) < 2:
            print("사용법: --run-module <모듈> [인자…]", file=sys.stderr)
            return 2
        return run_module(argv[1], argv[2:])
    if argv[:1] == ["--self-check"]:
        return self_check()
    ap = argparse.ArgumentParser(prog="univus-backend", description="유니버스 데스크톱 앱 로컬 서버")
    ap.add_argument("--data-root", help="앱 데이터 폴더 (기본: OS 별 위치)")
    ap.add_argument("--port", type=int, default=0, help="포트 (기본: 빈 포트)")
    ap.add_argument("--log", help="서버 로그 파일")
    ap.add_argument("--exit-with-stdin", action="store_true", help="표준입력이 닫히면 끝낸다 (Tauri 가 붙인다)")
    ap.add_argument("--no-browser-install", action="store_true", help="Chromium 자동 내려받기를 하지 않는다")
    ap.add_argument("--dev", action="store_true", help="개발 모드 — 예약 실행(작업 스케줄러·launchd)을 끈다 (tauri dev 가 붙인다)")
    ap.add_argument("--frontend-url", help="앱 창이 열 화면 주소 (개발 모드: next dev, 예 http://127.0.0.1:3000)")
    return serve(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
