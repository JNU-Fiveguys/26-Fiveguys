"""CA1 공강 식사 추천 — ../campus/CA1_Meal_agent 의 계산(meal.preview.MealApp)을 이 서버에 붙인다.

추천 계산 · 시간표(F3 출결) → 공강 · 지도 데이터는 전부 CA1_Meal_agent 에 있다. 여기서는
  - GET /api/meal/ui               CA1 미리보기 화면 그대로(화면 오른쪽 아래 전룡 MealLauncher 가 iframe 창으로 연다)
  - GET /api/meal/recommend?…      추천(공강 시작 · 길이 · 요일 · 출발 · 다음 건물 · 예산 · 인원 · 종류)
  - GET /api/meal/schedule?…       그 요일 시간표(F3 출결) → 공강 · 수업 건물
  - GET /api/meal/basemap.json     아이콘 없는 바탕 지도(OSM 길·건물) — CA1 의 data/osm/basemap.json 이 있을 때만
를 붙인다. CA1 은 표준 라이브러리만 쓰므로 이 백엔드의 .venv 에 더 설치할 것이 없다.
F3 출결 데이터 자리는 이 앱이 이미 F3_DATA_DIR 로 정해 두었으므로 CA1 이 같은 것을 읽는다.

CA1 폴더가 없거나 불러오지 못해도 나머지 화면은 그대로 동작해야 한다 → 라우터를 붙이지 않는다.
조사 양식(data/survey.csv)·지도 데이터는 커밋되지 않는 파일이라 이 PC 에만 있다(설치본에는 들어가지 않는다).
"""
from __future__ import annotations

import sys
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from . import config as C

_error: Optional[str] = None


def _load():
    global _error
    if not (C.CA1_AGENT_DIR / "meal" / "preview.py").exists():
        _error = f"CA1_Meal_agent 가 없습니다: {C.CA1_AGENT_DIR}"
        return None
    if str(C.CA1_AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(C.CA1_AGENT_DIR))
    try:
        from meal import preview as ca1        # type: ignore[import-not-found]
        return ca1
    except Exception as e:                      # noqa: BLE001 — CA1 이 못 떠도 나머지는 살린다
        _error = f"CA1 모듈을 불러오지 못했습니다: {type(e).__name__}: {e}"
        return None


ca1 = _load()
_app = None


def _svc():
    """길 그래프·바탕 지도는 크므로 처음 부를 때 한 번만 읽는다."""
    global _app
    if _app is None:
        _app = ca1.MealApp.default()
    return _app


def router():
    if not ca1:
        return None
    r = APIRouter(prefix="/api/meal", tags=["CA1 식사 추천"])

    @r.get("/ui", response_class=HTMLResponse, include_in_schema=False)
    def ui() -> HTMLResponse:
        return HTMLResponse(_svc().page(api="/api/meal/"))

    @r.get("/recommend")
    def recommend(request: Request) -> JSONResponse:
        body, code = _svc().recommend(dict(request.query_params))
        return JSONResponse(body, status_code=code)

    @r.get("/schedule")
    def schedule(request: Request) -> dict:
        return _svc().schedule(dict(request.query_params))

    @r.get("/places")
    def places(request: Request) -> dict:
        return _svc().places(dict(request.query_params))

    @r.get("/places/{place_id}")
    def place(place_id: str) -> JSONResponse:
        body, code = _svc().place(place_id)
        return JSONResponse(body, status_code=code)

    @r.get("/basemap.json", include_in_schema=False)
    def basemap() -> Response:
        data = _svc().basemap
        if not data:
            return JSONResponse({"detail": "바탕 지도 데이터가 없습니다 — CA1 에서 python -m meal.basemap fetch · build"}, status_code=404)
        return Response(data, media_type="application/json")

    return r


def status() -> dict:
    return {"available": ca1 is not None, "error": _error}
