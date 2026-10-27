"""데스크톱 사이드카(desktop.py) — 준비 줄 · 세션 쿠키 · 한 번 쓰는 실행 코드 · 앱 데이터 폴더 · stdin 닫으면 종료.
실제로 서버를 띄운다 (임시 데이터 폴더·빈 포트, 학교 사이트 접속 없음). 개발 venv(desktop/sidecar/.venv — fastapi·uvicorn) 로:
    univ_us_local/backend/.venv/bin/python -m pytest univ_us_local/backend/tests -q
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("uvicorn")
BACKEND = Path(__file__).resolve().parent.parent


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_open = urllib.request.build_opener(_NoRedirect).open


@pytest.fixture
def sidecar(tmp_path):
    # 예약 실행·06·18시 자동 수집은 끈다 — 켜 두면 /api/status 가 장학(F11)·대외활동(F12) 수집을 띄워 학교 사이트에 접속한다
    env = {**__import__("os").environ, "F1_TASKS": "off", "F6_TASKS": "off", "F11_AUTOSYNC": "off", "F12_AUTOSYNC": "off",
           "PYTHONUTF8": "1"}
    p = subprocess.Popen([sys.executable, str(BACKEND / "desktop.py"), "--data-root", str(tmp_path), "--exit-with-stdin",
                          "--no-browser-install", "--log", str(tmp_path / "logs" / "backend.log")],
                         cwd=str(BACKEND), stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8", env=env)
    ready = None
    for line in p.stdout:
        if line.startswith("UNIVUS_READY "):
            ready = json.loads(line[len("UNIVUS_READY "):])
            break
    assert ready, (tmp_path / "logs" / "backend.log").read_text(encoding="utf-8", errors="replace")
    yield p, ready, tmp_path
    if p.poll() is None:
        p.kill()


def _get(base, path, cookie=None):
    req = urllib.request.Request(base + path, headers={"Cookie": cookie} if cookie else {})
    try:
        with _open(req, timeout=10) as r:
            return r.status, r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.headers


def test_session_flow(sidecar):
    p, ready, root = sidecar
    base = f"http://127.0.0.1:{ready['port']}"
    launch = ready["launchUrl"][len(base):]
    assert _get(base, "/api/status")[0] == 403                     # 다른 프로그램·브라우저 탭
    status, headers = _get(base, launch)
    assert status == 303
    cookie = headers["Set-Cookie"]
    samesite = "lax" if sys.platform == "darwin" else "strict"     # 맥 WKWebView 는 Strict 면 첫 화면이 403 (main.desktop_launch)
    assert "HttpOnly" in cookie and f"samesite={samesite}" in cookie.lower()
    cv = cookie.split(";")[0]
    assert _get(base, "/api/status", cv)[0] == 200
    assert _get(base, launch)[0] == 403                            # 실행 코드는 한 번만
    assert _get(base, "/api/status", "univus_session=x")[0] == 403
    assert (root / "C1_Calendar_agent" / "data" / "univus.db").exists()   # 저장소가 아니라 앱 데이터 폴더에
    p.stdin.close()                                                # 앱이 끝나면
    assert p.wait(timeout=20) == 0
