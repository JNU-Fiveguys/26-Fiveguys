"""시간표 가져오기 → 목적지 선택 → 추천·경로 메타데이터의 로컬 API 통합 검사."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from meal.api import app
from meal.places import SAMPLES_DIR, check_dirs
from meal.routing import EstimateRouter, WalkRoute

EXAMPLE = Path(__file__).resolve().parent.parent / "data" / "examples" / "attendance_summary.json"


class KnownRoute:
    """라우터 자체의 도로망 테스트와 분리해 API의 구간 합산·출처 전달을 검증한다."""
    def route(self, origin, dest, **kwargs):
        return WalkRoute(distance_m=350, duration_min=5, source="osm", connector_m=12,
                         path=(origin, dest))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("meal.service.load_router", lambda *args: KnownRoute())
    samples = check_dirs([SAMPLES_DIR]).places
    monkeypatch.setattr("meal.service.load_places", lambda include_samples=False: samples if include_samples else [])
    with TestClient(app) as c:
        yield c


@pytest.fixture
def request_body():
    return {"at": "2026-11-11T11:50:00+09:00", "includeSamples": True,
            "timetable": json.loads(EXAMPLE.read_text(encoding="utf-8"))}


def cards(result):
    return [c for group in ("candidates", "more", "tight", "cafeteria") for c in result[group]]


def test_f3_import_selects_next_class_and_serializes_both_osm_legs(client, request_body):
    request_body["timetable"]["courses"][0]["name"] = "PRIVATE_COURSE_TITLE"
    response = client.post("/v1/meal/recommend", json=request_body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert response.headers["cache-control"] == "no-store"
    assert result["context"]["origin"]["code"] == "공7"
    assert result["context"]["destination"]["code"] == "경영1"
    assert result["context"]["destinationSource"] == "timetable"
    assert result["context"]["end"] == "13:30"
    assert result["sampleMode"] is True and result["routeMode"] == "osm"
    assert result["routing"]["osmLegs"] == 2 * len(cards(result))
    assert result["routing"]["estimatedLegs"] == 0
    assert "PRIVATE_COURSE_TITLE" not in response.text
    for c in cards(result):
        assert c["totalWalkMin"] == 10
        assert c["breakdown"]["walkTo"] == c["breakdown"]["walkBack"] == 5
        assert c["needMin"] == sum(c["breakdown"].values())
        for leg in c["walkRoutes"].values():
            assert leg["source"] == "osm"
            assert leg["durationMin"] == 5 and leg["distanceM"] == 350
            assert leg["connectorM"] == 12 and len(leg["path"]) == 2
            assert leg["fallbackReason"] is None


def test_direct_classroom_overrides_import_but_keeps_timetable_deadline(client, request_body):
    request_body["nextRoom"] = "자연1-205"
    response = client.post("/v1/meal/recommend", json=request_body)
    assert response.status_code == 200, response.text
    context = response.json()["context"]
    assert context["destination"]["code"] == "자연1"
    assert context["destinationSource"] == "manual"
    assert context["end"] == "13:30"


def test_full_manual_input_works_without_timetable(client):
    response = client.post("/v1/meal/recommend", json={
        "at": "2026-11-11T11:50:00+09:00", "origin": "공7", "nextRoom": "경영1",
        "nextStart": "13:40", "includeSamples": True, "autoTimetable": False,
    })
    assert response.status_code == 200, response.text
    context = response.json()["context"]
    assert context["originSource"] == context["destinationSource"] == "manual"
    assert context["end"] == "13:40"


def test_unknown_timetable_room_is_422_until_user_overrides_it(client, request_body):
    request_body["timetable"]["courses"][1]["sessions"][0]["room"] = "미등록건물-101"
    response = client.post("/v1/meal/recommend", json=request_body)
    assert response.status_code == 422
    assert "건물을 찾지" in response.json()["detail"]
    request_body["nextRoom"] = "경영1"
    fixed = client.post("/v1/meal/recommend", json=request_body)
    assert fixed.status_code == 200
    assert fixed.json()["context"]["destinationSource"] == "manual"


@pytest.mark.parametrize("explicit", [False, True])
def test_last_class_and_explicit_no_next_class_drop_return_leg(client, request_body, explicit):
    if explicit:
        request_body["noNextClass"] = True
    else:
        request_body["timetable"]["courses"] = request_body["timetable"]["courses"][:1]
    response = client.post("/v1/meal/recommend", json=request_body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["context"]["destination"] is None and result["context"]["end"] is None
    assert result["context"]["destinationSource"] == ("manual_none" if explicit else "no_next_class")
    for c in cards(result):
        assert c["totalWalkMin"] == 5 and c["walkRoutes"]["back"] is None
        assert c["breakdown"]["walkBack"] == 0


def test_osm_failure_is_labelled_fallback_in_cards_and_response(client, request_body, monkeypatch):
    monkeypatch.setattr("meal.service.load_router", lambda *args: EstimateRouter("network_missing"))
    response = client.post("/v1/meal/recommend", json=request_body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["routeMode"] == "osm"  # 요청 모드와 실제 각 구간의 출처를 따로 보존한다.
    assert result["routing"]["osmLegs"] == 0
    assert result["routing"]["estimatedLegs"] == 2 * len(cards(result))
    assert any("대체" in warning for warning in result["warnings"])
    for c in cards(result):
        assert c["walkRoutes"]["to"]["fallbackReason"] == "network_missing"
        assert c["walkRoutes"]["back"]["source"] == "estimate"


def test_explicit_estimate_mode_bypasses_osm_loader(client, request_body, monkeypatch):
    def forbidden_loader(*args):
        pytest.fail("명시 직선 비교 모드가 OSM을 로드하면 안 됩니다.")
    monkeypatch.setattr("meal.service.load_router", forbidden_loader)
    request_body["routeMode"] = "estimate"
    response = client.post("/v1/meal/recommend", json=request_body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["routing"]["osmLegs"] == 0
    assert all(c["walkRoutes"]["to"]["fallbackReason"] == "estimate_requested" for c in cards(result))
    assert not any("찾지 못해" in warning for warning in result["warnings"])


def test_samples_are_opt_in_and_empty_real_data_is_explained(client, request_body, monkeypatch):
    observed = []
    def sample_only(*, include_samples=False):
        observed.append(include_samples)
        return check_dirs([SAMPLES_DIR]).places if include_samples else []
    monkeypatch.setattr("meal.service.load_places", sample_only)
    request_body.pop("includeSamples")
    response = client.post("/v1/meal/recommend", json=request_body)
    assert response.status_code == 200
    assert observed == [False]
    assert response.json()["sampleMode"] is False
    assert cards(response.json()) == []
    assert any("추천용 식당 JSON이 없습니다" in warning for warning in response.json()["warnings"])
    request_body["includeSamples"] = True
    sample = client.post("/v1/meal/recommend", json=request_body)
    assert sample.status_code == 200 and cards(sample.json())
    assert any("가짜 데이터" in warning for warning in sample.json()["warnings"])


def test_imported_daily_context_preserves_canceled_state_and_exam_flag(client):
    response = client.post("/v1/meal/recommend", json={
        "at": "2026-11-11T11:50:00+09:00", "origin": "공7", "includeSamples": True,
        "timetable": {"date": "2026-11-11", "examPeriod": True, "blocks": [
            {"kind": "class", "start": "12:00", "end": "13:00", "building": "자연1", "state": "canceled"},
            {"kind": "exam", "start": "13:30", "end": "14:45", "building": "경영1"},
        ]},
    })
    assert response.status_code == 200, response.text
    assert response.json()["context"]["end"] == "13:30"
    assert response.json()["context"]["destination"]["code"] == "경영1"


def test_daily_busy_block_keeps_deadline_without_false_no_next_class(client):
    response = client.post("/v1/meal/recommend", json={
        "at": "2026-11-11T11:50:00+09:00", "origin": "공7", "includeSamples": True,
        "timetable": {"date": "2026-11-11", "blocks": [{"kind": "busy", "start": "13:30", "end": "14:45"}]},
    })
    assert response.status_code == 200, response.text
    context = response.json()["context"]
    assert context["end"] == "13:30"
    assert context["destinationSource"] == "timetable_busy"
    assert context["destination"] is None


@pytest.mark.parametrize("change", [
    {"routeMode": "driving"}, {"nextStart": "25:00"}, {"partySize": 1}, {"solo": True},
    {"budget": -1}, {"origin": [95, 126]}, {"noNextClass": True, "nextRoom": "공7"},
    {"unexpected": "field"}, {"timetable": {"date": "2026-11-12", "blocks": []}},
])
def test_bad_requests_return_actionable_422(client, request_body, change):
    request_body.update(change)
    response = client.post("/v1/meal/recommend", json=request_body)
    assert response.status_code == 422
    assert response.json().get("detail")


def test_foreign_browser_origin_cannot_submit_timetable(client, request_body):
    response = client.post("/v1/meal/recommend", json=request_body, headers={"Origin": "https://example.com"})
    assert response.status_code == 403


def test_home_building_list_and_downloadable_example_are_available(client):
    home = client.get("/")
    assert home.status_code == 200 and "출발시간" in home.text
    assert 'type="file"' not in home.text
    buildings = client.get("/v1/buildings")
    assert buildings.status_code == 200
    assert any(b["code"] == "공7" for b in buildings.json()["buildings"])
    sample = client.get("/v1/meal/example-timetable")
    assert sample.status_code == 200 and sample.json()["courses"]
