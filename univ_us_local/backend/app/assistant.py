"""CA2 캠퍼스 생활 Q&A·에이전트 대화 — ../../campus/CA2_Assistant_agent 의 API(assistant.api)를 이 서버에 붙인다.

대화 엔진·AI 연결 설정(사용자가 넣는 API 키)·PDF 쪽 검색은 전부 CA2 폴더에 있다. 여기서는
  - 라우터를 /api/assistant 아래에 include 하고  (/api/assistant/v1/settings/* · /v1/agent/chat(SSE) · /v1/kb/search)
  - **내 데이터 도구**를 대화에 등록한다 — 이 PC 에 수집·계산돼 있는 값을 화면과 같은 API 로 읽어 그대로 넘긴다(다시 계산하지 않는다):
      get_schedule(합친 캘린더) · get_assignments(F7 급한 순) · get_exams(F5) · get_attendance(F3) · get_graduation(F2 학점)
      · list_materials(F4 목록) · search_materials(F4 PDF 를 쪽 단위로 읽어 질문과 맞는 쪽만 — 출처 '파일 p.N')
    성적(평점·과목별 성적)은 넘기지 않는다(_DROP). 강의자료 본문은 사용자가 동의한 뒤에만 AI 로 나간다(CA2 settings.consent, F4-R40).
앱 창 세션 검사(desktop_session)와 Origin 검사는 이 서버의 것을 그대로 받는다. CA2 는 학사 폴더를 import 하지 않는다 — 방향은 여기 → CA2 뿐이다.
설정 파일은 앱 데이터 폴더(UNIVUS_DATA_ROOT)/CA2_Assistant_agent/data 에 둔다 — 설치된 앱 꾸러미 안에 쓰지 않게.

CA2 폴더가 없거나 불러오지 못해도 나머지 화면은 그대로 동작해야 한다 → router() 가 None.
"""
from __future__ import annotations

import inspect
import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

from . import config as C

_error: Optional[str] = None


def _load():
    global _error
    if not (C.CA2_AGENT_DIR / "assistant" / "api.py").exists():
        _error = f"CA2_Assistant_agent 가 없습니다: {C.CA2_AGENT_DIR}"
        return None
    if os.environ.get("UNIVUS_DATA_ROOT"):               # CA2 config 를 import 하기 전에
        os.environ.setdefault("CA2_DATA_DIR", str(Path(os.environ["UNIVUS_DATA_ROOT"]) / "CA2_Assistant_agent" / "data"))
    if str(C.CA2_AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(C.CA2_AGENT_DIR))
    try:
        from assistant import api as ca2_api    # type: ignore[import-not-found]
        return ca2_api
    except Exception as e:                      # noqa: BLE001 — 어떤 이유로든 CA2 가 못 뜨면 나머지는 살린다
        _error = f"CA2 모듈을 불러오지 못했습니다: {type(e).__name__}: {e}"
        return None


ca2 = _load()


def router():
    return ca2.router if ca2 else None


# ---------------------------------------------------------------- 내 데이터 도구
ROLE = (
    "너는 유니버스 PC 앱의 대화 도우미다. 이 PC 에 모아 둔 학생 본인의 학사일정 · 과제와 마감 · 시험 · 출결 · 졸업 학점 · 강의자료로 답한다.\n"
    "성적(평점·과목별 성적)은 다루지 않는다 — 물으면 '성적은 대화에서 다루지 않아요'라고 답한다.\n"
    "일정·마감·출결·학점 질문에는 먼저 알맞은 도구를 부른다. 강의 내용 질문에는 search_materials 로 그 과목 강의자료를 찾아 그 안의 내용으로만 답한다.\n"
    "도구 결과가 비어 있으면 아직 수집되지 않았을 수 있다고 알리고, 해당 화면에서 수집하라고 안내한다.\n"
    "날짜와 시각은 값을 바꾸지 말고 '11월 19일(목) 23:59'처럼 읽기 쉽게 적는다. 마감 시각이 00:00 이면 그 전날 밤 자정까지라는 뜻이니 "
    "'11월 18일 밤 12시(19일 00:00)까지'처럼 적는다. 여러 건이면 급한 순으로 한 줄에 하나씩 적는다."
)
# 성적은 넘기지 않는다 + 화면 꾸밈·설정처럼 답에 쓸모없는 덩어리
_DROP = {"gpa", "grade", "grades", "gradePoint", "score", "academicWarning", "labels", "settings", "periods", "ruleset", "semesters",
         "source", "limits", "types", "difficulties", "defaults", "scan", "courseSettings", "filter", "analysis", "profile",
         "unmapped", "sessions", "meetings", "notices", "fileUrl", "downloadUrl", "eclassUrl", "computedAt", "updatedAt"}


def _routes(routes):
    """include_router 로 붙인 라우터 안까지 편다 (새 FastAPI 는 붙인 라우터를 한 덩어리로 들고 있다 — original_router)."""
    for r in routes:
        inner = getattr(r, "original_router", None)
        if inner is not None:
            yield from _routes(inner.routes)
        else:
            yield r


def _api(path: str, **params: Any) -> Any:
    """이 서버의 GET API 를 서버 안에서 그대로 부른다 — 화면이 보는 값과 같은 값을 쓰려고(계산기는 하나)."""
    from . import main                           # 도구가 불릴 때는 main 이 다 만들어져 있다
    for r in _routes(main.app.routes):
        if getattr(r, "path", None) == path and "GET" in (getattr(r, "methods", None) or ()):
            kw = {}
            for name, p in inspect.signature(r.endpoint).parameters.items():
                default = getattr(p.default, "default", p.default)       # Query(None) 같은 것은 그 안의 기본값
                kw[name] = params.get(name, None if default in (inspect.Parameter.empty, ...) or "Undefined" in repr(default) else default)
            return r.endpoint(**kw)
    raise LookupError(path)


def _slim(v: Any, cap: int) -> Any:
    if isinstance(v, dict):
        out = {k: _slim(x, cap) for k, x in v.items() if k not in _DROP}
        return {k: x for k, x in out.items() if x not in (None, "", [], {})}
    if isinstance(v, list):
        return [_slim(x, cap) for x in v[:cap]]
    return v[:200] if isinstance(v, str) else v


def _fit(data: dict) -> dict:
    """도구 결과가 LLM 에 보내는 상한을 넘지 않게 목록을 줄인다."""
    limit = ca2.C.TOOL_RESULT_CHARS - 200
    for cap in (40, 20, 10, 5):
        out = _slim(data, cap)
        if len(json.dumps(out, ensure_ascii=False)) <= limit:
            return out if cap == 40 else {**out, "note": f"길어서 목록마다 앞의 {cap}개까지만 실었다"}
    return {"error": "결과가 너무 길어 싣지 못했다. 범위를 좁혀 다시 물어 달라고 안내한다."}


def _link(label: str, href: str) -> dict:
    return {"type": "link", "data": {"label": label, "href": href}}


def _day(v: Any, fallback: date) -> str:
    try:
        return date.fromisoformat(str(v)).isoformat()
    except ValueError:
        return fallback.isoformat()


def _squash(s: str) -> str:
    return re.sub(r"[\s\[\]()_\-·]", "", s or "").lower()


def _pdfs(course: str) -> tuple[list[dict], list[str]]:
    """그 과목의 읽을 수 있는 PDF [{id, title, path, pages, course}] + 과목 이름 목록(못 찾았을 때 되물으려고)."""
    from . import materials
    f4 = materials.f4
    if not f4:
        return [], []
    listing = _api("/api/materials")
    names = sorted({m["course"] for m in listing["materials"] if m.get("course")})
    want = _squash(course)
    out = []
    with f4.store.connect() as con:
        for m in listing["materials"]:
            if m["ext"] != ".pdf" or m["missing"] or m["dupOf"] or not (want and want in _squash(m["course"])):
                continue
            try:
                path, _, _ = f4.service.file_target(con, m["id"])
            except Exception:                    # noqa: BLE001 — 사라진 파일은 건너뛴다
                continue
            out.append({"id": m["id"], "title": m["title"], "path": str(path), "pages": m["pages"], "course": m["course"]})
    return out, names


def _register() -> None:
    A, S, pdf = ca2.agent, ca2.S, ca2.pdfsearch
    A.set_role(ROLE)
    day = {"type": "string", "description": "YYYY-MM-DD"}

    @A.tool("get_schedule", "캘린더 일정을 날짜 범위로 조회한다 — 학사일정·과제 마감·시험·수업·내 일정이 함께 온다. 범위를 안 주면 오늘부터 2주.",
            {"type": "object", "properties": {"start": day, "end": day}})
    def get_schedule(args: dict, ctx: dict) -> dict:
        today = date.today()
        start = _day(args.get("start"), today)
        end = _day(args.get("end"), date.fromisoformat(start) + timedelta(days=14))
        events = sorted(_api("/api/events", start=start, end=end), key=lambda e: str(e.get("start") or ""))
        return {"content": _fit({"start": start, "end": end, "events": events}), "cards": [_link("캘린더에서 보기", "/")]}

    @A.tool("get_assignments", "e클래스 과제·퀴즈·동영상을 급한 순으로 조회한다 — 마감, 예상 소요시간, 그 순서인 이유.")
    def get_assignments(args: dict, ctx: dict) -> dict:
        d = _api("/api/priority")
        return {"content": _fit({"items": d["items"], "counts": d["counts"]}), "cards": [_link("과제 화면 열기", "/assignments")]}

    @A.tool("get_exams", "다가오는 시험·발표와 오늘 공부 분량을 조회한다.")
    def get_exams(args: dict, ctx: dict) -> dict:
        d = _api("/api/exams")
        return {"content": _fit({"exams": d["exams"], "counts": d["counts"], "today": d["today"]}), "cards": [_link("시험·발표 화면 열기", "/exams")]}

    @A.tool("get_attendance", "과목별 출결을 조회한다 — 결석·지각 횟수, 결석 한도까지 남은 여유, 위험 과목.")
    def get_attendance(args: dict, ctx: dict) -> dict:
        d = _api("/api/attendance/summary")
        return {"content": _fit({"courses": d["courses"], "totals": d["totals"]}), "cards": [_link("출결 화면 열기", "/attendance")]}

    @A.tool("get_graduation", "졸업요건 학점을 조회한다 — 이수·남은 학점, 영역별 현황. 성적은 없다.")
    def get_graduation(args: dict, ctx: dict) -> dict:
        d = _api("/api/graduation/status")
        keep = {k: d.get(k) for k in ("verdict", "headline", "total", "areas", "checks", "certifications", "reasons")}
        return {"content": _fit(keep), "cards": [_link("졸업요건 화면 열기", "/graduation")]}

    @A.tool("list_materials", "모아 둔 강의자료 목록(과목·파일 이름·쪽수·주차)을 조회한다. 내용은 search_materials 로 읽는다.",
            {"type": "object", "properties": {"course": {"type": "string", "description": "과목 이름(일부만 적어도 된다)"}}})
    def list_materials(args: dict, ctx: dict) -> dict:
        d = _api("/api/materials")
        want = _squash(str(args.get("course") or ""))
        items = [{k: m.get(k) for k in ("course", "title", "pages", "week", "kindLabel", "ext")}
                 for m in d["materials"] if not want or want in _squash(m.get("course") or "")]
        return {"content": _fit({"totals": {k: d["totals"].get(k) for k in ("files", "pages", "courses")}, "materials": items}),
                "cards": [_link("강의자료 화면 열기", "/courses")]}

    @A.tool("search_materials", "한 과목의 강의자료(PDF)를 쪽 단위로 훑어 질문과 관련된 쪽의 본문을 가져온다. 강의 내용 질문에 쓴다.",
            {"type": "object", "properties": {"course": {"type": "string", "description": "과목 이름(일부만 적어도 된다)"},
                                              "query": {"type": "string", "description": "찾을 내용 — 핵심 낱말 위주로"}},
             "required": ["course", "query"]}, sends_materials=True)
    def search_materials(args: dict, ctx: dict) -> dict:
        files, names = _pdfs(str(args.get("course") or ""))
        if not files:
            return {"content": {"error": "그 과목의 PDF 강의자료를 찾지 못했다. 과목 이름을 확인해 달라고 한다.", "courses": names}}
        try:
            res = pdf.search(files, str(args.get("query") or ""))
        except pdf.TooMany:
            return {"content": {"error": f"이 과목의 강의자료가 너무 많아({len(files)}개 파일) 다 확인할 수 없다. "
                                         "신뢰할 수 있는 답을 줄 수 없다고 말하고, 주차나 주제를 좁혀 달라고 한다."}}
        note = "관련된 쪽을 찾지 못했다. 강의자료에서 확인되지 않는다고 답한다." if not res["hits"] else "아래 쪽의 내용으로만 답하고 (파일 p.쪽) 을 적는다."
        by_id = {f["id"]: f for f in files}
        return {"content": {"searched": {"files": res["files"], "pages": res["pages"], "unreadable": res["unread"][:5]}, "note": note,
                            "results": [{"file": h["title"], "page": h["page"], "text": h["text"]} for h in res["hits"]]},
                "cards": [{"type": "material", "data": {"title": h["title"], "page": h["page"], "course": by_id[h["id"]]["course"],
                                                        "text": h["text"][:240], "href": f"/api/materials/{h['id']}/file#page={h['page']}"}}
                          for h in res["hits"][:3]]}


if ca2:
    _register()
