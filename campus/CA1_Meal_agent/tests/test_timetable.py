import copy
import io
import json
from datetime import datetime, timezone
from urllib.error import HTTPError

import pytest

from meal.timetable import (TimetableError, fetch_attendance, load_buildings, resolve_context,
                            resolve_location, sanitize_attendance)

AT = datetime(2026, 11, 11, 11, 50)
BUILDINGS = [
    {"code": "공7", "name": "공과대 7호관", "aliases": ["공과대학 7호관"], "lat": 35.178227, "lng": 126.9091507},
    {"code": "경영1", "name": "경영대 1호관", "aliases": [], "lat": 35.1768165, "lng": 126.9040362},
    {"code": "자연1", "name": "자연과학대학 1호관", "aliases": [], "lat": 35.1775305, "lng": 126.9098041},
]


def session(start, end, room, *, day="2026-11-11", state="scheduled"):
    return {"date": day, "start": start, "end": end, "room": room, "state": state, "kind": "regular"}


def overview(*sessions):
    return {"semester": {"id": "2026-2", "start": "2026-09-01", "end": "2026-12-20"},
            "current": "2026-2", "courses": [{"excluded": False, "sessions": list(sessions)}]}


def standard():
    return overview(session("11:00", "11:50", "공7-223"), session("13:30", "14:45", "경영1-101"))


def resolve(data=None, **kwargs):
    return resolve_context(standard() if data is None else data, at=kwargs.pop("at", AT), buildings=BUILDINGS, **kwargs)


def test_actual_f3_response_shape_chooses_next_and_previous_class():
    selected = resolve()
    assert selected.context.origin == (35.178227, 126.9091507)
    assert selected.context.dest == (35.1768165, 126.9040362)
    assert selected.context.start == 710 and selected.context.end == 810
    assert selected.origin_source == selected.destination_source == "timetable"
    assert selected.to_dict()["end"] == "13:30"


def test_manual_next_room_overrides_timetable_but_preserves_next_start():
    selected = resolve(next_room="자연1-205")
    assert selected.destination_location.code == "자연1"
    assert selected.destination_source == "manual"
    assert selected.context.end == 810


def test_manual_origin_overrides_previous_class():
    assert resolve(origin="자연1").origin_location.code == "자연1"


def test_manual_mode_without_academic_app():
    selected = resolve_context(None, at=AT, origin="공7", next_room="경영1", next_start="13:20", buildings=BUILDINGS)
    assert selected.context.end == 800
    assert selected.origin_source == selected.destination_source == "manual"


def test_unknown_timetable_room_requires_override_instead_of_dropping_return_leg():
    data = overview(session("11:00", "11:50", "공7-223"), session("13:00", "14:00", "미등록-101"))
    with pytest.raises(TimetableError, match="건물을 찾지"):
        resolve(data)
    assert resolve(data, next_room="경영1").context.dest is not None


def test_canceled_excluded_and_other_date_sessions_are_ignored_makeup_is_used():
    data = standard()
    data["courses"][0]["sessions"] += [session("12:00", "13:00", "자연1", state="canceled"),
                                         session("12:00", "13:00", "자연1", day="2026-11-12")]
    data["courses"].append({"excluded": True, "sessions": [session("12:00", "13:00", "자연1")]})
    makeup = session("12:30", "13:00", "자연1")
    makeup["kind"] = "makeup"
    data["courses"][0]["sessions"].append(makeup)
    selected = resolve(data)
    assert selected.context.end == 750 and selected.destination_location.code == "자연1"


def test_class_in_progress_does_not_delay_meal_start():
    # 2026-11-12: 수업 중이어도 식사 시작을 미루지 않는다. 출발은 지금 듣는 수업 건물.
    data = overview(session("11:00", "12:00", "공7"), session("12:00", "12:50", "자연1"),
                    session("14:00", "15:00", "경영1"))
    selected = resolve(data)
    assert selected.context.start == 710
    assert selected.origin_location.code == "공7"
    assert selected.to_dict()["requestedStart"] == "11:50"
    assert not any("수업 중" in w for w in selected.warnings)
    # 다음 강의실은 지금 이후 시작하는 첫 수업
    assert selected.destination_location.code == "자연1" and selected.context.end == 720


def test_same_start_conflict_requires_manual_destination():
    data = standard()
    data["courses"][0]["sessions"].append(session("13:30", "14:45", "자연1"))
    with pytest.raises(TimetableError, match="겹칩니다"):
        resolve(data)
    assert resolve(data, next_room="공7").destination_source == "manual"


def test_no_next_class_has_no_return_leg_or_deadline():
    selected = resolve(overview(session("11:00", "11:50", "공7")))
    assert selected.context.dest is None and selected.context.end is None
    assert selected.destination_source == "no_next_class"


def test_explicit_no_next_class_is_distinguished_from_auto():
    selected = resolve(no_next_class=True)
    assert selected.destination_source == "manual_none"
    assert selected.context.dest is None and selected.context.end is None


def test_date_outside_semester_needs_origin_and_does_not_invent_weekly_classes():
    selected = resolve(at=datetime(2027, 1, 6, 11, 50), origin="공7")
    assert selected.context.end is None
    assert "학기 기간 밖" in selected.warnings[0]


def test_aware_time_converts_to_korea():
    selected = resolve(at=datetime(2026, 11, 11, 2, 50, tzinfo=timezone.utc))
    assert selected.context.start == 710


def test_missing_timetable_not_misrepresented_as_no_next_class():
    data = standard()
    data["courses"].append({"excluded": False, "timetable": {"meetings": []}, "sessions": []})
    with pytest.raises(TimetableError, match="입력되지 않은 과목"):
        resolve(data)
    selected = resolve(data, origin="공7", next_room="경영1", next_start="13:30")
    assert selected.destination_source == "manual" and selected.warnings


@pytest.mark.parametrize("kwargs", [
    {"no_next_class": True, "next_room": "공7"},
    {"no_next_class": True, "next_start": "13:00"},
    {"next_start": "11:40"},
    {"next_start": "25:00"},
])
def test_invalid_override_rejected(kwargs):
    with pytest.raises(TimetableError):
        resolve(**kwargs)


@pytest.mark.parametrize("value", ["공7", "공7-223", "공7 223호", "공과대학 7호관-223", " 공과대 7호관 "])
def test_room_alias_resolution(value):
    assert resolve_location(value, BUILDINGS).code == "공7"


def test_similar_building_number_not_accidentally_matched():
    with pytest.raises(TimetableError):
        resolve_location("공70-223", BUILDINGS)


@pytest.mark.parametrize("value", [(True, 126), (float("nan"), 126), (95, 126), (35, float("inf")), [35]])
def test_bad_coordinates_rejected(value):
    with pytest.raises(TimetableError):
        resolve_location(value, BUILDINGS)


def test_manual_coordinate_text_and_list_supported():
    assert resolve_location("35.178,126.909", BUILDINGS).coords == (35.178, 126.909)
    assert resolve_location([35.178, 126.909], BUILDINGS).coords == (35.178, 126.909)


def test_daily_context_uses_only_this_date_and_supports_exam():
    data = {"date": "2026-11-11", "examPeriod": True, "blocks": [
        {"kind": "class", "start": "11:00", "end": "11:50", "building": "공7"},
        {"kind": "exam", "start": "13:30", "end": "14:45", "building": "경영1"},
    ]}
    selected = resolve(data)
    assert selected.context.exam_period and selected.destination_location.code == "경영1"
    with pytest.raises(TimetableError, match="날짜가"):
        resolve(data, at=datetime(2026, 11, 12, 11, 50), origin="공7", no_next_class=True)


def test_daily_busy_block_keeps_deadline_without_inventing_a_room():
    data = {"date": "2026-11-11", "blocks": [{"kind": "busy", "start": "13:00", "end": "14:00"}]}
    selected = resolve(data, origin="공7")
    assert selected.context.dest is None and selected.context.end == 780
    assert selected.destination_source == "timetable_busy" and selected.warnings


def test_imported_response_is_sanitized_without_mutating_input():
    data = standard()
    data["courses"][0].update(name="not retained", memo="not retained", grade="not retained")
    data["courses"][0]["sessions"][0]["memo"] = "not retained"
    original = copy.deepcopy(data)
    clean = sanitize_attendance(data)
    assert "not retained" not in json.dumps(clean)
    assert original == data
    assert resolve(clean).context == resolve(data).context


def test_real_master_has_osm_provenance_and_main_building_is_distinct_from_annex():
    buildings = load_buildings()
    assert len(buildings) >= 2
    assert all(b["source"]["url"].startswith("https://www.openstreetmap.org/") for b in buildings)
    assert resolve_location("공7-223", buildings).code == "공7"
    main = resolve_location("공2-100", buildings)
    annex = resolve_location("공2별관-100", buildings)
    assert main.code == "공2" and annex.code == "공2별관"
    assert main.coords != annex.coords
    assert next(b for b in buildings if b["code"] == "공2")["source"]["osmId"] == 15057693


@pytest.mark.parametrize("url", ["https://127.0.0.1:8020", "http://example.com", "http://127.0.0.1:8020/secret",
                                "http://user:pass@127.0.0.1:8020", "http://127.0.0.1:8020?url=http://example.com"])
def test_fetch_only_local_api(url):
    with pytest.raises(TimetableError):
        fetch_attendance(url)


def test_fetch_403_reports_auth_without_bypassing(monkeypatch):
    class Opener:
        def open(self, request, timeout):
            raise HTTPError(request.full_url, 403, "Forbidden", {}, None)
    monkeypatch.setattr("meal.timetable.build_opener", lambda *args: Opener())
    with pytest.raises(TimetableError, match="인증이 필요"):
        fetch_attendance()


def test_fetch_reads_current_semester_default_and_strips_extra_fields(monkeypatch):
    calls = []
    data = standard()
    data["courses"][0]["name"] = "private title"
    class Opener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            return io.BytesIO(json.dumps(data).encode())
    monkeypatch.setattr("meal.timetable.build_opener", lambda *args: Opener())
    result = fetch_attendance()
    assert calls == ["http://127.0.0.1:8020/api/attendance/summary"]
    assert "private title" not in json.dumps(result)
