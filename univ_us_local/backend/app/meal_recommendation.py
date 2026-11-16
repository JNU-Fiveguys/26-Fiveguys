"""CA1 식사 화면·API를 앱 세션 안에 붙인다. 공개 seed는 번들, 입력 초안은 앱 데이터에 둔다."""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from . import attendance
from . import config as C


def _read_timetable(day: date):
    from meal import academic

    try:
        if attendance.f3 is None:
            raise ImportError("attendance unavailable")
        from attendance.meal_context import export_context

        # F3 config already follows this app's data root. No subprocess or copied timetable.
        return academic.snapshot_from_context(export_context(day), day)
    except Exception:
        summary = academic.explicit_summary(day, provided=False)
        summary.update(status="unavailable", dataMode="app", warnings=[academic.STATUS_MESSAGES["unavailable"]])
        return academic.AcademicSnapshot({"version": 1, "date": day.isoformat(), "blocks": []}, summary)


def application() -> FastAPI:
    """별도 8030 서버 없이 /api/meal/에서 작동한다. 다른 기능의 시작을 막지 않는다."""
    try:
        if not (C.CA1_AGENT_DIR / "meal" / "api.py").is_file():
            raise ImportError("meal unavailable")
        if str(C.CA1_AGENT_DIR) not in sys.path:
            sys.path.insert(0, str(C.CA1_AGENT_DIR))
        from meal.api import create_embedded_app

        root = Path(os.environ.get("UNIVUS_DATA_ROOT") or C.PROJECT_ROOT / "desktop" / ".dev-data")
        entries = Path(os.environ.get("CA1_ENTRIES_DIR") or root / "CA1_Meal_agent" / "data" / "survey" / "user_entries")
        return create_embedded_app(timetable_reader=_read_timetable, entries_dir=entries)
    except Exception as error:
        print(f"CA1 식사 추천을 불러오지 못했습니다: {type(error).__name__}", file=sys.stderr)
        unavailable = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @unavailable.get("/", response_class=HTMLResponse, status_code=503)
        def missing():
            return "<html lang='ko'><meta charset='utf-8'><p>식사 추천을 불러오지 못했습니다. 앱을 다시 실행해 주세요.</p></html>"

        return unavailable
