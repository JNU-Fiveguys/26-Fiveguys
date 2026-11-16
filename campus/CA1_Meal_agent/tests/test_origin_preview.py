"""Automatic origin placeholders follow the same saved timetable as recommendations."""
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from meal import academic, service
from meal.api import app


DAY = "2026-11-11"
AT = DAY + "T11:50:00+09:00"


def block(start, end, building, state="scheduled"):
    return {"kind": "class", "start": start, "end": end, "building": building, "state": state}


@pytest.fixture
def exported():
    return {"version": 1, "date": DAY,
            "semester": {"id": "2026-2", "start": "2026-09-01", "end": "2026-12-20"},
            "blocks": [block("09:00", "10:00", "자연1-205"),
                       block("11:00", "11:50", "공7-223"),
                       block("13:30", "14:45", "경영1-101")],
            "status": "ready", "warnings": [], "updatedAt": AT,
            "courseCount": 3, "missingTimetableCount": 0}


@pytest.fixture
def client(monkeypatch, exported):
    def read(day, mode):
        assert day == date.fromisoformat(exported["date"])
        return json.dumps(exported, ensure_ascii=False).encode("utf-8")
    monkeypatch.setattr(academic, "_run_cli", read)
    monkeypatch.setattr(service, "load_places", lambda **kwargs: [])
    with TestClient(app) as client:
        yield client


def preview(client, at=AT, **params):
    response = client.get("/v1/meal/timetable", params={"at": at, **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_preview_matches_actual_recommendation_origin_without_exporting_rooms(client):
    summary = preview(client)
    recommendation = client.post("/v1/meal/recommend", json={"at": AT, "routeMode": "estimate"})
    assert recommendation.status_code == 200
    assert summary["originPreview"] == recommendation.json()["context"]["origin"]
    assert summary["originPreview"]["code"] == "공7"
    assert summary["originHint"] == ""
    assert "blocks" not in summary
    assert "공7-223" not in json.dumps(summary, ensure_ascii=False)
    assert "경영1" not in json.dumps(summary, ensure_ascii=False)


def test_status_only_request_remains_location_free(client):
    summary = client.get("/v1/meal/timetable", params={"date": DAY}).json()
    assert "originPreview" not in summary and "originHint" not in summary


def test_canceled_class_cannot_replace_previous_origin(client, exported):
    exported["blocks"][1]["state"] = "canceled"
    assert preview(client)["originPreview"]["code"] == "자연1"


def test_unknown_next_classroom_does_not_hide_known_origin(client, exported):
    exported["blocks"][-1]["building"] = ""
    assert preview(client)["originPreview"]["code"] == "공7"


@pytest.mark.parametrize("room,hint", [("온라인", "온라인 수업"), ("", "건물이 없거나"),
                                      ("알 수 없는 건물", "건물을 찾지")])
def test_unresolved_previous_location_returns_reason_instead_of_old_building(client, exported, room, hint):
    exported["blocks"][1]["building"] = room
    summary = preview(client)
    assert summary["originPreview"] is None and hint in summary["originHint"]


def test_overlapping_previous_rooms_require_manual_origin(client, exported):
    exported["blocks"].append(block("11:00", "11:50", "자연1-205"))
    summary = preview(client)
    assert summary["originPreview"] is None and "겹칩니다" in summary["originHint"]


def test_no_previous_class_requests_current_location(client):
    summary = preview(client, DAY + "T08:00:00+09:00")
    assert summary["originPreview"] is None and "직전 수업 위치가 없습니다" in summary["originHint"]


def test_in_progress_class_uses_current_class_origin(client, exported):
    # 2026-11-12: 수업 중이어도 시작을 미루지 않고, 지금 듣는 수업(11:00–11:50 공7)에서 출발한다.
    exported["blocks"].append(block("11:50", "12:30", "자연1-205"))
    summary = preview(client, DAY + "T11:20:00+09:00")
    assert summary["originPreview"]["code"] == "공7"
    assert "수업 중" not in summary["originHint"]


@pytest.mark.parametrize("at", [DAY + "T02:50:00Z", DAY + "T11:50:00"])
def test_utc_and_naive_time_follow_korean_date_rules(client, at):
    assert preview(client, at, date=DAY)["originPreview"]["code"] == "공7"


def test_seconds_rounding_across_midnight_reads_correct_day(client, exported):
    exported["date"] = "2026-11-12"
    summary = preview(client, DAY + "T23:59:30+09:00")
    assert summary["date"] == "2026-11-12" and summary["originPreview"] is None


@pytest.mark.parametrize("params", [
    {"at": "not-a-time"}, {"at": "2026-11-31T11:50:00+09:00"},
    {"at": AT, "date": "2026-11-12"}, {"at": AT, "date": "2026-02-30"},
    {"at": "9999-12-31T23:59:59+09:00"},  # Minute rounding crosses datetime.max.
    {"at": "9999-12-31T20:00:00+00:00"},  # KST conversion overflows.
    {"at": "0001-01-01T00:00:00+14:00"},  # KST conversion underflows.
])
def test_invalid_or_mismatched_dates_are_rejected_before_read(client, monkeypatch, params):
    def unexpected_read(day):
        pytest.fail("Invalid date must not read the academic store")
    monkeypatch.setattr(academic, "read_timetable", unexpected_read)
    assert client.get("/v1/meal/timetable", params=params).status_code == 422


def test_incomplete_timetable_has_no_misleading_origin(client, exported):
    exported.update(status="incomplete", missingTimetableCount=1)
    summary = preview(client)
    assert summary["originPreview"] is None and "일부 비어" in summary["originHint"]


def test_each_preview_reads_latest_saved_location(client, exported):
    assert preview(client)["originPreview"]["code"] == "공7"
    exported["blocks"][1]["building"] = "공2-100"
    assert preview(client)["originPreview"]["code"] == "공2"
