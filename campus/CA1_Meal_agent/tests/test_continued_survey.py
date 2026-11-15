"""남은41곳 추가 조사: 원본 보존·가격 채널·요일·예산의 실제 데이터 회귀."""
import json

import pytest

from meal.hours import check_open
from meal.places import DATA_DIR, effective_menus, load_places, recommendation_ready
from meal.ranking import Filters, MealContext, rank
from meal.routing import EstimateRouter

FOLDER = DATA_DIR / "survey/continued_2026-11-12"


@pytest.fixture(scope="module")
def places():
    return {p["id"]: p for p in load_places()}


def test_all_existing_ids_attrs_and_research_survive_research_and_meat_portion_changes(places):
    baseline = json.loads((FOLDER / "baseline_places.json").read_text(encoding="utf-8"))["places"]
    report = json.loads((FOLDER / "report.json").read_text(encoding="utf-8"))
    assert (report["reviewed"], report["confirmedPlaces"], report["newReady"], report["remaining"]) == (41, 19, 23, 18)
    assert report["confirmedStoreMenus"] == 13 and report["confirmedHours"] == 9
    new_ready = set()
    for old in baseline:
        p = places[old["id"]]
        for key in ("id", "source", "sourceId", "research", "availability"):
            assert p.get(key) == old.get(key)
        assert all(p["attrs"][key] == value for key, value in old["attrs"].items())
        if recommendation_ready(old):
            assert recommendation_ready(p)
        elif recommendation_ready(p):
            new_ready.add(p["id"])
    assert new_ready == {"p_0039", "p_0131", "p_0023", "p_0050", "p_0145",
                         "p_0159", "p_0105", "p_0109", "p_0129", "p_0117", "p_0157",
                         "p_0055", "p_0108", "p_0138", "p_0148", "p_0056", "p_0137",
                         "p_0101", "p_0114", "p_0102", "p_0014", "p_0127", "p_0110"}


def test_gamjjok_uses_the_whole_existing_rib_menu_price_and_keeps_cheap_sides_out(places):
    p = places["p_0157"]
    context = MealContext(weekday=3, start=1080, end=None, origin=(p["lat"], p["lng"]), dest=None)
    exact = rank([p], context, Filters(budget_max=18000), router=EstimateRouter())
    assert recommendation_ready(p) and len(exact.top) == 1
    assert exact.top[0].price == 18000
    assert exact.top[0].menu_choices == [{"name": "소금 쪽갈비 (350g)", "price": 18000, "main": True}]
    low = rank([p], context, Filters(budget_max=17999), router=EstimateRouter())
    assert not low.top and low.excluded["overBudget"] == 1
    all_ribs = rank([p], context, Filters(budget_max=18500), router=EstimateRouter())
    assert len(all_ribs.top[0].menu_choices) == 4


@pytest.mark.parametrize("pid,price,name,start", [
    ("p_0001", 9900, "수제벌집와인숙성 삼겹살", 720),
    ("p_0055", 10000, "돼지뒷고기", 1080),
    ("p_0108", 11000, "한방양념삼겹살", 1080),
    ("p_0109", 13000, "생삼겹(180g)", 1080),
    ("p_0138", 9900, "주먹구이", 1080),
])
def test_meat_serving_price_is_one_person_budget_without_weight_or_minimum_order_conversion(places, pid, price, name, start):
    p = places[pid]
    context = MealContext(weekday=3, start=start, end=None, origin=(p["lat"], p["lng"]), dest=None)
    result = rank([p], context, Filters(budget_min=price, budget_max=price), router=EstimateRouter())
    assert len(result.top) == 1 and result.top[0].price == price
    assert {"name": name, "price": price, "main": True} in result.top[0].menu_choices
    assert all(m["price"] == price for m in result.top[0].menu_choices)


@pytest.mark.parametrize("pid,minimum", [("p_0055", 10000), ("p_0108", 11000), ("p_0138", 9900)])
def test_meat_restaurants_cannot_pass_budget_using_a_cheap_side(places, pid, minimum):
    p = places[pid]
    context = MealContext(weekday=3, start=1080, end=None, origin=(p["lat"], p["lng"]), dest=None)
    result = rank([p], context, Filters(budget_max=minimum-1), router=EstimateRouter())
    assert not result.top and result.excluded["overBudget"] == 1


def test_meat_policy_keeps_unverified_hours_and_addon_only_pork_out(places):
    p = places["p_0024"]
    assert {m["name"] for m in effective_menus(p) if m["main"]} >= {"삼겹살", "목살", "생오리구이"}
    assert not recommendation_ready(p)  # 고기 가격이 있어도 미확인 시간을 생성하지 않는다.
    assert recommendation_ready(places["p_0101"])
    assert {m["name"] for m in effective_menus(places["p_0101"]) if m["main"]} == {"앞다리(국내산)", "뒷다리(국내산)"}
    assert all(not m["main"] for m in effective_menus(places["p_0109"]) if m["name"].startswith(("주물럭", "육회")))


@pytest.mark.parametrize("pid,price,name", [
    ("p_0039", 5500, "콩나물국밥"), ("p_0131", 7900, "반계탕"),
    ("p_0050", 4300, "순한알밥"), ("p_0159", 3500, "라면"),
])
def test_confirmed_store_meals_pass_their_exact_budget_without_using_delivery_or_side_prices(places, pid, price, name):
    p = places[pid]
    context = MealContext(weekday=3, start=720, end=None, origin=(p["lat"], p["lng"]), dest=None)
    result = rank([p], context, Filters(budget_max=price), router=EstimateRouter())
    assert result.top[0].price == price and result.top[0].menu_choices == [{"name": name, "price": price, "main": True}]
    low = rank([p], context, Filters(budget_max=price-1), router=EstimateRouter())
    assert not low.top and low.excluded["overBudget"] == 1


def test_delivery_prices_and_delivery_provider_hours_stay_unconfirmed(places):
    for pid, count in (("p_0132", 23), ("p_0049", 30)):
        p = places[pid]
        assert len(p["menuObservation"]["menus"]) == count
        assert p["menuObservation"]["priceContext"] == "delivery"
        assert p["menuObservation"]["status"] == "unconfirmed"
        assert not effective_menus(p) and not recommendation_ready(p)
    for pid in ("p_0033", "p_0142", "p_0049"):
        assert not recommendation_ready(places[pid])
        assert any("요기요" in note for note in places[pid]["followupReview"]["reasons"])


def test_new_store_hours_respect_weekly_closure_and_break(places):
    for pid in ("p_0023", "p_0105", "p_0109", "p_0039"):
        assert check_open(places[pid], 3, arrive=720, order_at=723, done=753).ok
        assert check_open(places[pid], 6, arrive=720, order_at=723, done=753).reason == "closed_day"
    assert check_open(places["p_0109"], 3, arrive=900, order_at=903, done=933).reason == "break"
