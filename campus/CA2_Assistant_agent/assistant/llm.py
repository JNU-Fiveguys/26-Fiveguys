"""LLM 호출 — OpenAI Responses / 호환 chat/completions · embeddings (urllib).
연결 정보(주소·키·모델)는 settings.slot() — 화면에서 저장한 설정이 먼저, 없으면 .env 의 LLM_MAIN_*. 임베딩은 LLM_EMBED_*.

    chat(messages, tools)      한 번에 받기 → assistant 메시지 {role, content, tool_calls?}
    stream(messages, tools)    SSE 로 받기 → ("delta", 글자) … 마지막에 ("message", 위와 같은 메시지) 한 번 (CA2-R03)
    embed(texts)               임베딩 벡터 목록 (CA2-R10 하이브리드 검색의 한쪽)
    models(base_url, key)      그 주소의 모델 이름 목록 (설정 화면의 모델 고르기)
    probe(slot)                연결 시험 — 대화가 되는지 · 도구 호출을 하는지

설정이 없으면 available() 이 False — 부르는 쪽이 축소 모드(CA2-R53)로 간다. 실패는 모두 LLMError 하나로 올린다.
여기서는 아무것도 저장·로그하지 않는다 — 대화 원문은 서버에 남기지 않는다(CA2-R50).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Iterator, Optional
from urllib.parse import urlsplit

from . import config as C
from . import settings as S

Message = dict[str, Any]


class LLMError(RuntimeError):
    pass


def available() -> bool:
    return S.slot().ready


def embed_available() -> bool:
    return C.embed_configured()


def _post(url: str, key: str, body: dict, timeout: float):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    return urllib.request.urlopen(req, timeout=timeout)


def _prepare(slot: Optional[S.Slot], messages: list[Message], tools: Optional[list[dict]], temperature: float,
             max_tokens: Optional[int]) -> tuple[S.Slot, dict]:
    slot = slot or S.slot()
    if not slot.ready:
        raise LLMError("LLM 이 연결되지 않았습니다 — 설정에서 API 키를 넣어 주세요")
    if max_tokens is None:
        max_tokens = C.OPENAI_MAX_OUTPUT_TOKENS if _uses_responses(slot) else 800
    if _uses_responses(slot):
        return slot, _responses_body(slot, messages, tools, max_tokens)
    body: dict[str, Any] = {"model": slot.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if tools:
        body["tools"] = tools
    return slot, body


def _uses_responses(slot: S.Slot) -> bool:
    # Route by API provider, never by model name. Custom OpenAI-compatible
    # endpoints retain their existing Chat Completions contract.
    return urlsplit(slot.base_url).hostname == "api.openai.com"


def _responses_body(slot: S.Slot, messages: list[Message], tools: Optional[list[dict]], max_tokens: int) -> dict:
    items: list[dict] = []
    for msg in messages:
        if msg.get("_responses_output"):
            # Preserve encrypted reasoning, phase and call IDs within a tool turn.
            # These items are kept in memory only, never sent to the UI or saved.
            items.extend(msg["_responses_output"])
        elif msg.get("role") == "tool":
            items.append({"type": "function_call_output", "call_id": msg["tool_call_id"], "output": msg.get("content") or ""})
        else:
            if msg.get("content") or not msg.get("tool_calls"):
                items.append({"role": msg["role"], "content": msg.get("content") or ""})
            for call in msg.get("tool_calls") or []:
                fn = call["function"]
                items.append({"type": "function_call", "call_id": call["id"], "name": fn["name"],
                              "arguments": fn["arguments"]})
    # Use the model's defaults for optional sampling/reasoning settings. Sending
    # temperature or forcing a reasoning effort would require model-name rules.
    body: dict[str, Any] = {"model": slot.model, "input": items, "max_output_tokens": max_tokens,
                            "store": False, "include": ["reasoning.encrypted_content"]}
    if tools:
        body["tools"] = [{"type": "function", **t["function"], "strict": t["function"].get("strict", False)} for t in tools]
    return body


def _response_message(response: dict) -> Message:
    if response.get("status") == "failed" or response.get("error"):
        err = response.get("error") or {}
        raise LLMError(f"AI 응답 오류 — {str(err.get('message') or '응답을 만들지 못했습니다')[:200]}")
    if response.get("status") == "incomplete":
        if (response.get("incomplete_details") or {}).get("reason") == "max_output_tokens":
            raise LLMError("AI 응답이 토큰 상한에 도달했습니다. 질문을 짧게 나누거나 CA2_OPENAI_MAX_OUTPUT_TOKENS 설정을 확인해 주세요.")
        raise LLMError("AI 응답이 완료되지 않았습니다. 다시 시도해 주세요.")
    if response.get("status") != "completed":
        raise LLMError("AI 응답이 완료되지 않았습니다. 다시 시도해 주세요.")
    output = response["output"]
    text: list[str] = []
    calls: list[dict] = []
    for item in output:
        if item.get("type") == "message":
            for part in item.get("content") or []:
                if part.get("type") == "output_text":
                    text.append(part["text"])
                elif part.get("type") == "refusal":
                    text.append(part["refusal"])
        elif item.get("type") == "function_call":
            if item.get("status") not in (None, "completed"):
                raise LLMError("AI 도구 호출이 완료되지 않았습니다. 다시 시도해 주세요.")
            calls.append({"id": item["call_id"], "type": "function", "function": {
                "name": item["name"], "arguments": item["arguments"]}})
    if not text and not calls:
        raise LLMError("AI가 답변이나 도구 호출을 반환하지 않았습니다.")
    msg: Message = {"role": "assistant", "content": "".join(text), "_responses_output": output}
    if calls:
        msg["tool_calls"] = calls
    return msg


def _response_stream(response) -> Iterator[tuple[str, Any]]:
    for raw in response:
        line = raw.decode("utf-8").strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        event = json.loads(payload)
        kind = event.get("type")
        if kind in ("response.output_text.delta", "response.refusal.delta"):
            yield "delta", event.get("delta") or ""
        elif kind == "response.completed":
            yield "message", _response_message(event["response"])
            return
        elif kind in ("response.failed", "response.incomplete"):
            _response_message(event["response"])
            raise LLMError("AI 응답이 완료되지 않았습니다.")
        elif kind == "error":
            raise LLMError(f"AI 응답 오류 — {str(event.get('message') or '응답을 읽지 못했습니다')[:200]}")
    # Never execute partially streamed function arguments after a disconnect.
    raise LLMError("AI 응답 수신이 완료되기 전에 연결이 끊겼습니다. 다시 시도해 주세요.")


def _detail(e: urllib.error.HTTPError) -> str:
    """서버가 본문에 적어 준 이유(있으면) — 무엇이 틀렸는지 알 수 있게 앞부분만. 키는 응답 본문에 없다."""
    try:
        data = json.loads(e.read().decode("utf-8", "replace"))
        err = (data[0] if isinstance(data, list) else data).get("error")
        msg = err.get("message") if isinstance(err, dict) else err
        return f" — {' '.join(str(msg).split())[:200]}" if msg else ""
    except Exception:                                    # noqa: BLE001
        return ""


def _why(e: Exception) -> str:
    """사용자에게 보여 줄 실패 이유 — 키는 넣지 않는다."""
    if isinstance(e, urllib.error.HTTPError):
        return {401: "키가 맞지 않습니다 (401)", 403: "이 키로는 쓸 수 없습니다 (403)", 404: "주소나 모델 이름이 맞지 않습니다 (404)",
                429: "사용 한도를 넘었습니다 (429) — 잠시 뒤 다시 해 보세요"}.get(e.code, f"서버 오류 ({e.code}){_detail(e)}")
    if isinstance(e, (urllib.error.URLError, TimeoutError)):
        return "서버에 연결하지 못했습니다 — 주소와 네트워크를 확인해 주세요"
    return f"응답을 읽지 못했습니다 ({type(e).__name__})"


_ERRORS = (urllib.error.URLError, KeyError, IndexError, TypeError, ValueError, TimeoutError)


def _clean(msg: Message) -> Message:
    """다음 요청의 messages 에 그대로 다시 넣을 수 있는 모양으로 — 빈 tool_calls 는 뺀다(일부 제공자가 빈 목록을 거부한다)."""
    out: Message = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        out["tool_calls"] = msg["tool_calls"]
    return out


def _merge_call(calls: list[dict], tc: dict) -> None:
    """스트리밍 도구 호출 조각을 합친다. 제공자마다 모양이 다르다:
      - OpenAI 식: 같은 index 로 이름 → 인자 조각이 이어서 온다
      - Gemini 식: 호출 하나가 한 조각에 통째로 오고, 여러 호출이 같은 index(0) 로 올 수 있다 → id 가 다르면 새 호출
    name·arguments 말고 온 것(예: Gemini 의 extra_content.google.thought_signature)은 **그대로 둔다** — 다음 요청에 도구 호출을
    되돌려 보낼 때 빠지면 서버가 400 으로 거절한다."""
    index, cid = tc.get("index", 0), tc.get("id") or ""
    cur = next((c for c in reversed(calls) if c["index"] == index), None)
    if cur is None or (cid and cur["id"] and cid != cur["id"]):
        cur = {"index": index, "id": "", "type": "function", "function": {"name": "", "arguments": ""}}
        calls.append(cur)
    fn = tc.get("function") or {}
    cur["id"] = cid or cur["id"]
    cur["function"]["name"] += fn.get("name") or ""
    cur["function"]["arguments"] += fn.get("arguments") or ""
    for k, v in fn.items():
        if k not in ("name", "arguments"):
            cur["function"][k] = v
    for k, v in tc.items():
        if k not in ("index", "id", "type", "function") and v is not None:
            cur[k] = v


def chat(messages: list[Message], tools: Optional[list[dict]] = None, temperature: float = 0.2,
         max_tokens: Optional[int] = None, slot: Optional[S.Slot] = None) -> Message:
    slot, body = _prepare(slot, messages, tools, temperature, max_tokens)
    try:
        endpoint = "responses" if _uses_responses(slot) else "chat/completions"
        with _post(f"{slot.base_url}/{endpoint}", slot.api_key, body, C.LLM_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
        if _uses_responses(slot):
            return _response_message(data)
        return _clean(data["choices"][0]["message"])
    except _ERRORS as e:
        raise LLMError(_why(e)) from e


def stream(messages: list[Message], tools: Optional[list[dict]] = None, temperature: float = 0.2,
           max_tokens: Optional[int] = None, slot: Optional[S.Slot] = None) -> Iterator[tuple[str, Any]]:
    slot, body = _prepare(slot, messages, tools, temperature, max_tokens)
    body["stream"] = True
    text: list[str] = []
    calls: list[dict] = []                               # 도구 호출은 조각으로 온다 — 이름·인자를 이어 붙인다 (_merge_call)
    try:
        endpoint = "responses" if _uses_responses(slot) else "chat/completions"
        with _post(f"{slot.base_url}/{endpoint}", slot.api_key, body, C.LLM_TIMEOUT) as r:
            if _uses_responses(slot):
                yield from _response_stream(r)
                return
            for raw in r:
                line = raw.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                choices = json.loads(payload).get("choices") or []
                delta = (choices[0].get("delta") or {}) if choices else {}
                if delta.get("content"):
                    text.append(delta["content"])
                    yield "delta", delta["content"]
                for tc in delta.get("tool_calls") or []:
                    _merge_call(calls, tc)
    except _ERRORS as e:
        raise LLMError(_why(e)) from e
    yield "message", _clean({"content": "".join(text), "tool_calls": [{k: v for k, v in c.items() if k != "index"} for c in calls]})


def embed(texts: list[str]) -> list[list[float]]:
    if not embed_available():
        raise LLMError("임베딩이 설정되지 않았습니다 (LLM_EMBED_MODEL)")
    if not texts:
        return []
    try:
        with _post(f"{C.EMBED_BASE_URL}/embeddings", C.EMBED_API_KEY, {"model": C.EMBED_MODEL, "input": texts},
                   C.LLM_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))["data"]
        vecs = [d["embedding"] for d in sorted(data, key=lambda d: d.get("index", 0))]
    except _ERRORS as e:
        raise LLMError(_why(e)) from e
    if len(vecs) != len(texts):
        raise LLMError(f"임베딩 개수가 다릅니다: 보낸 것 {len(texts)} · 받은 것 {len(vecs)}")
    return vecs


def models(base_url: str, api_key: str) -> list[str]:
    req = urllib.request.Request(f"{base_url}/models", headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=C.LLM_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
        return sorted(str(m["id"]).removeprefix("models/") for m in data["data"])
    except _ERRORS as e:
        raise LLMError(_why(e)) from e


_PING = [{"type": "function", "function": {"name": "ping", "description": "연결 시험용. 반드시 이 도구를 호출한다.",
                                           "parameters": {"type": "object", "properties": {}}}}]


def probe(slot: Optional[S.Slot] = None) -> dict:
    """연결 시험 {ok, tools, error} — 도구를 붙여 한 번 부르고, 도구를 못 받는 모델이면 도구 없이 한 번 더."""
    ask = [{"role": "user", "content": "연결 시험입니다. ping 도구를 호출하세요."}]
    slot = slot or S.slot()
    # Responses counts reasoning as well as visible output. The legacy probe's
    # 16/64-token caps can reject a working reasoning model before it can answer.
    budget = C.OPENAI_MAX_OUTPUT_TOKENS if _uses_responses(slot) else None
    try:
        msg = chat(ask, tools=_PING, max_tokens=budget if budget is not None else 64, slot=slot)
        return {"ok": True, "tools": bool(msg.get("tool_calls")), "error": None}
    except LLMError:
        pass
    try:
        chat(ask, max_tokens=budget if budget is not None else 16, slot=slot)
        return {"ok": True, "tools": False, "error": None}
    except LLMError as e:
        return {"ok": False, "tools": False, "error": str(e)}
