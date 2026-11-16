"""남은27곳의 새 가격·공유 메뉴 총액·목요일 시간과 기존 자료 보존을 검증한다."""
import json
from datetime import date

import pytest

from meal.hours import check_open
from meal.places import DATA_DIR, effective_menus, effective_price_source, load_places, recommendation_ready
from meal.ranking import Filters, MealContext, rank
from meal.routing import EstimateRouter
from meal.survey import queue

FOLDER = DATA_DIR / "survey/remaining_27_2026-11-12"


@pytest.fixture(scope="module")
def places():
    return {p["id"]: p for p in load_places()}


def test_nine_more_ready_places_preserve_existing_ids_attrs_and_research(places):
    baseline = json.loads((FOLDER / "baseline_places.json").read_text(encoding="utf-8"))
    assert baseline["queue"]["counts"]["total"] == 27
    newly_ready = set()
    for old in baseline["places"]:
        current = places[old["id"]]
        for field in ("id", "source", "sourceId", "availability", "research"):
            assert current.get(field) == old.get(field)
        assert all(current["attrs"][k] == v for k, v in old["attrs"].items())
        if not recommendation_ready(old) and recommendation_ready(current):
            newly_ready.add(old["id"])
    assert newly_ready == {"p_0014", "p_0127", "p_0110", "p_0148", "p_0056", "p_0137", "p_0101", "p_0114", "p_0102"}
    assert sum(recommendation_ready(p) for p in places.values()) == 80
    assert queue()["counts"] == {"total": 18, "menus": 9, "hours": 13, "saved": 0}


@pytest.mark.parametrize("pid,price", [("p_0148", 6000), ("p_0056", 23000), ("p_0137", 18900),
                                      ("p_0101", 37000), ("p_0114", 9000), ("p_0102", 14000),
                                      ("p_0014", 8500), ("p_0127", 8900)])
def test_posted_meal_total_passes_exact_budget_and_is_never_divided(places, pid, price):
    p = places[pid]
    context = MealContext(weekday=3, start=1080, end=None, origin=(p["lat"], p["lng"]), dest=None)
    exact = rank([p], context, Filters(budget_min=price, budget_max=price), router=EstimateRouter())
    assert len(exact.top) == 1 and exact.top[0].price == price
    assert exact.top[0].menu_choices and all(m["price"] == price for m in exact.top[0].menu_choices)
    too_low = rank([p], context, Filters(budget_max=price-1), router=EstimateRouter())
    assert not too_low.top and too_low.excluded["overBudget"] == 1


def test_three_recent_store_visit_sources_supply_only_the_39_readable_prices(places):
    for pid, count in (("p_0022", 34), ("p_0014", 2), ("p_0127", 3)):
        p = places[pid]
        assert len(effective_menus(p)) == count
        assert effective_price_source(p)["kind"] == "naver_blog_in_store"
        assert p["menuObservation"]["priceContext"] == "in_store"
    mirinae = places["p_0022"]
    assert next(m["price"] for m in effective_menus(mirinae) if m["name"] == "오므라이스") == 6500
    assert not recommendation_ready(mirinae) and mirinae["hoursObservation"]["status"] == "unconfirmed"
    assert all(not m["main"] for m in effective_menus(mirinae) if m["name"] in {"공기", "오뎅", "음료수"})


def test_pizza_school_thursday_matches_weekdays_but_the_announced_date_is_closed(places):
    p = places["p_0110"]
    weekdays = p["hoursObservation"]["hours"][0]
    assert weekdays == {"days": list("월화수목금"), "open": "14:00", "close": "22:30", "lastOrder": "22:25"}
    assert check_open(p, 3, arrive=1080, order_at=1083, done=1113, on_date=date(2026, 11, 19)).ok
    assert not check_open(p, 3, arrive=1080, order_at=1083, done=1113, on_date=date(2026, 11, 12)).ok
    assert not check_open(p, 3, arrive=780, order_at=783, done=813, on_date=date(2026, 11, 19)).ok


def test_unknown_prices_and_delivery_prices_stay_unknown(places):
    assert {m["name"] for m in places["p_0113"]["menuObservation"]["menus"] if m["price"] is None} == {
        "꽃게탕", "병어조림", "오리주물럭", "오리로스", "오리탕"}
    for pid in ("p_0132", "p_0049", "p_0013", "p_0116", "p_0147"):
        assert places[pid]["menuObservation"]["priceContext"] == "delivery"
        assert not recommendation_ready(places[pid])
