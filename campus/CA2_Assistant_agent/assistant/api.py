"""로컬 API (프로토타입) — router 에 경로를 두고, 두 가지로 쓴다:
    ① 혼자 띄우기  python -m assistant → 아래 app (127.0.0.1 전용 + 시험용 화면 web/index.html)
    ② 유니버스 PC 앱에 붙이기  학사 백엔드가 router 를 /api/assistant 아래에 include 한다 (앱 창 세션 검사는 그쪽이 한다)

    GET  /                         시험용 화면
    GET  /v1/settings/llm          연결 설정 보기 (키는 있는지와 끝 4글자만)
    PUT  /v1/settings/llm          {provider, baseUrl, model, apiKey?} 저장 — 키는 OS 암호 저장소로
    DELETE /v1/settings/llm        설정·키 지우기
    POST /v1/settings/llm/models   {baseUrl?, apiKey?} 그 주소의 모델 목록 (저장 전에 고를 수 있게 — 비우면 저장된 값)
    POST /v1/settings/llm/test     저장된 설정으로 연결 시험 {ok, tools, error}
    GET · PUT /v1/settings/consent 강의자료 본문을 AI 로 보내는 데 동의했는지 {materials}
    POST /v1/agent/chat            {messages, campus?, confirmPii?} → SSE (delta · tool · card · pending · consent · warn · error · done)
    GET  /v1/kb/search?q=          키워드 검색 (간단 모드)

다른 사이트가 이 서버를 몰래 부르지 못하게, Host 가 내 PC 가 아니거나 Origin 이 다른 곳인 요청은 막는다.
요청 본문(대화·키)은 로그에 남기지 않는다.
"""
from __future__ import annotations

import json
from typing import Any, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import agent, llm, pdfsearch, privacy, search  # noqa: F401 — pdfsearch 는 붙이는 쪽(PC 앱)이 이 모듈로 꺼내 쓴다
from . import config as C
from . import settings as S

router = APIRouter()


class LlmSettings(BaseModel):
    provider: str = "custom"
    baseUrl: str
    model: str
    apiKey: Optional[str] = None


class ModelsQuery(BaseModel):
    baseUrl: Optional[str] = None
    apiKey: Optional[str] = None


class Consent(BaseModel):
    materials: bool


class ChatRequest(BaseModel):
    messages: list[dict[str, Any]]
    campus: Optional[str] = None
    confirmPii: bool = False


@router.get("/v1/settings/llm")
def get_settings() -> dict:
    return S.view()


@router.put("/v1/settings/llm")
def put_settings(body: LlmSettings) -> dict:
    try:
        return S.save(body.provider, body.baseUrl, body.model, body.apiKey)
    except S.Invalid as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/v1/settings/llm")
def delete_settings() -> dict:
    return S.clear()


@router.post("/v1/settings/llm/models")
def list_models(body: ModelsQuery) -> dict:
    cur = S.slot()
    try:
        base = S.check_url(body.baseUrl) if body.baseUrl else cur.base_url
        # 저장된 키는 저장된 주소로만 보낸다 — 다른 주소를 물으면 그 주소용 키를 같이 받아야 한다
        key = (body.apiKey or "").strip() or (cur.api_key if base == cur.base_url else "") or ("local" if S.is_local(base) else "")
        if not base or not key:
            raise S.Invalid("주소와 API 키를 넣어 주세요")
        return {"models": llm.models(base, key)}
    except S.Invalid as e:
        raise HTTPException(400, str(e)) from e
    except llm.LLMError as e:
        raise HTTPException(502, str(e)) from e


@router.post("/v1/settings/llm/test")
def test_settings() -> dict:
    if not S.slot().ready:
        return {"ok": False, "tools": False, "error": "먼저 주소·모델·키를 저장해 주세요"}
    return llm.probe()


@router.get("/v1/settings/consent")
def get_consent() -> dict:
    return {"materials": S.consent()}


@router.put("/v1/settings/consent")
def put_consent(body: Consent) -> dict:
    return {"materials": S.set_consent(body.materials)}


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/v1/agent/chat")
def chat(body: ChatRequest) -> StreamingResponse:
    last = next((m for m in reversed(body.messages) if isinstance(m, dict) and m.get("role") == "user"), {})
    found = [] if body.confirmPii or not llm.available() else privacy.find(str(last.get("content") or ""))

    def events():
        if found:                                        # 보내기 전에 한 번 묻는다 (CA2-R32)
            yield _sse("warn", {"kinds": found, "text": f"{'·'.join(found)}처럼 보이는 내용이 있어요. 그래도 AI 로 보낼까요?"})
            yield _sse("done", None)
            return
        for kind, data in agent.run(body.messages, campus=body.campus):
            yield _sse(kind, data)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


@router.get("/v1/kb/search")
def kb_search(q: str, campus: Optional[str] = None) -> dict:
    return {"results": search.search(q, campus=campus)}


# ---------------------------------------------------------------- 혼자 띄울 때
app = FastAPI(title="CA2 캠퍼스 생활 Q&A·에이전트 (프로토타입)", docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def local_only(request: Request, call_next):
    host = urlsplit(f"//{request.headers.get('host', '')}").hostname
    origin = request.headers.get("origin")
    if host not in S.LOCAL_HOSTS or (origin and urlsplit(origin).hostname not in S.LOCAL_HOSTS):
        return JSONResponse({"detail": "이 PC 에서만 열 수 있습니다"}, status_code=403)
    return await call_next(request)


@app.get("/")
def index():
    return FileResponse(C.WEB_DIR / "index.html")


app.include_router(router)
