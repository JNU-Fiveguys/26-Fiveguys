from datetime import date

import pytest
from fastapi.testclient import TestClient

from meal.academic import AcademicSnapshot
from meal.api import create_embedded_app
from meal.timeline import daily_timeline

DAY = date(2026, 11, 16)


def snapshot(blocks, status="ready"):
    return AcademicSnapshot({"version": 1, "date": DAY.isoformat(), "blocks": blocks},
                            {"date": DAY.isoformat(), "status": status, "warnings": []})


def block(start, end, room="공7", kind="class", state="scheduled"):
    return {"start": start, "end": end, "building": room, "kind": kind, "state": state}


def test_overlapping_blocks_and_exams_do_not_become_free_time():
    data = daily_timeline(snapshot([
        block("09:00", "10:30"), block("10:00", "11:00"),
        block("11:10", "11:50", kind="exam"), block("13:30", "14:30", "경영1"),
    ]), DAY)
    assert [(s["start"], s["end"]) for s in data["slots"]] == [("11:50", "13:30"), ("14:30", "20:00")]
    assert data["slots"][0]["enabled"] and data["slots"][-1]["lastOfDay"]


def test_canceled_class_does_not_block_the_gap():
    data = daily_timeline(snapshot([block("09:00", "10:00"), block("11:00", "12:00", state="canceled"),
                                    block("13:30", "14:30", "경영1")]), DAY)
    assert data["slots"][0]["start"] == "10:00" and data["slots"][0]["end"] == "13:30"
    assert len(data["classes"]) == 2


def test_unknown_next_building_is_not_offered_as_a_safe_gap():
    data = daily_timeline(snapshot([block("09:00", "11:00"), block("13:30", "14:30", "없는건물")]), DAY)
    assert data["slots"][0]["enabled"] is False
    assert data["slots"][0]["reason"]


def test_evening_class_deadline_is_not_truncated_to_the_display_axis():
    data = daily_timeline(snapshot([block("17:00", "19:00"), block("21:00", "22:00", "경영1")]), DAY)
    assert [(s["start"], s["end"]) for s in data["slots"]] == [("19:00", "21:00")]
    assert data["slots"][0]["enabled"] and not data["slots"][0]["lastOfDay"]


@pytest.mark.parametrize("status", ["incomplete", "no_timetable", "outside_semester", "unavailable"])
def test_unusable_timetable_never_offers_free_time(status):
    result = daily_timeline(snapshot([block("09:00", "11:00")], status), DAY)
    assert result["classes"] == result["slots"] == []


def test_embedded_timeline_uses_injected_reader_and_keeps_status_api_private(tmp_path):
    calls = []
    def reader(day):
        calls.append(day)
        return snapshot([block("09:00", "11:00"), block("13:30", "14:30", "경영1")])
    app = create_embedded_app(timetable_reader=reader, entries_dir=tmp_path)
    with TestClient(app) as client:
        response = client.get("/v1/meal/timeline?date=2026-11-16")
        assert response.status_code == 200 and response.json()["slots"][0]["enabled"]
        assert response.headers["cache-control"] == "no-store"
        assert calls == [DAY]
        assert "blocks" not in client.get("/v1/meal/timetable?date=2026-11-16").json()
        assert client.get("/v1/meal/timeline?date=invalid").status_code == 422
        app.state.public = True
        before = len(calls)
        public = client.get("/v1/meal/timeline?date=2026-11-16").json()
        assert public["classes"] == public["slots"] == [] and len(calls) == before


def test_clean_checkout_has_team_map_and_main_recommendations_without_pr6_csv(tmp_path):
    def forbidden(day):
        pytest.fail("manual requests must not read the timetable")
    app = create_embedded_app(timetable_reader=forbidden, entries_dir=tmp_path)
    with TestClient(app) as client:
        assert client.get("/ui").status_code == 200
        assert client.get("/assets/meal-experience.js").status_code == 200
        assert client.get("/v1/meal/places?ready=true").json()["total"] > 0
        response = client.post("/v1/meal/recommend", json={"at": "2026-11-16T11:50:00+09:00",
            "autoTimetable": False, "origin": "공7", "nextRoom": "경영1", "nextStart": "13:30"})
        assert response.status_code == 200, response.text
        assert len(response.json()["candidates"]) == 3
        assert client.get("/assets/meal-basemap.js").status_code == 200
        basemap = client.get("/v1/meal/basemap")
        assert basemap.status_code == 200
        data = basemap.json()
        assert "OpenStreetMap" in data["source"]
        assert len(data["buildings"]) > 1000 and len(data["green"]) > 100 and data["water"]
        assert set(data) == {"source", "major", "minor", "path", "buildings", "green", "water"}
        assert response.json()["routing"]["osmLegs"] > 0


def test_missing_map_returns_recoverable_error(tmp_path, monkeypatch):
    monkeypatch.setattr("meal.api.ROOT", tmp_path)
    app = create_embedded_app(timetable_reader=lambda day: None, entries_dir=tmp_path)
    with TestClient(app) as client:
        response = client.get("/v1/meal/basemap")
        assert response.status_code == 503
        assert str(tmp_path) not in response.text
