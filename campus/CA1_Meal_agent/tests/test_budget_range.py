"""예산 범위는 실제 식사 메뉴의 가격과 추천 카드에 함께 적용된다."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from meal.api import app
from meal.places import SAMPLES_DIR
from meal.ranking import Filters, MealContext, TasteSummary, rank
from meal.routing import EstimateRouter


BASE = json.loads((SAMPLES_DIR / "s_01.json").read_text(encoding="utf-8"))
CONTEXT = MealContext(weekday=2, start=720, end=None, origin=(35.179, 126.912), dest=None)


def restaurant(pid, *prices, side_price=None):
    place = copy.deepcopy(BASE)
    place["id"] = pid
    place["attrs"]["menus"] = [{"name": f"식사 {i}", "price": p, "main": True}
                               for i, p in enumerate(prices)]
    if side_price is not None:
        place["attrs"]["menus"].append({"name": "곁들임", "price": side_price, "main": False})
    return place


def ranked(places, filters, taste=None):
    return rank(places, CONTEXT, filters, taste, router=EstimateRouter())


def candidates(result):
    return result.top + result.more + result.tight + result.cafeteria


def test_range_includes_both_endpoints_and_excludes_gaps_between_menu_prices():
    places = [restaurant("s_91", 7000), restaurant("s_92", 10000),
              restaurant("s_93", 6500, 11000), restaurant("s_94", 9000)]
    result = ranked(places, Filters(budget_min=7000, budget_max=10000))
    assert {c.place_id for c in candidates(result)} == {"s_91", "s_92", "s_94"}
    assert result.excluded["overBudget"] == 1


def test_range_card_price_and_score_use_only_matching_main_menus_without_mutation():
    place = restaurant("s_91", 4000, 8000, 10000, 22000, side_price=7000)
    original = copy.deepcopy(place)
    result = ranked([place], Filters(budget_min=7000, budget_max=10000),
                    TasteSummary(budget_default=3000))
    candidate = result.top[0]
    assert candidate.price == candidate.to_dict()["repPrice"] == 9000
    assert candidate.features["budget"] == 1.0
    assert place == original


def test_range_does_not_count_side_dish_as_meal():
    result = ranked([restaurant("s_91", 12000, side_price=8000)],
                    Filters(budget_min=7000, budget_max=10000))
    assert not candidates(result)
    assert result.excluded["overBudget"] == 1


@pytest.mark.parametrize("filters,expected", [
    (Filters(budget_min=10000), {"s_92", "s_93"}),
    (Filters(budget_max=10000), {"s_91", "s_92"}),
    (Filters(budget_min=10000, budget_max=10000), {"s_92"}),
    (Filters(budget_min=0, budget_max=0), set()),
    (Filters(), {"s_91", "s_92", "s_93"}),
])
def test_unbounded_equal_and_zero_range_limits(filters, expected):
    result = ranked([restaurant("s_91", 7000), restaurant("s_92", 10000),
                     restaurant("s_93", 15000)], filters)
    assert {c.place_id for c in candidates(result)} == expected


def test_legacy_budget_keeps_existing_cheapest_filter_and_median_price():
    result = ranked([restaurant("s_91", 7000, 7500)], Filters(budget=7000, budget_explicit=True))
    assert result.top[0].price == 7250


@pytest.mark.parametrize("options", [
    {"budget_min": -1}, {"budget_max": -1}, {"budget_min": True},
    {"budget_max": 8000.5}, {"budget_min": 10000, "budget_max": 7000},
    {"budget": 7000, "budget_min": 0},
])
def test_direct_ranking_rejects_invalid_or_ambiguous_ranges(options):
    with pytest.raises(ValueError, match="예산"):
        ranked([BASE], Filters(**options))


@pytest.fixture
def client(monkeypatch):
    places = [restaurant("s_91", 7000, 7500), restaurant("s_92", 10000), restaurant("s_93", 15000)]
    monkeypatch.setattr("meal.service.load_places", lambda **kwargs: places)
    with TestClient(app) as client:
        yield client


def body(**kwargs):
    return {"at": "2026-11-11T12:00:00+09:00", "origin": "공7", "noNextClass": True,
            "autoTimetable": False, "routeMode": "estimate", **kwargs}


@pytest.mark.parametrize("limits,expected", [
    ({"budgetMin": 7500, "budgetMax": 10000}, {"s_91": 7500, "s_92": 10000}),
    ({"budgetMin": 10000}, {"s_92": 10000, "s_93": 15000}),
    ({"budgetMax": 7000}, {"s_91": 7000}),
    ({"budget": 7000}, {"s_91": 7250}),
])
def test_api_range_reaches_ranking_and_serialized_prices(client, limits, expected):
    response = client.post("/v1/meal/recommend", json=body(**limits))
    assert response.status_code == 200, response.text
    result = response.json()
    assert {c["placeId"]: c["repPrice"] for group in ("candidates", "more", "tight", "cafeteria")
            for c in result[group]} == expected


@pytest.mark.parametrize("limits", [
    {"budgetMin": -1}, {"budgetMax": -1}, {"budgetMin": True}, {"budgetMax": 7500.5},
    {"budgetMin": 10000, "budgetMax": 7000}, {"budget": 7000, "budgetMin": 0},
])
def test_api_rejects_invalid_range_before_recommendation(client, limits):
    response = client.post("/v1/meal/recommend", json=body(**limits))
    assert response.status_code == 422
    assert response.json()["detail"]
