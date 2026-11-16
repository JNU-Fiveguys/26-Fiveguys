"""온라인 수업은 이동 목적지 없이 수업 시작 시각을 제약으로 유지한다."""
import copy
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from meal.api import app
from meal.places import SAMPLES_DIR, check_dirs
from meal.routing import WalkRoute
from meal.timetable import TimetableError, resolve_context
from meal.timing import compute_for_place


AT = datetime(2026, 11, 11, 11, 50)
BUILDINGS = [
    {"code": "A", "name": "출발 건물", "aliases": [], "lat": 35.1765, "lng": 126.9095},
    {"code": "B", "name": "다음 건물", "aliases": [], "lat": 35.18, "lng": 126.91},
]
PLACE = next(p for p in check_dirs([SAMPLES_DIR]).places if p["id"] == "s_01")


def session(start, end, room, state="scheduled"):
    return {"date": "2026-11-11", "start": start, "end": end,
            "room": room, "state": state, "kind": "regular"}


def overview(*sessions):
    return {"semester": {"id": "2026-2", "start": "2026-09-01", "end": "2026-12-20"},
            "courses": [{"excluded": False, "sessions": list(sessions)}]}


def standard(room="온라인"):
    return overview(session("11:00", "11:50", "A"), session("13:30", "14:45", room))


def resolve(data=None, **kwargs):
    return resolve_context(standard() if data is None else data, at=kwargs.pop("at", AT),
                           buildings=BUILDINGS, **kwargs)


class KnownRoute:
    def __init__(self):
        self.calls = []

    def route(self, origin, dest, **kwargs):
        self.calls.append((origin, dest))
        return WalkRoute(350, 5, "osm", path=(origin, dest))


@pytest.mark.parametrize("label", ["온라인", " 온라인 수업 ", "온라인 강의", "비대면", "비대면 수업",
                                   "비대면강의", "원격", "원격 수업", "원격강의", "ONLINE", "Remote"])
def test_explicit_online_labels_preserve_next_start_without_a_destination(label):
    selected = resolve(standard(label))
    assert selected.context.end == 13 * 60 + 30
    assert selected.context.dest is None and selected.destination_location is None
    assert selected.destination_source == "online"
    assert selected.to_dict()["end"] == "13:30"
    assert selected.to_dict()["destination"] is None
    assert any("건물 이동 없이" in warning for warning in selected.warnings)


@pytest.mark.parametrize("label", ["온라인교육관", "비대면 아님", "원격지원센터", "온라인/대면", "온라인 (장소 미정)"])
def test_ambiguous_or_building_like_labels_are_not_guessed_to_be_online(label):
    with pytest.raises(TimetableError, match="건물을 찾지"):
        resolve(standard(label))


def test_previous_online_class_requires_origin_instead_of_reusing_an_older_building():
    data = overview(session("09:00", "10:00", "A"), session("11:00", "11:50", "온라인"),
                    session("13:30", "14:45", "B"))
    with pytest.raises(TimetableError, match="온라인 수업.*출발 건물을 직접"):
        resolve(data)
    selected = resolve(data, origin="A")
    assert selected.origin_source == "manual"
    assert selected.destination_location.code == "B"


def test_an_online_class_in_progress_does_not_delay_meal_start():
    data = overview(session("11:00", "12:45", "온라인"), session("13:30", "14:45", "B"))
    selected = resolve(data, origin="A")
    assert selected.context.start == 11 * 60 + 50
    assert selected.context.end == 13 * 60 + 30
    assert not any("수업 중" in warning for warning in selected.warnings)


def test_manual_room_and_start_override_the_online_timetable():
    selected = resolve(next_room="B", next_start="13:40")
    assert selected.destination_source == "manual"
    assert selected.destination_location.code == "B"
    assert selected.context.end == 13 * 60 + 40
    assert not any("건물 이동 없이" in warning for warning in selected.warnings)
    assert any("시간표의 다음 수업보다 늦습니다" in warning for warning in selected.warnings)
    assert resolve(next_room="B").context.end == 13 * 60 + 30


def test_manual_start_alone_can_override_online_class_time():
    selected = resolve(next_start="13:20")
    assert selected.destination_source == "online" and selected.context.dest is None
    assert selected.context.end == 13 * 60 + 20


def test_explicit_no_next_class_is_distinct_from_an_online_deadline():
    selected = resolve(no_next_class=True)
    assert selected.destination_source == "manual_none"
    assert selected.context.end is None and selected.context.dest is None


def test_simultaneous_online_and_physical_classes_require_an_override():
    data = standard()
    data["courses"][0]["sessions"].append(session("13:30", "14:45", "B"))
    with pytest.raises(TimetableError, match="같은 시각"):
        resolve(data)
    assert resolve(data, next_room="B").destination_source == "manual"


def test_multiple_explicit_online_labels_at_the_same_time_keep_one_deadline():
    data = standard()
    data["courses"][0]["sessions"].append(session("13:30", "14:45", "비대면"))
    assert resolve(data).destination_source == "online"


def test_canceled_online_class_does_not_hide_the_next_physical_class():
    data = overview(session("11:00", "11:50", "A"), session("12:30", "13:00", "온라인", "canceled"),
                    session("13:30", "14:45", "B"))
    assert resolve(data).destination_location.code == "B"


def test_academic_cli_blocks_accept_online_classroom_labels():
    data = {"date": "2026-11-11", "blocks": [
        {"kind": "class", "start": "11:00", "end": "11:50", "building": "A"},
        {"kind": "class", "start": "13:30", "end": "14:45", "building": "온라인"},
    ]}
    selected = resolve(data)
    assert selected.destination_source == "online"
    assert selected.to_dict()["end"] == "13:30"


def test_online_deadline_still_excludes_meals_that_take_too_long():
    selected = resolve(overview(session("11:00", "11:50", "A"), session("12:00", "13:00", "온라인")))
    context, router = selected.context, KnownRoute()
    timed = compute_for_place(PLACE, start=context.start, end=context.end, origin=context.origin, dest=context.dest, router=router)
    unlimited = compute_for_place(PLACE, start=context.start, end=None, origin=context.origin, dest=None, router=KnownRoute())
    assert timed.gap_min == 10 and timed.fit == "out"
    assert timed.breakdown.walk_back == 0 and timed.route_back is None
    assert len(router.calls) == 1
    assert unlimited.fit == "ok" and unlimited.gap_min is None


def test_api_returns_one_osm_leg_and_preserves_online_class_time(monkeypatch):
    router = KnownRoute()
    monkeypatch.setattr("meal.service.load_router", lambda *args: router)
    monkeypatch.setattr("meal.service.load_places", lambda **kwargs: [copy.deepcopy(PLACE)])
    with TestClient(app) as client:
        response = client.post("/v1/meal/recommend", json={
            "at": "2026-11-11T11:50:00+09:00", "origin": [35.1765, 126.9095],
            "timetable": standard(), "includeSamples": True,
        })
    assert response.status_code == 200
    result = response.json()
    assert result["context"]["destinationSource"] == "online"
    assert result["context"]["destination"] is None and result["context"]["end"] == "13:30"
    candidate = result["candidates"][0]
    assert candidate["gapMin"] == 100 and candidate["slackMin"] is not None
    assert candidate["breakdown"]["walkBack"] == 0 and candidate["walkRoutes"]["back"] is None
    assert len(router.calls) == 1 and result["routing"]["osmLegs"] == 1
    assert any("건물 이동 없이" in warning for warning in result["warnings"])
