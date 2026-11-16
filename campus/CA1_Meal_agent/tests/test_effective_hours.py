"""최신 영업시간과 특정 날짜 휴무가 보존된 옛 attrs보다 우선한다."""
import copy
import json
from datetime import date, datetime

import pytest

from meal.hours import check_open
from meal.places import SAMPLES_DIR, effective_hours
from meal.routing import EstimateRouter
from meal.service import recommend


def observed_place():
    p = json.loads((SAMPLES_DIR / "s_01.json").read_text(encoding="utf-8"))
    p.update(id="p_0001", source="team", sourceId="naver:12345")
    p["availability"] = {
        "status": "listed", "provider": "naver", "checkedAt": "2026-11-11",
        "naverPlaceId": "12345", "observedName": p["name"],
        "observedAddress": "광주 북구 우치로 94", "district": "후문",
        "addressVerified": True, "nearby": True,
    }
    p["hoursObservation"] = {
        "status": "confirmed", "checkedAt": "2026-11-11",
        "sourceUrl": "https://pcmap.place.naver.com/restaurant/12345/home",
        "hours": [{"days": list("월화수목금토일"), "open": "11:00", "close": "15:00", "lastOrder": "14:10"}],
        "closedDates": ["2026-11-19"],
    }
    return p


def test_latest_closing_and_last_order_apply_without_overwriting_old_hours():
    p = observed_place()
    original = copy.deepcopy(p)
    assert check_open(p, 2, arrive=840, order_at=851, done=880).reason == "last_order"
    assert check_open(p, 2, arrive=840, order_at=845, done=901).reason == "closing"
    assert check_open(p, 2, arrive=720, order_at=725, done=760).ok
    effective_hours(p)[0]["open"] = "00:00"
    assert p == original


def test_kakao_store_hours_require_same_naver_identity_and_road_building():
    p = observed_place()
    p["hoursObservation"].update(sourceUrl="https://place.map.kakao.com/12345", hoursContext="in_store",
                                 placeSourceUrl="https://pcmap.place.naver.com/restaurant/12345/home",
                                 sourceAddress="전남광주통합특별시 북구 우치로 94 1층")
    original = copy.deepcopy(p)
    assert effective_hours(p) == p["hoursObservation"]["hours"]
    assert check_open(p, 2, arrive=840, order_at=851, done=880).reason == "last_order"
    assert p == original


def test_midnight_last_order_remains_zero_in_the_following_day_segment():
    from meal.places import _hours_errors
    p = observed_place()
    p["hoursObservation"]["hours"] = [
        {"days": list("월화수목금토일"), "open": "16:00", "close": "24:00"},
        {"days": list("월화수목금토일"), "open": "00:00", "close": "01:00", "lastOrder": "00:00"},
    ]
    assert not _hours_errors(p["hoursObservation"]["hours"])
    assert check_open(p, 3, arrive=0, order_at=0, done=30).ok
    assert check_open(p, 3, arrive=0, order_at=1, done=30).reason == "last_order"
    assert _hours_errors([{"days": ["월"], "open": "11:00", "close": "12:00", "lastOrder": "11:00"}])


@pytest.mark.parametrize("change", ["delivery", "unknown", "missing_context", "missing_link", "wrong_identity",
                                    "wrong_address", "prefix_building", "wrong_host", "wrong_path", "http"])
def test_unbound_or_delivery_kakao_hours_cannot_override_store_hours(change):
    p = observed_place()
    h = p["hoursObservation"]
    h.update(sourceUrl="https://place.map.kakao.com/12345", hoursContext="in_store",
             placeSourceUrl="https://pcmap.place.naver.com/restaurant/12345/home",
             sourceAddress="전남광주통합특별시 북구 우치로 94 1층")
    if change in {"delivery", "unknown"}: h["hoursContext"] = change
    elif change == "missing_context": h.pop("hoursContext")
    elif change == "missing_link": h.pop("placeSourceUrl")
    elif change == "wrong_identity": h["placeSourceUrl"] = "https://pcmap.place.naver.com/restaurant/999/home"
    elif change == "wrong_address": h["sourceAddress"] = "광주 북구 자미로 94"
    elif change == "prefix_building": h["sourceAddress"] = "광주 북구 우치로 940"
    elif change == "wrong_host": h["sourceUrl"] = "https://example.com/12345"
    elif change == "wrong_path": h["sourceUrl"] = "https://place.map.kakao.com/search/12345"
    elif change == "http": h["sourceUrl"] = "http://place.map.kakao.com/12345"
    assert effective_hours(p) == p["attrs"]["hours"]


@pytest.mark.parametrize("change", ["wrong_place", "old", "future", "unconfirmed"])
def test_untrusted_observation_cannot_override_hours_or_close_the_store(change):
    p = observed_place()
    h = p["hoursObservation"]
    if change == "wrong_place": h["sourceUrl"] = "https://pcmap.place.naver.com/restaurant/999/home"
    elif change == "old": h["checkedAt"] = "2026-11-10"
    elif change == "future": h["checkedAt"] = "9999-12-31"
    else: h["status"] = "unconfirmed"
    assert effective_hours(p) == p["attrs"]["hours"]
    assert check_open(p, 3, arrive=720, order_at=725, done=760, on_date=date(2026, 11, 19)).ok


def test_specific_closure_flows_from_requested_date_through_recommendation():
    p = observed_place()
    options = dict(origin=(p["lat"], p["lng"]), no_next_class=True, places=[p], router=EstimateRouter())
    closed = recommend(at=datetime(2026, 11, 19, 12), **options)
    open_day = recommend(at=datetime(2026, 11, 20, 12), **options)
    assert not closed["candidates"] and closed["excluded"]["closed"] == 1
    assert len(open_day["candidates"]) == 1


@pytest.mark.parametrize("day,closed", [(2, False), (9, True), (16, False), (23, True), (30, False)])
def test_second_and_fourth_monday_are_calendar_occurrences_not_arbitrary_weeks(day, closed):
    p = observed_place()
    p["hoursObservation"]["monthlyClosed"] = [{"day": "월", "weeks": [2, 4]}]
    st = check_open(p, 0, arrive=720, order_at=725, done=760, on_date=date(2026, 11, day))
    assert st.ok is not closed


def test_monthly_closure_without_a_calendar_date_does_not_assume_open():
    p = observed_place()
    p["hoursObservation"]["monthlyClosed"] = [{"day": "월", "weeks": [2, 4]}]
    assert check_open(p, 0, arrive=720, order_at=725, done=760).reason == "no_info"
