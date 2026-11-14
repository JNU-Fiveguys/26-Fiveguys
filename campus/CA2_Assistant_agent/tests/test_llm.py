"""LLM 호출 계층 — 요청 모양 · 도구 호출 · 스트리밍 조립 · 임베딩 · 실패 (가짜 서버, 외부 접속 없음)."""
import pytest

from assistant import llm

TOOLS = [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object", "properties": {}}}}]


def _delta(**d):
    return {"choices": [{"delta": d}]}


def test_not_configured():
    assert not llm.available() and not llm.embed_available()
    with pytest.raises(llm.LLMError):
        llm.chat([{"role": "user", "content": "안녕"}])
    with pytest.raises(llm.LLMError):
        list(llm.stream([{"role": "user", "content": "안녕"}]))
    with pytest.raises(llm.LLMError):
        llm.embed(["안녕"])


def test_chat_request_and_text(fake_llm):
    fake_llm.reply = {"choices": [{"message": {"role": "assistant", "content": "정보마루 1층입니다.", "tool_calls": []}}]}
    msg = llm.chat([{"role": "user", "content": "학생증 재발급 어디서 해?"}])
    assert msg == {"role": "assistant", "content": "정보마루 1층입니다."}     # 빈 tool_calls 는 뺀다
    req = fake_llm.requests[0]
    assert req["path"] == "/v1/chat/completions" and req["auth"] == "Bearer test-key"
    assert req["body"]["model"] == "test-model" and "tools" not in req["body"] and "stream" not in req["body"]


def test_chat_tool_call(fake_llm):
    call = {"id": "c1", "type": "function", "function": {"name": "get_weather", "arguments": '{"hours": 3}'}}
    fake_llm.reply = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [call]}}]}
    msg = llm.chat([{"role": "user", "content": "비 와?"}], tools=TOOLS)
    assert msg == {"role": "assistant", "content": "", "tool_calls": [call]}
    assert fake_llm.requests[0]["body"]["tools"] == TOOLS


def test_stream_text(fake_llm):
    fake_llm.reply = [_delta(role="assistant"), _delta(content="정보"), _delta(content="마루"), {"choices": []}, "[DONE]"]
    events = list(llm.stream([{"role": "user", "content": "어디?"}]))
    assert events == [("delta", "정보"), ("delta", "마루"), ("message", {"role": "assistant", "content": "정보마루"})]
    assert fake_llm.requests[0]["body"]["stream"] is True


def test_stream_assembles_tool_calls(fake_llm):
    fake_llm.reply = [
        _delta(tool_calls=[{"index": 0, "id": "c1", "function": {"name": "get_weather", "arguments": ""}}]),
        _delta(tool_calls=[{"index": 0, "function": {"arguments": '{"hou'}}]),
        _delta(tool_calls=[{"index": 1, "id": "c2", "function": {"name": "recommend_meal", "arguments": "{}"}}]),
        _delta(tool_calls=[{"index": 0, "function": {"arguments": 'rs": 3}'}}]),
        "[DONE]",
    ]
    events = list(llm.stream([{"role": "user", "content": "비 오면 가까운 데로 점심"}], tools=TOOLS))
    assert events == [("message", {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "get_weather", "arguments": '{"hours": 3}'}},
        {"id": "c2", "type": "function", "function": {"name": "recommend_meal", "arguments": "{}"}},
    ]})]


def test_embed_keeps_input_order(fake_llm):
    fake_llm.reply = {"data": [{"index": 1, "embedding": [0.0, 1.0]}, {"index": 0, "embedding": [1.0, 0.0]}]}
    assert llm.embed(["가", "나"]) == [[1.0, 0.0], [0.0, 1.0]]
    req = fake_llm.requests[0]
    assert req["path"] == "/v1/embeddings" and req["body"] == {"model": "test-embed", "input": ["가", "나"]}
    assert llm.embed([]) == [] and len(fake_llm.requests) == 1                # 빈 입력은 부르지 않는다


def test_failures_become_llm_error(fake_llm):
    fake_llm.reply = 500
    with pytest.raises(llm.LLMError):
        llm.chat([{"role": "user", "content": "안녕"}])
    with pytest.raises(llm.LLMError):
        list(llm.stream([{"role": "user", "content": "안녕"}]))
    fake_llm.reply = {"unexpected": True}
    with pytest.raises(llm.LLMError):
        llm.chat([{"role": "user", "content": "안녕"}])
    fake_llm.reply = {"data": [{"index": 0, "embedding": [1.0]}]}
    with pytest.raises(llm.LLMError):
        llm.embed(["가", "나"])                                                 # 개수가 다르면 조용히 넘기지 않는다


def test_stream_keeps_provider_extras_and_same_index_calls(fake_llm):
    """Gemini 식 — 호출이 통째로, 같은 index 로, 되돌려 보내야 하는 덧붙임(thought_signature)과 함께 온다."""
    sig = {"google": {"thought_signature": "abc123"}}
    fake_llm.reply = [
        _delta(tool_calls=[{"index": 0, "id": "f1", "type": "function", "extra_content": sig,
                            "function": {"name": "get_graduation", "arguments": "{}"}}]),
        _delta(tool_calls=[{"index": 0, "id": "f2", "type": "function", "function": {"name": "get_exams", "arguments": "{}"}}]),
        "[DONE]",
    ]
    msg = list(llm.stream([{"role": "user", "content": "졸업까지 몇 학점?"}], tools=TOOLS))[-1][1]
    assert msg["tool_calls"] == [
        {"id": "f1", "type": "function", "extra_content": sig, "function": {"name": "get_graduation", "arguments": "{}"}},
        {"id": "f2", "type": "function", "function": {"name": "get_exams", "arguments": "{}"}},
    ]


def test_error_shows_server_reason(fake_llm):
    fake_llm.reply = (400, {"error": {"code": 400, "message": "Function call is missing a thought_signature", "status": "INVALID_ARGUMENT"}})
    with pytest.raises(llm.LLMError, match=r"서버 오류 \(400\) — Function call is missing a thought_signature"):
        llm.chat([{"role": "user", "content": "안녕"}])
