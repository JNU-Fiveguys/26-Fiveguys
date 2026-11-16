"""로컬 CA1 실행: python -m meal.api (http://127.0.0.1:8030).

공개 데모(MEAL_PUBLIC=1): Cloudflare 터널로 다른 사람에게 보여 줄 때만 켠다.
  - 개인 학사 시간표를 읽지 않는다(시간표 API는 빈 요약, 추천은 직접 입력만)
  - 조사 입력 화면·저장 API(/survey, /v1/meal/survey…)를 막는다
  - *.trycloudflare.com 호스트를 추가로 허용한다
"""
from __future__ import annotations

import os
from datetime import date, datetime
from html import escape
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import academic, service, survey, timeline
from .ranking import Filters
from .timetable import SEOUL, TimetableError, load_buildings, resolve_context

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="CA1 식사 추천", version="0.2.0")
app.mount("/assets", StaticFiles(directory=ROOT / "web" / "assets", check_dir=False), name="assets")
PUBLIC = os.environ.get("MEAL_PUBLIC") == "1"
PUBLIC_TIMETABLE_MESSAGE = "공개 데모에서는 시간표를 읽지 않습니다. 출발 건물과 다음 강의실을 직접 입력하세요."
_HOSTS = ["127.0.0.1", "localhost", "::1", "testserver"] + (["*.trycloudflare.com"] if PUBLIC else [])
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_HOSTS)


@app.middleware("http")
async def local_request(request: Request, call_next):
    if PUBLIC and (request.url.path == "/survey" or request.url.path.startswith("/v1/meal/survey")):
        return JSONResponse({"detail": "공개 데모에서는 사용할 수 없습니다."}, status_code=404)
    origin = request.headers.get("origin")
    if request.method == "POST" and origin:
        try:
            url = urlsplit(origin)
            if PUBLIC:
                # 터널 뒤에서는 브라우저가 https, 서버가 http 로 보이므로 호스트만 비교한다
                same_origin = url.netloc == request.headers.get("host", "")
            else:
                same_origin = url.scheme == request.url.scheme and url.netloc == request.url.netloc
        except ValueError:
            same_origin = False
        if not same_origin:
            return JSONResponse({"detail": "이 로컬 식사 추천 화면에서 요청하세요."}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


class MealRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    at: datetime = Field(default_factory=lambda: datetime.now(SEOUL))
    timetable: dict | None = None
    auto_timetable: bool = Field(True, alias="autoTimetable")
    origin: str | tuple[float, float] | None = None
    next_room: str | tuple[float, float] | None = Field(None, alias="nextRoom")
    next_start: str | None = Field(None, alias="nextStart", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    no_next_class: bool = Field(False, alias="noNextClass")
    rain: bool = False
    exam_period: bool = Field(False, alias="examPeriod")
    include_samples: bool = Field(False, alias="includeSamples")
    route_mode: Literal["osm", "estimate"] = Field("osm", alias="routeMode")
    budget: int | None = Field(None, ge=0)
    budget_min: int | None = Field(None, alias="budgetMin", ge=0, strict=True)
    budget_max: int | None = Field(None, alias="budgetMax", ge=0, strict=True)

    @model_validator(mode="after")
    def validate_budget_range(self):
        if self.budget_min is not None or self.budget_max is not None:
            if self.budget is not None:
                raise ValueError("예산은 budget 또는 budgetMin/budgetMax 중 한 방식으로 지정하세요.")
            if self.budget_min is not None and self.budget_max is not None and self.budget_min > self.budget_max:
                raise ValueError("최소 예산은 최대 예산보다 클 수 없습니다.")
        return self


@app.get("/ui", include_in_schema=False)
@app.get("/", include_in_schema=False)
def home(request: Request):
    return _page("index.html", request)


def _page(name: str, request: Request) -> HTMLResponse:
    """같은 화면을 단독 서버와 /api/meal 아래에서 그대로 연다."""
    page = (ROOT / "web" / name).read_text(encoding="utf-8")
    base = escape(request.scope.get("root_path", "").rstrip("/") + "/", quote=True)
    page = page.replace("<head>", f'<head><base href="{base}">', 1)
    if getattr(request.app.state, "public", PUBLIC):
        page = page.replace("<html", "<html data-public-demo", 1)
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@app.get("/survey", include_in_schema=False)
def survey_home(request: Request):
    return _page("survey.html", request)


@app.get("/v1/meal/survey")
def survey_queue(request: Request):
    return survey.queue(entries_dir=getattr(request.app.state, "entries_dir", None))


@app.get("/v1/meal/survey/{place_id}")
def survey_detail(place_id: str, request: Request):
    try:
        return survey.detail(place_id, entries_dir=getattr(request.app.state, "entries_dir", None))
    except KeyError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@app.post("/v1/meal/survey/{place_id}")
def save_survey(place_id: str, body: survey.SurveyEntry, request: Request):
    try:
        return survey.save(place_id, body, entries_dir=getattr(request.app.state, "entries_dir", None))
    except KeyError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    except survey.RevisionConflict as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, detail="입력 파일을 저장하지 못했습니다. 입력 내용을 유지하고 다시 시도하세요.") from exc


@app.get("/v1/buildings")
def buildings():
    return {"buildings": load_buildings(), "attribution": "© OpenStreetMap contributors"}


@app.get("/v1/meal/example-timetable")
def example_timetable():
    # 시연용 시간표. 개인 시간표로 오인하지 않게 이름을 붙인다.
    return FileResponse(ROOT / "data" / "examples" / "attendance_summary.json",
                        media_type="application/json", filename="sample-attendance-summary.json")


@app.get("/v1/meal/timetable")
def timetable_status(request: Request, date_value: str | None = Query(None, alias="date", pattern=r"^\d{4}-\d{2}-\d{2}$"),
                     at: datetime | None = Query(None)):
    try:
        requested_day = academic.local_date(at) if at is not None else None
        selected = date.fromisoformat(date_value) if date_value else requested_day or datetime.now(SEOUL).date()
    except (ValueError, OverflowError) as exc:
        raise HTTPException(422, detail="시간표 날짜 형식이 올바르지 않습니다.") from exc
    if requested_day is not None and selected != requested_day:
        raise HTTPException(422, detail="시간표 날짜가 선택한 시각의 한국 날짜와 다릅니다.")
    if getattr(request.app.state, "public", PUBLIC):
        summary = academic.explicit_summary(selected, provided=False)
        summary.update(status="unavailable", warnings=[PUBLIC_TIMETABLE_MESSAGE], originPreview=None,
                       originHint=PUBLIC_TIMETABLE_MESSAGE)
        return summary
    reader = getattr(request.app.state, "timetable_reader", None) or academic.read_timetable
    snapshot = reader(selected)
    if at is None:
        # Existing status-only clients receive no timetable locations.
        return snapshot.summary
    summary = {**snapshot.summary, "originPreview": None, "originHint": ""}
    try:
        # Reuse the recommendation's release-time and origin selection rules.
        # A missing next classroom must not hide a known departure building.
        selection = resolve_context(snapshot.require_usable(), at=at, no_next_class=True)
        summary["originPreview"] = selection.origin_location.to_dict()
        summary["originHint"] = " ".join(selection.warnings)
    except TimetableError as exc:
        summary["originHint"] = str(exc)
    return summary


@app.get("/v1/meal/timeline")
def meal_timeline(request: Request, date_value: date = Query(alias="date")):
    if getattr(request.app.state, "public", PUBLIC):
        return {"date": date_value.isoformat(), "status": "unavailable", "classes": [], "slots": [],
                "start": "09:00", "end": "20:00", "warnings": [PUBLIC_TIMETABLE_MESSAGE]}
    reader = getattr(request.app.state, "timetable_reader", None) or academic.read_timetable
    try:
        return timeline.daily_timeline(reader(date_value), date_value)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@app.get("/v1/meal/basemap", include_in_schema=False)
def meal_basemap():
    path = ROOT / "data" / "osm" / "basemap.json"
    if not path.is_file():
        raise HTTPException(503, detail="지도 자료를 찾지 못했습니다. 앱을 업데이트해 주세요.")
    return FileResponse(path, media_type="application/json")


@app.post("/v1/meal/recommend")
def recommend(body: MealRequest, request: Request):
    data = body.model_dump()
    if getattr(request.app.state, "public", PUBLIC):  # 개인 시간표는 공개 데모에서 읽지 않는다
        data["auto_timetable"] = False
        data["timetable"] = None
    budget = data.pop("budget")
    budget_min, budget_max = data.pop("budget_min"), data.pop("budget_max")
    try:
        return service.recommend(**data, timetable_reader=getattr(request.app.state, "timetable_reader", None), filters=Filters(
            budget=budget, budget_explicit=budget is not None,
            budget_min=budget_min, budget_max=budget_max))
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@app.get("/v1/meal/places")
def places(query: str = Query("", max_length=120), status: Literal["active", "missing", "all"] = "active",
           ready: bool | None = None, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
    try:
        return service.list_places(query=query, status=status, ready=ready, offset=offset, limit=limit)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@app.get("/v1/meal/places/{place_id}")
def place_detail(place_id: str):
    try:
        return service.get_place(place_id)
    except KeyError as exc:
        raise HTTPException(404, detail="실제 식당 목록에서 찾을 수 없습니다.") from exc
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc


def create_embedded_app(*, timetable_reader, entries_dir: Path) -> FastAPI:
    """유니버스의 세션·Host·Origin 검사를 받는 하위 앱. 단독 앱 설정은 바꾸지 않는다."""
    embedded = FastAPI(title="CA1 식사 추천", routes=list(app.router.routes),
                       docs_url=None, redoc_url=None, openapi_url=None)
    embedded.state.public = False
    embedded.state.timetable_reader = timetable_reader
    embedded.state.entries_dir = Path(entries_dir)

    @embedded.middleware("http")
    async def no_cache(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    return embedded


def main():
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8030)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
