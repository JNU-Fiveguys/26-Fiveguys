"""연결 설정 — 저장·보기·우선순위 · 키는 OS 저장소로만 · 주소를 바꾸면 예전 키를 버린다."""
import json

import pytest

from assistant import config as C
from assistant import settings as S


def test_empty():
    assert S.slot() == S.Slot("", "", "", "none") and not S.slot().ready
    v = S.view()
    assert not v["configured"] and not v["hasKey"] and v["keyHint"] == "" and v["providers"]


def test_save_keeps_key_out_of_file_and_view(isolated):
    v = S.save("gemini", "https://example.test/v1/", "m-1", "sk-secret-1234")
    assert S.slot() == S.Slot("https://example.test/v1", "sk-secret-1234", "m-1", "settings")
    assert isolated["key"] == "sk-secret-1234"
    saved = (C.DATA_DIR / "settings.json").read_text(encoding="utf-8")
    assert "sk-secret" not in saved and json.loads(saved) == {"provider": "gemini", "baseUrl": "https://example.test/v1", "model": "m-1"}
    assert v["configured"] and v["hasKey"] and v["keyHint"] == "…1234" and "sk-secret" not in json.dumps(v)


def test_model_change_keeps_key_but_url_change_drops_it(isolated):
    S.save("custom", "https://a.test/v1", "m-1", "key-for-a-0001")
    S.save("custom", "https://a.test/v1", "m-2")                    # 모델만 바꿈 — 키 그대로
    assert S.slot().api_key == "key-for-a-0001" and S.slot().model == "m-2"
    with pytest.raises(S.Invalid):                                   # 주소가 바뀌었는데 키가 없다 — 예전 키를 새 주소로 보내지 않는다
        S.save("custom", "https://b.test/v1", "m-2")
    assert "key" not in isolated
    S.save("custom", "https://b.test/v1", "m-2", "key-for-b-0002")
    assert S.slot().api_key == "key-for-b-0002"


def test_validation():
    for url in ("", "ftp://x.test", "example.test/v1", "http://remote.test/v1"):    # 바깥 주소는 https 만
        with pytest.raises(S.Invalid):
            S.save("custom", url, "m", "k-12345678")
    with pytest.raises(S.Invalid):
        S.save("custom", "https://a.test/v1", "  ", "k-12345678")
    with pytest.raises(S.Invalid):
        S.save("custom", "https://a.test/v1", "m")                   # 키 없음


def test_local_needs_no_key():
    S.save("ollama", "http://localhost:11434/v1", "local-model")
    assert S.slot().ready and S.slot().api_key == "local" and S.view()["local"]


def test_env_fallback_and_settings_win(monkeypatch):
    monkeypatch.setattr(C, "LLM_BASE_URL", "https://env.test/v1")
    monkeypatch.setattr(C, "LLM_API_KEY", "env-key")
    monkeypatch.setattr(C, "LLM_MODEL", "env-model")
    assert S.slot() == S.Slot("https://env.test/v1", "env-key", "env-model", "env")
    assert S.view()["keyHint"] == ""                                 # .env 키는 끝자리도 보여 주지 않는다
    S.save("custom", "https://ui.test/v1", "ui-model", "ui-key-123456")
    assert S.slot().source == "settings" and S.slot().base_url == "https://ui.test/v1"


def test_clear(isolated):
    S.save("custom", "https://a.test/v1", "m", "k-12345678")
    S.clear()
    assert "key" not in isolated and S.slot().source == "none"
