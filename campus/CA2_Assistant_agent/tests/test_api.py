"""로컬 API — 이 PC 요청만 · 설정 저장과 연결 시험 · 대화 SSE · 개인정보 경고 (가짜 LLM)."""
import json

import pytest
from fastapi.testclient import TestClient

from assistant import privacy
from assistant.api import app

LOCAL = "http://127.0.0.1:8765"


@pytest.fixture
def client():
    return TestClient(app, base_url=LOCAL)


def _events(text):
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_other_sites_are_blocked(client):
    assert client.get("/v1/settings/llm").status_code == 200
    assert client.get("/v1/settings/llm", headers={"Host": "evil.test"}).status_code == 403         # DNS 바꿔치기
    assert client.put("/v1/settings/llm", json={"baseUrl": "https://evil.test/v1", "model": "m", "apiKey": "k"},
                      headers={"Origin": "https://evil.test"}).status_code == 403
    assert client.get("/").status_code == 200


def test_settings_roundtrip_never_returns_key(client, fake_llm, isolated):
    r = client.put("/v1/settings/llm", json={"provider": "custom", "baseUrl": fake_llm.url, "model": "m-1", "apiKey": "sk-secret-9876"})
    assert r.status_code == 200 and r.json()["configured"] and r.json()["keyHint"] == "…9876" and "sk-secret" not in r.text
    assert "sk-secret" not in client.get("/v1/settings/llm").text
    assert client.put("/v1/settings/llm", json={"baseUrl": "http://remote.test/v1", "model": "m", "apiKey": "k"}).status_code == 400
    assert client.delete("/v1/settings/llm").json()["source"] != "settings" and "key" not in isolated   # 가짜 서버의 .env 슬롯만 남는다


def test_models_and_probe(client, fake_llm):
    fake_llm.reply = {"data": [{"id": "models/b"}, {"id": "a"}]}
    r = client.post("/v1/settings/llm/models", json={"baseUrl": fake_llm.url, "apiKey": "typed-key"})
    assert r.json() == {"models": ["a", "b"]} and fake_llm.requests[-1]["auth"] == "Bearer typed-key"
    call = {"id": "c", "type": "function", "function": {"name": "ping", "arguments": "{}"}}
    fake_llm.reply = {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [call]}}]}
    assert client.post("/v1/settings/llm/test").json() == {"ok": True, "tools": True, "error": None}
    fake_llm.reply = {"choices": [{"message": {"role": "assistant", "content": "pong"}}]}
    assert client.post("/v1/settings/llm/test").json()["tools"] is False
    fake_llm.reply = 401
    assert client.post("/v1/settings/llm/test").json() == {"ok": False, "tools": False, "error": "키가 맞지 않습니다 (401)"}


def test_saved_key_is_not_sent_to_another_address(client, fake_llm):
    client.put("/v1/settings/llm", json={"baseUrl": "https://saved.test/v1", "model": "m", "apiKey": "saved-key-0001"})
    r = client.post("/v1/settings/llm/models", json={"baseUrl": "https://other.test/v1"})
    assert r.status_code == 400 and not fake_llm.requests


def test_probe_without_settings(client):
    assert client.post("/v1/settings/llm/test").json()["ok"] is False


def test_chat_simple_mode_stream(client):
    r = client.post("/v1/agent/chat", json={"messages": [{"role": "user", "content": "프린터 어디 있어?"}]})
    ev = _events(r.text)
    assert r.headers["content-type"].startswith("text/event-stream") and ev[0] == ("mode", "simple") and ev[-1] == ("done", None)


def test_chat_with_llm_and_pii_warning(client, fake_llm):
    fake_llm.reply = [{"choices": [{"delta": {"content": "안녕하세요."}}]}, "[DONE]"]
    ev = _events(client.post("/v1/agent/chat", json={"messages": [{"role": "user", "content": "안녕"}]}).text)
    assert ev == [("delta", "안녕하세요."), ("done", None)]
    n = len(fake_llm.requests)
    body = {"messages": [{"role": "user", "content": "내 학번은 201234567 이야"}]}
    ev = _events(client.post("/v1/agent/chat", json=body).text)
    assert ev[0][0] == "warn" and ev[0][1]["kinds"] == ["학번"] and len(fake_llm.requests) == n      # 묻기 전에는 보내지 않는다
    ev = _events(client.post("/v1/agent/chat", json={**body, "confirmPii": True}).text)
    assert ev[0] == ("delta", "안녕하세요.") and len(fake_llm.requests) == n + 1


def test_privacy_patterns():
    assert privacy.find("주민번호 990101-1234567") == ["주민등록번호"]
    assert privacy.find("비밀번호는 abcd1234 야") == ["비밀번호"]
    assert privacy.find("도서관 몇 시까지 해? 3,000원 09:00") == []


def test_kb_search(client):
    assert client.get("/v1/kb/search", params={"q": "학생증 재발급"}).json()["results"][0]["section"] == "재발급"
