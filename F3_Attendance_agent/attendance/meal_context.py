"""식사 추천용 하루 시간표 내보내기. 학사 데이터는 읽기 전용으로만 연다.

출결 회차는 하루 단위이므로 그대로 시간 블록으로 쓰지 않는다. F3가 계산한
휴강·복구·보강 상태를 유지하면서 그날 적용되는 시간표를 연속 교시·강의실별로
다시 나눈다. stdout 계약에는 과목·출결·성적·메모·계정 정보가 들어가지 않는다.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, time
from pathlib import Path

from . import academic_calendar as AC
from . import config as C
from . import service, sessions, store

_SEMESTER = re.compile(r"^\d{4}-[12SW]$")


def _empty(day: str = "", semester: str | None = None) -> dict:
    return {
        "version": 1, "date": day,
        "semester": {"id": semester, "start": None, "end": None},
        "blocks": [], "status": "unavailable", "warnings": [], "updatedAt": None,
        "courseCount": 0, "missingTimetableCount": 0,
    }


def _meetings_on(course: dict, day: str) -> list[dict]:
    current = None
    for version in sorted(course["timetable"]["versions"], key=lambda v: v.get("validFrom") or ""):
        if (version.get("validFrom") or "") <= day:
            current = version
    return current["meetings"] if current else []


def _period_blocks(meetings: list[dict], weekday: int, state: str, pmap: sessions.PeriodMap) -> list[dict]:
    """같은 강의실의 이어지는 교시만 합친다. 떨어진 오전·오후 수업은 별개다."""
    by_room: dict[str, set[int]] = {}
    for meeting in meetings:
        room = meeting.get("room") or ""
        if not isinstance(room, str):
            raise ValueError("invalid room")
        periods = meeting["periods"]
        if not isinstance(periods, list) or not periods or any(
            isinstance(p, bool) or not isinstance(p, int) or not 1 <= p <= 15 for p in periods
        ):
            raise ValueError("invalid periods")
        by_room.setdefault(room, set()).update(periods)
    blocks = []
    for room, periods in by_room.items():
        run: list[int] = []
        for period in [*sorted(periods), None]:
            if run and (period is None or period != run[-1] + 1):
                start, end = sessions.period_span(weekday, run, pmap)
                if not (re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", start)
                        and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", end) and start < end):
                    raise ValueError("invalid period times")
                blocks.append({"kind": "class", "start": start, "end": end,
                               "building": room, "state": state})
                run = []
            if period is not None:
                run.append(period)
    return blocks


def _course_blocks(course: dict, day: date, pmap: sessions.PeriodMap) -> list[dict]:
    blocks = []
    for session in course["sessions"]:
        if session["date"] != day.isoformat():
            continue
        state = session["state"]
        if state not in {"scheduled", "canceled"}:
            raise ValueError("invalid session state")
        if session["origin"] == "user":
            # 수기 보강에는 강의실 입력 필드가 없다. 원래 수업 강의실을 추측하지 않는다.
            meetings = [{"periods": session["periods"], "room": ""}]
        else:
            original = date.fromisoformat(session["makeupFor"]) if session["origin"] == "school" else day
            meetings = [m for m in _meetings_on(course, original.isoformat())
                        if m["weekday"] == original.weekday()]
            if not meetings:
                raise ValueError("missing session meetings")
        # 학교 보강의 과목·교시는 원래 날짜, 교시 시각은 실제 보강일의 요일을 따른다.
        blocks.extend(_period_blocks(meetings, day.weekday(), state, pmap))
    return blocks


def export_context(day: date | str, semester: str | None = None, *, db_path: Path | None = None) -> dict:
    """저장된 F3 시간표를 안전한 JSON 계약으로 반환한다. 오류 세부값은 내보내지 않는다.

    store.connect/overview는 마이그레이션·과목 동기화·경고 저장을 수행하므로 사용하지
    않는다. 읽기 전용 SQLite 트랜잭션에서 기존 계산 함수만 호출한다.
    """
    out = _empty()
    try:
        if isinstance(day, datetime):
            raise ValueError("invalid day")
        selected = date.fromisoformat(day) if isinstance(day, str) else day
        if not isinstance(selected, date) or (semester is not None and not _SEMESTER.fullmatch(semester)):
            raise ValueError("invalid selection")
        out = _empty(selected.isoformat(), semester)
    except (TypeError, ValueError):
        out["warnings"] = ["조회 날짜 또는 학기 형식을 확인하세요."]
        return out

    path = Path(db_path) if db_path is not None else C.DB_PATH
    try:
        if not path.is_file():
            out.update(status="missing", warnings=["학사 앱에 저장된 시간표 데이터가 없습니다. 학사 앱에서 시간표를 먼저 설정하세요."])
            return out
        # as_uri가 공백·한글·#·? 등 SQLite URI에서 특별한 경로 문자를 이스케이프한다.
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)) as con:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA query_only = ON")
            con.execute("BEGIN")
            cal = AC.load()
            sid = semester or service.current_semester(con, cal, selected)
            sem, courses = service.build_semester(con, sid, datetime.combine(selected, time()), cal)
            active = [c for c in courses if not c["excluded"]]
            missing = sum(not _meetings_on(c, selected.isoformat()) for c in active)
            out.update(semester={k: sem[k] for k in ("id", "start", "end")},
                       updatedAt=store.get_meta(con, "updated_at"),
                       courseCount=len(active), missingTimetableCount=missing)
            if not active:
                out.update(status="no_timetable", warnings=["학사 앱에 등록된 활성 과목이 없습니다. 시간표를 먼저 설정하세요."])
                return out
            if not sem["start"] or not sem["end"]:
                out.update(status="incomplete", warnings=["학기 시작·종료일이 없어 실제 수업 회차를 확인할 수 없습니다. 학사 앱에서 학기를 설정하세요."])
                return out
            if not sem["start"] <= selected.isoformat() <= sem["end"]:
                out.update(status="outside_semester", warnings=["선택한 날짜는 저장된 시간표의 학기 기간 밖입니다."])
                return out
            pmap = service.period_map(con)
            blocks = [b for c in active for b in _course_blocks(c, selected, pmap)]
            # 서로 다른 과목에서 완전히 같은 시간·장소가 나오더라도 식사 계산에는 한 구간이다.
            unique = {(b["start"], b["end"], b["building"], b["state"]): b for b in blocks}
            out["blocks"] = [unique[k] for k in sorted(unique)]
            out["status"] = "incomplete" if missing else "ready"
            if missing:
                out["warnings"].append("시간표가 입력되지 않은 과목이 있습니다. 학사 앱에서 시간표를 완성하거나 강의실·시각을 직접 지정하세요.")
            if not cal["available"]:
                out["warnings"].append("학사일정을 읽지 못해 일부 휴업·보강이 반영되지 않았을 수 있습니다.")
            if any(not b["building"] and b["state"] == "scheduled" for b in blocks):
                out["warnings"].append("강의실 정보가 없는 수업이 있습니다. 필요한 위치를 직접 지정하세요.")
            return out
    except Exception:  # DB·파일 오류에 개인 데이터나 경로가 들어갈 수 있으므로 안전한 설명만 반환한다.
        out.update(status="unavailable", blocks=[], warnings=["학사 시간표를 읽을 수 없습니다. 학사 앱의 시간표를 확인한 뒤 다시 시도하세요."])
        return out


__all__ = ["export_context"]
