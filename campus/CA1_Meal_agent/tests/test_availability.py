"""지도 재확인 결과는 ID·과거 메뉴조사를 보존하면서 후보 노출을 제어한다."""
import copy
import csv
import json
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from meal import service
from meal.api import app
from meal.availability import availability_verified, candidate_active
from meal.import_places import import_places
from meal.places import SAMPLES_DIR, _check_rules, _validator, check_dirs, recommendation_ready
from meal.ranking import MealContext, rank
from meal.research import merge_record
from meal.routing import EstimateRouter

TODAY = date(2026, 11, 11)
CONTEXT = MealContext(2, 720, None, (35.1765, 126.9095), None)


def evidence(status="listed", **changes):
    return {"status": status, "provider": "naver", "checkedAt": "2026-11-11",
            "sourceUrl": "https://pcmap.place.naver.com/restaurant/123456/home", "naverPlaceId": "123456",
            "observedName": "확인한 밥집", "observedAddress": "광주 북구 용봉로 77",
            "district": "정문", "addressVerified": True, "nearby": True,
            "reason": "동일 주소의 전남대 정문 식당으로 지도 등록 확인", **changes}


def observation(context="in_store"):
    return {"sourceUrl": "https://pcmap.place.naver.com/restaurant/123456/menu/list",
            "checkedAt": "2026-11-11", "priceContext": context,
            "menus": [{"name": "새 메뉴", "price": 9000, "priceText": "9,000원", "main": True},
                      {"name": "시가 메뉴", "price": None, "priceText": "변동"}], "note": "메뉴 탭 관찰"}


def place(pid="p_0001", status="listed", **changes):
    value = json.loads((SAMPLES_DIR / "s_01.json").read_text(encoding="utf-8"))
    value.update(id=pid, source="semas", sourceId=pid, sourceStatus="active", address="광주 북구 용봉로 77")
    value["availability"] = evidence(status, **changes)
    return value


def errors(value):
    schema = list(_validator().iter_errors(value))
    if schema:
        return [e.message for e in schema]
    return [i.message for i in _check_rules(value, value["id"] + ".json", False, TODAY) if i.level == "error"]


def test_verified_nearby_listing_is_eligible_without_claiming_current_open_status():
    value = place()
    assert not errors(value)
    assert candidate_active(value) and availability_verified(value) and recommendation_ready(value)
    assert rank([value], CONTEXT, router=EstimateRouter()).top


@pytest.mark.parametrize("state", ["excluded", "unconfirmed", "closed"])
def test_non_listed_places_keep_original_attrs_but_are_excluded_before_menu_readiness(state):
    value = place(status=state)
    original = copy.deepcopy(value)
    assert not errors(value)
    assert not recommendation_ready(value)
    result = rank([value], CONTEXT, router=EstimateRouter())
    assert not result.top and result.excluded["unavailable"] == 1 and result.excluded["noInfo"] == 0
    assert value == original


@pytest.mark.parametrize("changes", [
    {"nearby": False}, {"addressVerified": False}, {"naverPlaceId": None},
    {"naverPlaceId": "not-a-place-id"}, {"district": None}, {"observedAddress": ""}, {"observedName": ""},
])
def test_listed_requires_verified_identity_location_and_naver_identifier(changes):
    value = place(**changes)
    assert errors(value)
    assert not candidate_active(value)


def test_search_absence_can_be_saved_without_fabricated_identity_or_closure():
    value = place(status="unconfirmed", sourceUrl="https://map.naver.com/p/search/no-result",
                  naverPlaceId=None, observedName="", observedAddress="", district=None,
                  addressVerified=False, nearby=False, reason="이름·주소 검색에서 동일 매장 확인 불가")
    assert not errors(value)
    assert value["availability"]["status"] != "closed"
    assert not candidate_active(value)


@pytest.mark.parametrize("field", ["availability", "menuObservation"])
def test_observations_cannot_claim_future_check_dates(field):
    value = place()
    value["menuObservation"] = observation()
    value[field]["checkedAt"] = "2026-11-12"
    assert any("미래" in message for message in errors(value))


@pytest.mark.parametrize("context", ["in_store", "delivery", "unknown"])
def test_menu_observation_preserves_prices_without_overwriting_or_promoting_attrs(context):
    value = place()
    value["menuObservation"] = observation(context)
    original_attrs = copy.deepcopy(value["attrs"])
    assert not errors(value)
    assert recommendation_ready(value)
    assert value["attrs"] == original_attrs
    value["attrs"] = {"status": "partial"}
    assert not errors(value)
    assert not recommendation_ready(value)


@pytest.mark.parametrize("price", [-1, 10.5, True, "9000"])
def test_menu_observation_rejects_non_integer_or_negative_prices(price):
    value = place()
    value["menuObservation"] = observation()
    value["menuObservation"]["menus"][0]["price"] = price
    assert errors(value)


def test_legacy_and_sample_records_without_availability_keep_existing_behavior():
    value = place()
    value.pop("availability")
    assert recommendation_ready(value) and candidate_active(value)
    sample = json.loads((SAMPLES_DIR / "s_01.json").read_text(encoding="utf-8"))
    assert recommendation_ready(sample) and candidate_active(sample)


def test_default_catalog_all_restaurants_and_counts_exclude_unverified_or_far_records(monkeypatch):
    records = [place("p_0001"), place("p_0002", "unconfirmed", naverPlaceId=None),
               place("p_0003", "excluded", nearby=False, reason="전남대 주변이 아님"), place("p_0004", "closed")]
    records[0]["menuObservation"] = observation()
    monkeypatch.setattr(service, "load_places", lambda **kwargs: records)
    with TestClient(app) as client:
        data = client.get("/v1/meal/places").json()
        assert [item["id"] for item in data["items"]] == ["p_0001"]
        assert data["counts"] == {"total": 4, "active": 1, "ready": 1, "needsSurvey": 0,
                                  "missing": 0, "availabilityExcluded": 3}
        assert data["items"][0]["availability"] == records[0]["availability"]
        assert data["items"][0]["menuObservation"] == records[0]["menuObservation"]
        # status=all is an explicit audit API; the UI's ordinary all-restaurants view uses default active.
        assert client.get("/v1/meal/places", params={"status": "all"}).json()["total"] == 4
        assert client.get("/v1/meal/places/p_0004").json()["ready"] is False


def test_all_excluded_catalog_has_specific_message():
    result = service.recommend(at=datetime(2026, 11, 11, 12), origin="공7", no_next_class=True,
                               places=[place(status="unconfirmed")], router=EstimateRouter())
    assert not result["candidates"] and result["catalog"]["active"] == 0
    assert any("지도" in message and "확인된 후보" in message for message in result["warnings"])


def test_import_regeneration_and_source_reappearance_cannot_restore_audit_exclusion(tmp_path):
    csv_path, directory, registry = tmp_path / "selected.csv", tmp_path / "places", tmp_path / "ids.json"
    headers = ["sourceId", "name", "category", "address", "lat", "lng"]
    def write_rows(ids):
        with csv_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=headers)
            writer.writeheader()
            writer.writerows({"sourceId": sid, "name": "밥집 " + sid, "category": "한식",
                              "address": "광주 북구 용봉로 77", "lat": 35.1765, "lng": 126.9095} for sid in ids)
    write_rows(["A", "B"])
    import_places(csv_path, directory, registry, today=TODAY)
    path = directory / "p_0001.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    attrs = place()["attrs"]
    value.update(attrs=attrs, availability=evidence("excluded", reason="검색되지 않아 후보 제외"),
                 menuObservation=observation())
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    write_rows(["B"])
    import_places(csv_path, directory, registry, today=TODAY)
    write_rows(["A", "B"])
    import_places(csv_path, directory, registry, today=TODAY)
    restored = json.loads(path.read_text(encoding="utf-8"))
    assert restored["sourceStatus"] == "active"
    assert restored["id"] == value["id"] and restored["sourceId"] == value["sourceId"]
    assert restored["attrs"] == attrs and restored["availability"] == value["availability"]
    assert restored["menuObservation"] == value["menuObservation"] and not recommendation_ready(restored)


def test_research_reapplication_keeps_availability_and_observations():
    value = place(status="excluded")
    value["menuObservation"] = observation("delivery")
    record = {"placeId": value["id"], "sourceId": value["sourceId"], "identityStatus": "uncertain",
              "queries": ["이름 주소"], "sources": [], "notes": [], "facts": {
                  key: {"status": "unconfirmed", "sourceIds": [], "note": "추가 확인 필요"}
                  for key in ("menus", "hours")}}
    merged = merge_record(value, record, "2026-11-11")
    assert merged["attrs"] == value["attrs"]
    assert merged["availability"] == value["availability"]
    assert merged["menuObservation"] == value["menuObservation"]
    assert not recommendation_ready(merged) and not errors(merged)


def test_duplicate_verified_naver_place_id_is_rejected_but_archived_match_is_kept(tmp_path):
    folder = tmp_path / "places"
    folder.mkdir()
    first, second = place(), place("p_0002")
    for record in [first, second]:
        (folder / f"{record['id']}.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    assert any("naverPlaceId" in issue.message for issue in check_dirs([folder], today=TODAY).errors)
    second["availability"]["status"] = "excluded"
    (folder / "p_0002.json").write_text(json.dumps(second, ensure_ascii=False), encoding="utf-8")
    assert not check_dirs([folder], today=TODAY).errors
