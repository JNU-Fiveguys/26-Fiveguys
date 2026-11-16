"""네이버 실제 조사 명단의 노출·원본 보존·매장 가격·추천 통합 회귀."""
import copy
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from meal import service
from meal.api import app
from meal.availability import candidate_active
from meal.places import DATA_DIR, effective_menus, load_places, recommendation_ready
from meal.routing import EstimateRouter


SURVEY = DATA_DIR / "survey" / "naver_2026-11-11"


def read(name):
    return json.loads((SURVEY / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def actual_places():
    return {p["id"]: p for p in load_places()}


def test_active_catalog_matches_checked_identity_and_fifteen_minute_osm_walks(actual_places):
    selected = {p["naverPlaceId"]: p for p in read("selected_catalog.json")["places"]}
    audit = read("campus_walk_audit.json")
    audited = {p["naverPlaceId"]: p for p in audit["places"]}
    active = [p for p in actual_places.values() if candidate_active(p)]
    assert len(selected) == len(active) == 98
    assert {p["availability"]["naverPlaceId"] for p in active} == set(selected)
    assert audit["rule"]["maxWalkMin"] == 15
    assert set(p["availability"]["district"] for p in active) == {"정문", "상대", "후문", "예대"}
    for p in active:
        evidence = p["availability"]
        walk = audited[evidence["naverPlaceId"]]
        assert evidence["addressVerified"] and evidence["nearby"]
        assert walk["eligible"] and walk["routeSource"] == "osm" and walk["durationMin"] <= 15
        assert any(r["entranceConfirmed"] and r["source"] == "osm" and r["durationMin"] <= 15
                   for r in walk["routes"])
    # 거리가 완화되어도 사용자가 지정한 제외와 주점·치킨 전용 제외는 유지한다.
    assert set(selected).isdisjoint({"2097663323", "1400747026", "1148897279"})


def test_original_ids_and_researched_values_survive_replacement(actual_places):
    baseline = read("baseline_catalog.json")["places"]
    assert len(baseline) == 100
    assert sum(candidate_active(actual_places[p["id"]]) for p in baseline) == 37
    assert len(set(actual_places) - {p["id"] for p in baseline}) == 61
    for old in baseline:
        current = actual_places[old["id"]]
        for field in ("id", "source", "sourceId", "research"):
            if field in old:
                assert current[field] == old[field]
        # 확인한 신규 필드는 채울 수 있지만 이미 조사한 값은 덮어쓰지 않는다.
        for key, value in old.get("attrs", {}).items():
            assert current["attrs"][key] == value


def test_ready_catalog_has_new_verified_meals_without_invented_attributes(actual_places):
    ready = {pid for pid, p in actual_places.items() if recommendation_ready(p)}
    assert len(ready) == 80
    assert {"p_0089", "p_0120", "p_0153", "p_0155"} <= ready
    assert ready.isdisjoint({"p_0040", "p_0100"})  # 미재확인 / 메뉴 가격 변동(null)
    for p in actual_places.values():
        assert not {"soloOk", "seats"} & set(p.get("attrs", {}))
        assert not {"soloOk", "seats"} & set(p.get("research", {}).get("unknownFields", []))
        assert "verifiedBy" not in p.get("attrs", {}) and "mealType" not in p.get("attrs", {})


def test_actual_lunch_excludes_unavailable_incomplete_and_closed_restaurants(actual_places):
    result = service.recommend(at=datetime(2026, 11, 11, 11, 50), origin="공7", next_room="경영1", next_start="13:30")
    cards = result["candidates"] + result["more"] + result["tight"] + result["cafeteria"]
    assert result["catalog"]["active"] == 98 and result["catalog"]["ready"] == 80
    assert result["excluded"]["unavailable"] == 63 and result["excluded"]["noInfo"] == 18
    assert len(result["candidates"]) == 3 and len(cards) >= 41
    assert all(recommendation_ready(actual_places[c["placeId"]]) for c in cards)
    assert "p_0133" not in {c["placeId"] for c in cards}  # 먹고싶은튀김은 이 시각 영업 전
    assert result["excluded"]["closed"] >= 1
    assert result["routing"]["osmLegs"] == len(cards) * 2 and result["routing"]["estimatedLegs"] == 0
    assert all(c["mealTimeEstimated"] and c["breakdown"]["eat"] == 30 for c in cards)
    assert any("30분" in w for w in result["warnings"])
    assert not any("좌석" in w or "혼밥" in w for w in result["warnings"])


def test_confirmation_conflict_blocks_a_previously_ready_baseline_record():
    place = copy.deepcopy(next(p for p in read("baseline_catalog.json")["places"] if p["id"] == "p_0040"))
    assert recommendation_ready(place)
    place["research"]["facts"]["hours"]["status"] = "conflict"
    result = service.recommend(at=datetime(2026, 11, 11, 11, 50), origin="공7", no_next_class=True,
                               places=[place], router=EstimateRouter())
    assert result["catalog"]["ready"] == 0 and result["excluded"]["noInfo"] == 1
    assert not result["candidates"]


def test_default_api_hides_archived_places_but_keeps_history_retrievable(actual_places):
    with TestClient(app) as client:
        active = client.get("/v1/meal/places", params={"limit": 100}).json()
        ready = client.get("/v1/meal/places", params={"ready": True, "limit": 100}).json()
        archive = client.get("/v1/meal/places", params={"status": "all", "limit": 100}).json()
        assert active["total"] == 98 and ready["total"] == 80 and archive["total"] == 161
        assert "p_0040" not in {p["id"] for p in active["items"]}
        assert {p["id"] for p in ready["items"]} == {pid for pid, p in actual_places.items() if recommendation_ready(p)}
        detail = client.get("/v1/meal/places/p_0040").json()
        assert not detail["ready"] and detail["availability"]["status"] == "excluded"
        assert detail["attrs"]["menus"] and detail["research"]["sources"]


def test_actual_menu_uses_store_price_and_keeps_delivery_only_observation_separate(actual_places):
    store = next(p for p in actual_places.values() if p.get("availability", {}).get("naverPlaceId") == "1984446498")
    assert next(m for m in effective_menus(store) if m["name"] == "깐풍기")["price"] == 20000
    for pid in ("p_0068", "p_0078"):
        place = actual_places[pid]
        assert place["menuObservation"]["priceContext"] == "delivery"
        assert service.get_place(pid)["priceSource"]["kind"] == "attrs"
        assert effective_menus(place) == place["attrs"]["menus"]


def test_actual_announced_closure_is_excluded_on_the_requested_date(actual_places):
    chef = actual_places["p_0089"]
    assert chef["hoursObservation"]["closedDates"] == ["2026-11-19"]
    options = dict(origin=(chef["lat"], chef["lng"]), no_next_class=True, places=[chef], router=EstimateRouter())
    closed = service.recommend(at=datetime(2026, 11, 19, 12), **options)
    next_day = service.recommend(at=datetime(2026, 11, 20, 12), **options)
    assert not closed["candidates"] and closed["excluded"]["closed"] == 1
    assert len(next_day["candidates"]) == 1
