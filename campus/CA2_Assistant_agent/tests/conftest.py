"""테스트 공통 — 외부 LLM 에 접속하지 않는다. 이 PC 안에 띄운 가짜 OpenAI 호환 서버(fake_llm)로만 시험한다.

내 .env 의 키가 테스트에 섞이지 않게, config 를 import 하기 전에 슬롯을 비워 둔다(_load_env 는 이미 있는 값을 건드리지 않는다).
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

for _k in ("LLM_MAIN_BASE_URL", "LLM_MAIN_API_KEY", "LLM_MAIN_MODEL", "LLM_EMBED_BASE_URL", "LLM_EMBED_API_KEY", "LLM_EMBED_MODEL"):
    os.environ[_k] = ""
os.environ["CA2_DATA_DIR"] = tempfile.mkdtemp(prefix="ca2test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant import config as C  # noqa: E402
from assistant import settings as S  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """테스트마다 빈 설정 폴더 + 메모리 키 저장소 — 내 PC 의 키체인과 저장된 설정을 건드리지 않는다."""
    store: dict = {}
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    monkeypatch.setattr(C, "KNOWLEDGE_DIRS", (C.ROOT / "tests" / "fixtures",))
    monkeypatch.setattr(S, "_key_get", lambda: store.get("key"))
    monkeypatch.setattr(S, "_key_set", lambda v: store.__setitem__("key", v))
    monkeypatch.setattr(S, "_key_delete", lambda: store.pop("key", None))
    return store


class FakeLLM:
    """요청을 기록하고, 테스트가 정해 둔 응답을 돌려준다. reply = dict(JSON 응답) · list(SSE data 줄) · int(오류 코드).
    replies 에 여러 개를 넣어 두면 요청마다 앞에서부터 하나씩 쓴다(다 쓰면 reply)."""

    def __init__(self):
        self.requests: list[dict] = []
        self.reply = None
        self.replies: list = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
                reply = fake.replies.pop(0) if fake.replies else fake.reply
                if isinstance(reply, tuple):               # (오류 코드, 본문)
                    raw = json.dumps(reply[1]).encode("utf-8")
                    self.send_response(reply[0])
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                elif isinstance(reply, int):
                    self.send_response(reply)
                    self.end_headers()
                elif isinstance(reply, list):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    for item in reply:
                        data = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
                        self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                else:
                    raw = json.dumps(reply, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)

            def do_GET(self):
                fake.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": None})
                raw = json.dumps(fake.reply).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def fake_llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(C, "LLM_BASE_URL", fake.url)
    monkeypatch.setattr(C, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(C, "LLM_MODEL", "test-model")
    monkeypatch.setattr(C, "EMBED_BASE_URL", fake.url)
    monkeypatch.setattr(C, "EMBED_API_KEY", "test-key")
    monkeypatch.setattr(C, "EMBED_MODEL", "test-embed")
    monkeypatch.setattr(C, "LLM_TIMEOUT", 5.0)
    yield fake
    fake.server.shutdown()
    fake.server.server_close()
