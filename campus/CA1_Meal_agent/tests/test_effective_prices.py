"""저장 attrs 보존과 별개로 검증된 최신 매장 가격을 일관되게 계산한다."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from meal import service
from meal.api import app
from meal.places import SAMPLES_DIR, effective_menus, effective_price_source, meal_menus, rep_price
from meal.ranking import Filters, MealContext, rank
from meal.routing import EstimateRouter

CONTEXT = MealContext(weekday=2, start=720, end=None, origin=(35.179, 126.912), dest=None)


def observed_place():
    p = json.loads((SAMPLES_DIR / "s_01.json").read_text(encoding="utf-8"))
    p.update(id="p_0001", source="team", sourceId="naver:12345", sourceStatus="active")
    p["attrs"]["menus"] = [{"name": "2인 식사", "price": 25000, "main": True}]
    p["availability"] = {"status": "listed", "provider": "naver", "checkedAt": "2026-11-11",
                         "sourceUrl": "https://map.naver.com/p/entry/place/12345", "naverPlaceId": "12345",
                         "observedName": p["name"], "observedAddress": "광주 북구 우치로 94", "district": "후문",
                         "addressVerified": True, "nearby": True, "reason": "동일 매장 확인"}
    p["menuObservation"] = {
        "status": "confirmed", "priceContext": "in_store", "checkedAt": "2026-11-11",
        "sourceUrl": "https://pcmap.place.naver.com/restaurant/12345/menu/list",
        "menus": [{"name": "2인 식사 (매장)", "price": 20000, "main": True, "priceContext": "in_store"},
                  {"name": "2인 식사 (배달)", "price": 25000, "main": True, "priceContext": "delivery"},
                  {"name": "가격 구분 불명", "price": 18000, "main": True, "priceContext": "unknown"},
                  {"name": "기본 반찬", "price": 0, "main": False},
                  {"name": "시가 식사", "price": None, "main": True},
                  {"name": "양념 추가", "price": 1000, "main": False}],
    }
    return p


def test_latest_store_price_wins_over_old_and_delivery_prices_without_mutating_any_source():
    p = observed_place()
    before = copy.deepcopy(p)
    result = rank([p], CONTEXT, Filters(budget=20000, budget_explicit=True), router=EstimateRouter())
    assert len(result.top) == 1
    assert result.top[0].price == rep_price(p) == 20000
    assert result.top[0].features["budget"] == 1.0
    assert meal_menus(p) == [{"name": "2인 식사 (매장)", "price": 20000, "main": True}]
    assert effective_price_source(p)["kind"] == "naver_in_store"
    assert p == before and p["attrs"]["menus"][0]["price"] == 25000


def test_order_menu_source_keeps_the_actual_url_with_an_explicit_same_place_link():
    p = observed_place()
    url = "https://m.booking.naver.com/order/bizes/1104944/items/5748897?theme=place"
    p["menuObservation"].update(sourceUrl=url, placeSourceUrl="https://pcmap.place.naver.com/restaurant/12345/menu/list")
    assert rep_price(p) == 20000
    assert effective_price_source(p)["sourceUrl"] == url


def test_kakao_store_menu_source_uses_same_place_address_and_keeps_real_source_url():
    p = observed_place()
    p["menuObservation"].update(sourceUrl="https://place.map.kakao.com/12345",
                                placeSourceUrl="https://pcmap.place.naver.com/restaurant/12345/home",
                                sourceAddress="전남광주통합특별시 북구 우치로 94 1층")
    original = copy.deepcopy(p)
    assert rep_price(p) == 20000
    assert effective_price_source(p) == {"kind": "kakao_in_store", "priceContext": "in_store",
                                         "sourceUrl": "https://place.map.kakao.com/12345", "checkedAt": "2026-11-11"}
    assert p == original


@pytest.mark.parametrize("change", ["delivery", "unknown", "missing_address", "wrong_address", "missing_link"])
def test_kakao_delivery_or_unbound_menu_prices_cannot_enter_store_budget(change):
    p = observed_place()
    m = p["menuObservation"]
    m.update(sourceUrl="https://place.map.kakao.com/12345", sourceAddress="광주 북구 우치로 94",
             placeSourceUrl="https://pcmap.place.naver.com/restaurant/12345/home")
    if change in {"delivery", "unknown"}: m["priceContext"] = change
    elif change == "missing_address": m.pop("sourceAddress")
    elif change == "wrong_address": m["sourceAddress"] = "광주 북구 우치로 940"
    elif change == "missing_link": m.pop("placeSourceUrl")
    assert rep_price(p) == 25000


def test_known_store_prices_are_visible_without_inventing_a_single_person_meal():
    from meal.places import recommendation_ready
    p = observed_place()
    p["menuObservation"]["menus"] = [{"name": "공유 피자 한 판", "price": 19900, "main": False}]
    assert effective_menus(p) == [{"name": "공유 피자 한 판", "price": 19900, "main": False}]
    assert meal_menus(p) == [] and rep_price(p) is None
    assert not recommendation_ready(p)


def test_recent_naver_visit_source_uses_the_real_blog_url_and_preserves_the_old_attrs():
    p = observed_place()
    m = p["menuObservation"]
    m.update(sourceUrl="https://blog.naver.com/visit_writer/224356779893",
             placeSourceUrl="https://pcmap.place.naver.com/restaurant/12345/home",
             sourceAddress="광주 북구 우치로 94 1층", sourcePublishedAt="2026-07-24")
    before = copy.deepcopy(p)
    assert rep_price(p) == 20000 and p == before
    assert effective_price_source(p)["kind"] == "naver_blog_in_store"
    assert effective_price_source(p)["sourceUrl"] == m["sourceUrl"]


@pytest.mark.parametrize("change", ["delivery", "unknown", "missing_address", "wrong_address", "missing_link",
                                   "wrong_place", "missing_date", "old_date", "future_date", "wrong_host", "wrong_path"])
def test_blog_menu_prices_need_a_recent_dated_same_store_visit(change):
    p = observed_place()
    m = p["menuObservation"]
    m.update(sourceUrl="https://blog.naver.com/visit_writer/224356779893",
             placeSourceUrl="https://pcmap.place.naver.com/restaurant/12345/home",
             sourceAddress="광주 북구 우치로 94", sourcePublishedAt="2026-07-24")
    if change in {"delivery", "unknown"}: m["priceContext"] = change
    elif change == "missing_address": m.pop("sourceAddress")
    elif change == "wrong_address": m["sourceAddress"] = "광주 북구 우치로 940"
    elif change == "missing_link": m.pop("placeSourceUrl")
    elif change == "wrong_place": m["placeSourceUrl"] = "https://pcmap.place.naver.com/restaurant/999/home"
    elif change == "missing_date": m.pop("sourcePublishedAt")
    elif change == "old_date": m["sourcePublishedAt"] = "2019-05-23"
    elif change == "future_date": m["sourcePublishedAt"] = "2026-11-12"
    elif change == "wrong_host": m["sourceUrl"] = "https://blog.naver.com.example.com/visit_writer/224356779893"
    elif change == "wrong_path": m["sourceUrl"] = "https://blog.naver.com/visit_writer"
    assert rep_price(p) == 25000


@pytest.mark.parametrize("change", ["missing_link", "wrong_place", "wrong_host", "wrong_path", "http", "delivery"])
def test_unbound_or_non_store_order_sources_cannot_override_prices(change):
    p = observed_place()
    m = p["menuObservation"]
    m.update(sourceUrl="https://m.booking.naver.com/order/bizes/1104944/items/5748897", placeSourceUrl="https://pcmap.place.naver.com/restaurant/12345/menu/list")
    if change == "missing_link": m.pop("placeSourceUrl")
    elif change == "wrong_place": m["placeSourceUrl"] = "https://pcmap.place.naver.com/restaurant/999/menu/list"
    elif change == "wrong_host": m["sourceUrl"] = m["sourceUrl"].replace("m.booking.naver.com", "example.com")
    elif change == "wrong_path": m["sourceUrl"] = "https://m.booking.naver.com/booking/1104944"
    elif change == "http": m["sourceUrl"] = m["sourceUrl"].replace("https:", "http:")
    else: m["priceContext"] = "delivery"
    assert rep_price(p) == 25000


@pytest.mark.parametrize("filters,expected_count", [
    (Filters(budget_min=20000, budget_max=20000), 1),
    (Filters(budget_min=25000, budget_max=25000), 0),
    (Filters(budget_min=18000, budget_max=18000), 0),
    (Filters(budget_max=1000), 0),
    (Filters(budget=19000, budget_explicit=True), 0),
])
def test_all_budget_paths_use_only_positive_priced_store_main_menus(filters, expected_count):
    result = rank([observed_place()], CONTEXT, filters, router=EstimateRouter())
    assert len(result.top) == expected_count
    if not expected_count:
        assert result.excluded["overBudget"] == 1


@pytest.mark.parametrize("change", ["delivery", "unknown", "unconfirmed", "wrong_place", "old_date", "invalid_date", "future", "unverified_address"])
def test_untrusted_or_non_store_observations_never_override_existing_prices(change):
    p = observed_place()
    if change in {"delivery", "unknown"}: p["menuObservation"]["priceContext"] = change
    elif change == "unconfirmed": p["menuObservation"]["status"] = "unconfirmed"
    elif change == "wrong_place": p["menuObservation"]["sourceUrl"] = "https://pcmap.place.naver.com/restaurant/999/menu/list"
    elif change == "old_date": p["menuObservation"]["checkedAt"] = "2026-11-10"
    elif change == "invalid_date": p["menuObservation"]["checkedAt"] = "not-a-date"
    elif change == "future": p["menuObservation"]["checkedAt"] = "9999-12-31"
    elif change == "unverified_address": p["availability"]["addressVerified"] = False
    assert rep_price(p) == 25000
    assert effective_price_source(p)["kind"] == "attrs"
    assert p["attrs"]["menus"] == [{"name": "2인 식사", "price": 25000, "main": True}]


def test_same_store_price_keeps_same_rank_score_and_budget_features():
    current = observed_place()
    current["attrs"]["menus"][0]["price"] = 20000
    previous = copy.deepcopy(current)
    previous.pop("menuObservation")
    old = rank([previous], CONTEXT, Filters(budget=20000), router=EstimateRouter()).top[0]
    new = rank([current], CONTEXT, Filters(budget=20000), router=EstimateRouter()).top[0]
    assert old.price == new.price == 20000
    assert old.features == new.features and old.score == new.score


def test_menu_observation_older_than_human_verification_does_not_replace_newer_saved_price():
    p = observed_place()
    p["availability"]["checkedAt"] = "2026-11-09"
    p["menuObservation"]["checkedAt"] = "2026-11-09"
    p["attrs"]["verifiedAt"] = "2026-11-10"
    assert rep_price(p) == 25000 and effective_price_source(p)["kind"] == "attrs"


def test_effective_menu_result_does_not_share_mutable_objects_with_saved_attrs_or_observations():
    p = observed_place()
    before = copy.deepcopy(p)
    menus = effective_menus(p)
    assert {m["price"] for m in menus} == {1000, 20000}
    menus[0]["price"] = 1
    assert p == before


def test_legacy_null_and_zero_price_side_dishes_do_not_become_a_meal_budget():
    p = observed_place()
    p.pop("menuObservation")
    p["attrs"]["menus"] += [{"name": "물", "price": 0, "main": False},
                            {"name": "미확인", "price": None, "main": True}]
    assert rep_price(p) == 25000
    assert len(meal_menus(p)) == 1


def test_detail_catalog_and_recommendation_expose_the_same_effective_store_price(monkeypatch):
    p = observed_place()
    original = copy.deepcopy(p)
    monkeypatch.setattr(service, "load_places", lambda **kwargs: [p])
    with TestClient(app) as client:
        detail = client.get("/v1/meal/places/p_0001").json()
        catalog = client.get("/v1/meal/places").json()["items"][0]
        response = client.post("/v1/meal/recommend", json={
            "at": "2026-11-11T12:00:00+09:00", "autoTimetable": False, "origin": "공7",
            "noNextClass": True, "routeMode": "estimate", "budgetMin": 20000, "budgetMax": 20000,
        })
        assert response.status_code == 200, response.text
        card = response.json()["candidates"][0]
    assert detail["attrs"]["menus"][0]["price"] == 25000
    assert detail["repPrice"] == catalog["repPrice"] == card["repPrice"] == 20000
    assert detail["priceSource"] == catalog["priceSource"] == card["priceSource"]
    assert card["effectiveMenus"] == [{"name": "2인 식사 (매장)", "price": 20000, "main": True}]
    assert all(m["price"] != 25000 for m in detail["effectiveMenus"])
    assert p == original
