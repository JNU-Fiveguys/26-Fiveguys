"""OpenAI API contract regressions. All HTTP requests go to the local fake server."""
import json

import pytest

from assistant import agent, config as C, llm, settings as S


@pytest.fixture
def responses_llm(fake_llm, monkeypatch):
    monkeypatch.setattr(C, "LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(C, "LLM_MODEL", "gpt-6.1-terra")
    monkeypatch.setattr(C, "OPENAI_MAX_OUTPUT_TOKENS", 4096)
    post = llm._post

    def local_only(url, key, body, timeout):
        assert url == "https://api.openai.com/v1/responses"
        assert key == "test-key"
        return post(fake_llm.url + "/responses", key, body, timeout)

    monkeypatch.setattr(llm, "_post", local_only)
    return fake_llm


def _text(text, phase="final_answer"):
    return {"type": "message", "id": "msg_test", "role": "assistant", "status": "completed",
            "phase": phase, "content": [{"type": "output_text", "text": text, "annotations": []}]}


def _call(name="ping", call_id="call_test", arguments="{}"):
    return {"type": "function_call", "id": "fc_" + call_id, "call_id": call_id,
            "name": name, "arguments": arguments, "status": "completed"}


def _complete(output):
    return {"type": "response.completed", "response": {"status": "completed", "output": output}}


@pytest.mark.parametrize("model", ["gpt-6.1-terra", "gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna",
                                    "gpt-4.1-mini", "gpt-5-mini", "o3-mini", "future-model-test"])
def test_models_share_responses_probe_without_model_specific_options(responses_llm, monkeypatch, model):
    monkeypatch.setattr(C, "LLM_MODEL", model)
    responses_llm.reply = {"status": "completed", "output": [_call()]}
    assert llm.probe() == {"ok": True, "tools": True, "error": None}
    request = responses_llm.requests[0]
    assert request["path"] == "/v1/responses"
    body = request["body"]
    assert body["model"] == model and body["max_output_tokens"] == 4096
    assert body["store"] is False
    assert not ({"max_tokens", "max_completion_tokens", "temperature", "reasoning", "messages"} & body.keys())
    assert body["tools"][0]["name"] == "ping" and body["tools"][0]["strict"] is False
    assert "function" not in body["tools"][0]


def test_responses_streaming_text(responses_llm):
    responses_llm.reply = [
        {"type": "response.output_text.delta", "delta": "안녕"},
        {"type": "response.output_text.delta", "delta": "하세요."},
        _complete([_text("안녕하세요.")]),
    ]
    events = list(llm.stream([{"role": "user", "content": "안녕"}]))
    assert events[:2] == [("delta", "안녕"), ("delta", "하세요.")]
    assert events[-1][1]["content"] == "안녕하세요."
    assert responses_llm.requests[0]["body"]["stream"] is True


def test_agent_tool_roundtrip_preserves_encrypted_state_and_phase(responses_llm, monkeypatch):
    monkeypatch.setattr(agent, "_TOOLS", {})
    executed = []

    @agent.tool("lookup", "일정을 확인한다", {"type": "object", "properties": {"day": {"type": "string"}}})
    def lookup(args, ctx):
        executed.append(args)
        return {"content": {"start": "11:00"}}

    output = [
        {"type": "reasoning", "id": "rs_test", "summary": [], "encrypted_content": "opaque-test-state"},
        _text("확인하겠습니다.", "commentary"),
        _call("lookup", "call_first", '{"day":"today"}'),
        _call("lookup", "call_second", '{"day":"tomorrow"}'),
    ]
    responses_llm.replies = [
        [{"type": "response.output_text.delta", "delta": "확인하겠습니다."}, _complete(output)],
        [{"type": "response.output_text.delta", "delta": "11시에 시작합니다."}, _complete([_text("11시에 시작합니다.")])],
    ]
    events = list(agent.run([{"role": "user", "content": "오늘과 내일 일정 알려줘"}]))
    assert executed == [{"day": "today"}, {"day": "tomorrow"}]
    assert ("delta", "11시에 시작합니다.") in events and events[-1] == ("done", None)
    body = responses_llm.requests[1]["body"]
    assert body["input"][2:6] == output
    assert [i["call_id"] for i in body["input"] if i.get("type") == "function_call_output"] == ["call_first", "call_second"]
    assert json.loads(body["input"][-1]["output"]) == {"start": "11:00"}
    assert "opaque-test-state" not in json.dumps(events)


def test_interrupted_tool_stream_never_runs_partial_call(responses_llm, monkeypatch):
    monkeypatch.setattr(agent, "_TOOLS", {})
    executed = []

    @agent.tool("write", "테스트 쓰기")
    def write(args, ctx):
        executed.append(args)
        return {"content": "done"}

    responses_llm.reply = [
        {"type": "response.output_item.added", "item": _call("write", arguments="")},
        {"type": "response.function_call_arguments.delta", "delta": '{"incomplete":'},
    ]
    events = list(agent.run([{"role": "user", "content": "테스트"}]))
    assert not executed and any(kind == "error" for kind, _ in events)
    assert len(responses_llm.requests) == 1


@pytest.mark.parametrize("reply", [
    [{"type": "error", "message": "provider failure"}],
    [{"type": "response.failed", "response": {"status": "failed", "error": {"message": "failure"}}}],
    [{"type": "response.incomplete", "response": {"status": "incomplete", "output": []}}],
    [_complete([])],
])
def test_response_errors_do_not_become_empty_success(responses_llm, reply):
    responses_llm.reply = reply
    with pytest.raises(llm.LLMError):
        list(llm.stream([{"role": "user", "content": "테스트"}]))


def test_other_providers_keep_correct_contracts():
    messages = [{"role": "user", "content": "테스트"}]
    for base in ("https://generativelanguage.googleapis.com/v1beta/openai", "http://localhost:11434/v1",
                 "https://gateway.example.test/v1", "https://api.openai.com.example.test/v1"):
        slot = S.Slot(base, "test", "gpt-6.1-terra", "test")
        assert not llm._uses_responses(slot)
        _, body = llm._prepare(slot, messages, None, 0.2, 100)
        assert body["max_tokens"] == 100 and body["temperature"] == 0.2 and "input" not in body
        assert llm._prepare(slot, messages, None, 0.2, None)[1]["max_tokens"] == 800


def test_configurable_budget_and_explicit_call_limit(responses_llm, monkeypatch):
    monkeypatch.setattr(C, "OPENAI_MAX_OUTPUT_TOKENS", 2048)
    responses_llm.reply = {"status": "completed", "output": [_text("연결됨")]}
    llm.chat([{"role": "user", "content": "테스트"}])
    llm.chat([{"role": "user", "content": "테스트"}], max_tokens=128)
    assert [r["body"]["max_output_tokens"] for r in responses_llm.requests] == [2048, 128]


def test_probe_text_fallback_keeps_reasoning_budget(responses_llm):
    responses_llm.replies = [
        (400, {"error": {"message": "tools not supported"}}),
        {"status": "completed", "output": [_text("연결됨")]},
    ]
    assert llm.probe() == {"ok": True, "tools": False, "error": None}
    assert [r["body"]["max_output_tokens"] for r in responses_llm.requests] == [4096, 4096]
    assert "tools" not in responses_llm.requests[1]["body"]


@pytest.mark.parametrize("reply", [
    {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output": []},
    {"status": "in_progress", "output": [_call()]},
    {"status": "completed", "output": [{**_call(), "status": "incomplete"}]},
    {"status": "completed", "output": []},
    (404, {"error": {"message": "model not found"}}),
])
def test_probe_reports_failures_without_switching_models(responses_llm, reply):
    responses_llm.reply = reply
    result = llm.probe()
    assert result["ok"] is False and result["error"]
    assert all(r["body"]["model"] == "gpt-6.1-terra" for r in responses_llm.requests)


def test_saved_settings_probe_through_http_api(responses_llm):
    from fastapi.testclient import TestClient
    from assistant.api import app

    responses_llm.reply = {"status": "completed", "output": [_call()]}
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        saved = client.put("/v1/settings/llm", json={"provider": "openai", "baseUrl": "https://api.openai.com/v1",
                                                   "apiKey": "test-key", "model": "gpt-6.1-sol"})
        assert saved.status_code == 200
        result = client.post("/v1/settings/llm/test")
        assert result.status_code == 200 and result.json() == {"ok": True, "tools": True, "error": None}
    assert responses_llm.requests[0]["body"]["model"] == "gpt-6.1-sol"
