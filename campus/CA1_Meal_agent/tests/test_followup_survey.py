"""추가 조사 값·원본 보존 및 시간 한정 매장 가격의 통합 검증."""
import copy
import json
from datetime import date, datetime

import pytest

from meal.hours import check_open
from meal.places import DATA_DIR, _check_rules, _validator, effective_menus, load_places, recommendation_ready
from meal.routing import EstimateRouter
from meal.service import recommend

FOLDER = DATA_DIR / "survey/followup_2026-11-12"


@pytest.fixture(scope="module")
def places():
    return {p["id"]: p for p in load_places()}


def test_all_55_reviews_preserve_original_ids_research_and_existing_attrs(places):
    baseline = json.loads((FOLDER / "baseline_places.json").read_text(encoding="utf-8"))["places"]
    report = json.loads((FOLDER / "report.json").read_text(encoding="utf-8"))
    assert report["reviewed"] == 55 and report["newReady"] == 37 and report["remaining"] == 18
    assert len({r["id"] for r in report["places"]}) == 55
    assert sum(recommendation_ready(p) for p in baseline) == 42
    for old in baseline:
        current = places[old["id"]]
        for field in ("id", "source", "sourceId", "research", "availability"):
            assert current.get(field) == old.get(field)
        assert all(current["attrs"][key] == value for key, value in old["attrs"].items())
        if recommendation_ready(old): assert recommendation_ready(current)
    for row in report["places"]:
        p = places[row["id"]]
        assert row["ready"] == recommendation_ready(p)
        assert p["followupReview"]["reasons"] and p["followupReview"]["sources"]


def test_photo_store_price_is_used_and_unreadable_prices_stay_null(places):
    p = places["p_0158"]
    assert next(m["price"] for m in effective_menus(p) if m["name"] == "곰탕") == 11000
    unreadable = [m for m in places["p_0123"]["menuObservation"]["menus"] if m["price"] is None]
    assert {m["name"] for m in unreadable} == {"부대찌개", "순두부찌개", "육개장"}
    assert all(m["price"] is not None for m in effective_menus(places["p_0123"]))


def test_noodle_menu_uses_6500_won_meal_and_keeps_sides_out_of_budget(places):
    from meal.ranking import Filters, MealContext, rank
    p = places["p_0112"]
    menus = effective_menus(p)
    assert recommendation_ready(p) and len(menus) == 54
    assert sum(m["main"] for m in menus) == 38
    assert next(m["price"] for m in menus if m["name"] == "생면국수") == 6500
    assert next(m["price"] for m in menus if m["name"] == "국치정식") == 10300
    result = rank([p], MealContext(weekday=3, start=720, end=None,
                  origin=(p["lat"], p["lng"]), dest=None), Filters(budget_max=6500), router=EstimateRouter())
    assert result.top[0].price == 6500
    assert result.top[0].menu_choices == [{"name": "생면국수", "price": 6500, "main": True}]
    too_low = rank([p], MealContext(weekday=3, start=720, end=None,
                   origin=(p["lat"], p["lng"]), dest=None), Filters(budget_max=6000), router=EstimateRouter())
    assert not too_low.top and too_low.excluded["overBudget"] == 1


def test_pickup_jokbal_menu_uses_single_meals_and_excludes_sides_from_budget(places):
    from meal.ranking import Filters, MealContext, rank
    p = places["p_0135"]
    menus = effective_menus(p)
    mains = [m for m in menus if m["main"]]
    assert recommendation_ready(p) and len(menus) == 50
    assert {m["name"] for m in mains} == {"쟁반국수(1인)", "족발볶음밥(1인)", "비빔국수(1인)", "마왕국수(1인)"}
    assert {m["price"] for m in mains} == {8000}
    context = MealContext(weekday=3, start=1080, end=None, origin=(p["lat"], p["lng"]), dest=None)
    allowed = rank([p], context, Filters(budget_max=8000), router=EstimateRouter())
    assert allowed.top[0].price == 8000 and len(allowed.top[0].menu_choices) == 4
    low = rank([p], context, Filters(budget_max=7999), router=EstimateRouter())
    assert not low.top and low.excluded["overBudget"] == 1


def test_delivery_booking_prices_do_not_become_store_budget_prices(places):
    p = places["p_0116"]
    assert len(p["menuObservation"]["menus"]) == 122
    assert p["menuObservation"]["priceContext"] == "delivery"
    assert p["menuObservation"]["status"] == "unconfirmed"
    assert not effective_menus(p) and not recommendation_ready(p)


def test_shared_pizza_uses_the_posted_whole_menu_price(places):
    p = places["p_0137"]
    menus = effective_menus(p)
    assert len(menus) == 75 and all(m["price"] > 0 and m["main"] for m in menus)
    assert recommendation_ready(p)


@pytest.mark.parametrize("weekday,minute,expected", [(5, 60, True), (5, 1080, False), (6, 60, False), (6, 1080, True)])
def test_overnight_hours_belong_to_following_calendar_day(places, weekday, minute, expected):
    p = next(p for p in places.values() if p.get("availability", {}).get("naverPlaceId") == "1992635726")
    assert check_open(p, weekday, arrive=minute, order_at=minute+3, done=minute+33).ok is expected


@pytest.mark.parametrize("hour,minute,price", [(14, 56, 19900), (14, 57, 22900), (15, 0, 22900)])
def test_lunch_dinner_price_switches_at_order_time_including_wait(places, hour, minute, price):
    p = places["p_0154"]
    result = recommend(at=datetime(2026, 11, 12, hour, minute), origin=(p["lat"], p["lng"]),
                       no_next_class=True, places=[p], router=EstimateRouter())
    card = result["candidates"][0]
    assert card["repPrice"] == price
    assert len(card["effectiveMenus"]) == 1
    assert card["effectiveMenus"][0]["price"] == price


def test_dinner_cannot_use_lunch_or_child_price_to_pass_budget(places):
    from meal.ranking import Filters, MealContext, rank
    p = places["p_0154"]
    result = rank([p], MealContext(weekday=3, start=900, end=None, origin=(p["lat"], p["lng"]), dest=None),
                  Filters(budget_max=19900), router=EstimateRouter())
    assert not result.top and result.excluded["overBudget"] == 1
    stale = copy.deepcopy(p)
    stale["menuObservation"]["status"] = "unconfirmed"
    assert not recommendation_ready(stale)  # 시간 조건을 지운 가격으로 돌아가지 않는다.


def test_order_windows_are_copied_and_invalid_bounds_are_rejected(places):
    p = copy.deepcopy(places["p_0154"])
    menus = effective_menus(p)
    menus[0]["orderWindow"]["start"] = "16:00"
    assert p["menuObservation"]["menus"][0]["orderWindow"]["start"] == "11:00"
    p["menuObservation"]["menus"][0]["orderWindow"]["end"] = "11:00"
    assert not list(_validator().iter_errors(p))
    assert any("orderWindow" in i.message for i in _check_rules(p, "p_0154.json", False, date(2026, 11, 12)))
