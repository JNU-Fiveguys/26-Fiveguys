import copy
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from meal.api import app
from meal.places import SAMPLES_DIR, check_dirs
from meal.routing import EstimateRouter
from meal import service


@pytest.fixture
def records():
    sample = copy.deepcopy(check_dirs([SAMPLES_DIR]).places[0])
    a = {k: v for k, v in sample.items() if k != "attrs"}
    a.update(id="p_0001", name="가까운 밥집", source="semas", sourceId="A", sourceStatus="active", address="용봉로 1")
    b = copy.deepcopy(sample)
    b.update(id="p_0002", name="나무 식당", source="semas", sourceId="B", sourceStatus="active", address="대학로 2")
    c = copy.deepcopy(b)
    c.update(id="p_0003", name="다시 확인할 식당", sourceId="C", sourceStatus="missing")
    return [a, b, c, sample]


@pytest.fixture
def client(monkeypatch, records):
    monkeypatch.setattr(service, "load_places", lambda include_samples=False: records if include_samples else records[:3])
    monkeypatch.setattr(service, "load_router", lambda *args: EstimateRouter())
    with TestClient(app) as client:
        yield client


def test_catalog_shows_unreviewed_real_places_without_samples(client):
    result = client.get("/v1/meal/places").json()
    assert result["counts"] == {"total": 3, "active": 2, "ready": 1, "needsSurvey": 1, "missing": 1, "availabilityExcluded": 0}
    assert [p["id"] for p in result["items"]] == ["p_0001", "p_0002"]
    assert result["items"][0]["ready"] is False
    assert "attrs" not in result["items"][1]


def test_catalog_search_pagination_and_readiness(client):
    second = client.get("/v1/meal/places", params={"limit": 1, "offset": 1}).json()
    assert second["total"] == 2 and second["items"][0]["id"] == "p_0002"
    by_address = client.get("/v1/meal/places", params={"query": "용봉로"}).json()
    assert [p["id"] for p in by_address["items"]] == ["p_0001"]
    ready = client.get("/v1/meal/places", params={"ready": "true"}).json()
    assert [p["id"] for p in ready["items"]] == ["p_0002"]
    missing = client.get("/v1/meal/places", params={"status": "missing"}).json()
    assert missing["items"][0]["ready"] is False
    assert missing["items"][0]["id"] == "p_0003"


@pytest.mark.parametrize("params", [{"offset": -1}, {"limit": 101}, {"status": "closed"}])
def test_catalog_rejects_invalid_pagination_and_status(client, params):
    assert client.get("/v1/meal/places", params=params).status_code == 422


def test_real_places_connect_to_recommender_but_unreviewed_and_missing_are_excluded(client):
    result = client.post("/v1/meal/recommend", json={
        "at": "2026-11-11T11:50:00+09:00", "origin": "공7", "nextRoom": "경영1", "nextStart": "14:00", "autoTimetable": False
    }).json()
    assert [c["placeId"] for c in result["candidates"]] == ["p_0002"]
    assert result["excluded"]["noInfo"] == 1 and result["excluded"]["unavailable"] == 1
    assert result["catalog"]["ready"] == 1


def test_all_unreviewed_real_data_has_actionable_message(records):
    result = service.recommend(at=datetime(2026, 11, 11, 11, 50), origin="공7", no_next_class=True,
                               places=[records[0]], router=EstimateRouter())
    assert not result["candidates"]
    assert result["catalog"]["needsSurvey"] == 1
    assert any("조사가 아직 없어" in warning for warning in result["warnings"])
