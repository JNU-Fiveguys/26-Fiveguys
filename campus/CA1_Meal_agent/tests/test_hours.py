import json

import pytest

from meal.hours import check_open, check_timing
from meal.places import SAMPLES_DIR
from meal.settings import to_hhmm, to_min
from meal.timing import compute_for_place

MON, WED, SAT, SUN = 0, 2, 5, 6
CENTER = (35.1765, 126.9095)


def sample(pid):
    return json.loads((SAMPLES_DIR / f"{pid}.json").read_text(encoding="utf-8"))


def at(place, day, arrive, order=None, done=None):
    a = to_min(arrive)
    o = to_min(order) if order else a
    d = to_min(done) if done else o
    return check_open(place, day, arrive=a, order_at=o, done=d)


# ── 기본 · 휴무 ─────────────────────────────────────────────

def test_open_normal():
    st = at(sample("s_01"), WED, "12:00", "12:03", "12:33")
    assert st.ok and st.reason == "open"
    assert to_hhmm(st.open_until) == "21:00"


def test_sunday_closed():
    assert at(sample("s_03"), SUN, "12:00").reason == "closed_day"
    assert at(sample("s_03"), SAT, "12:00").ok


def test_weekend_closed_for_weekday_only_place():
    assert at(sample("s_02"), SAT, "12:00").reason == "closed_day"


def test_weekday_as_korean_letter():
    assert at(sample("s_03"), "일", "12:00").reason == "closed_day"


# ── 브레이크 (정의서 수용 기준: 15:00~17:00 브레이크인 곳이 15:30 추천에 나오지 않는다) ──

def test_break_time_excluded():
    st = at(sample("s_02"), WED, "15:30", "15:33", "16:03")
    assert (st.ok, st.reason) == (False, "break")
    assert to_hhmm(st.opens_at) == "17:00"


def test_meal_running_into_break_excluded():
    st = at(sample("s_02"), WED, "14:30", "14:38", "15:08")
    assert (st.ok, st.reason) == (False, "break_soon")


def test_meal_finishing_right_at_break_ok():
    st = at(sample("s_02"), WED, "14:20", "14:28", "14:58")
    assert st.ok
    assert to_hhmm(st.open_until) == "15:00"


def test_after_break_ok():
    assert at(sample("s_02"), WED, "17:00", "17:03", "17:33").ok


# ── 라스트오더 · 마감 ───────────────────────────────────────

def test_last_order():
    assert at(sample("s_02"), WED, "20:00", "20:03", "20:33").ok          # 주문 20:03 ≤ 20:30
    assert at(sample("s_02"), WED, "20:28", "20:36", "21:00").reason == "last_order"


@pytest.mark.parametrize("last_order", ["14:30", "15:00", "16:00", "17:00", "21:01"])
def test_break_row_rejects_last_order_outside_final_segment(last_order):
    p = sample("s_02")
    p["attrs"]["hours"][0]["lastOrder"] = last_order
    # 직접 check_open 을 부르는 경우에도 점심 마감이 조용히 무시되면 안 된다.
    with pytest.raises(ValueError, match="hours 행을 나눠"):
        at(p, WED, "14:35", "14:37", "14:52")


def test_separate_lunch_and_dinner_last_orders():
    p = sample("s_02")
    days = p["attrs"]["hours"][0]["days"]
    p["attrs"]["hours"] = [
        {"days": days, "open": "11:00", "close": "15:00", "lastOrder": "14:30"},
        {"days": days, "open": "17:00", "close": "21:00", "lastOrder": "20:30"},
    ]
    assert at(p, WED, "14:28", "14:30", "14:45").ok
    assert at(p, WED, "14:35", "14:37", "14:52").reason == "last_order"
    assert at(p, WED, "17:10", "17:12", "17:27").ok
    assert at(p, WED, "20:28", "20:30", "20:45").ok
    assert at(p, WED, "20:29", "20:31", "20:46").reason == "last_order"


def test_break_row_last_order_can_be_just_after_reopening():
    p = sample("s_02")
    p["attrs"]["hours"][0]["lastOrder"] = "17:01"
    assert at(p, WED, "17:00", "17:01", "17:16").ok
    assert at(p, WED, "17:00", "17:02", "17:17").reason == "last_order"


def test_closing_during_meal():
    st = at(sample("s_01"), WED, "20:40", "20:43", "21:13")
    assert (st.ok, st.reason) == (False, "closing")


# ── 영업 전 · 영업 사이 (학식 두 구간) ──────────────────────

def test_not_yet_open_dinner_only():
    st = at(sample("s_04"), WED, "12:00", "12:05", "13:05")
    assert (st.ok, st.reason) == (False, "not_yet")
    assert to_hhmm(st.opens_at) == "17:00"


def test_cafeteria_lunch_gap_dinner():
    caf = sample("s_08")
    assert at(caf, MON, "12:00", "12:03", "12:23").ok
    gap = at(caf, MON, "14:00")
    assert (gap.reason, to_hhmm(gap.opens_at)) == ("closed", "17:00")
    assert at(caf, MON, "17:10", "17:13", "17:33").ok
    assert at(caf, MON, "19:00").reason == "closed"
    assert at(caf, MON, "19:00").opens_at is None


# ── 정보 없음 · 잘못된 입력 ─────────────────────────────────

def test_no_info():
    st = at(sample("s_06"), WED, "12:00")
    assert (st.ok, st.reason, st.text) == (False, "no_info", "영업시간 정보 없음")


def test_bad_order_of_times():
    with pytest.raises(ValueError):
        check_open(sample("s_01"), WED, arrive=700, order_at=690, done=720)


def test_bad_weekday():
    with pytest.raises(ValueError):
        at(sample("s_01"), 7, "12:00")


# ── timing 과 함께 ──────────────────────────────────────────

def test_with_timing_lunch_slot():
    """수요일 11:50~13:30 공강: 저녁만 여는 고기집(s_04)은 빠지고 한식(s_01)은 남는다."""
    def status(pid):
        p = sample(pid)
        t = compute_for_place(p, start=to_min("11:50"), end=to_min("13:30"), origin=CENTER, dest=CENTER)
        return check_timing(p, WED, t)

    assert status("s_01").ok
    assert status("s_04").reason == "not_yet"
    assert status("s_08").ok


def test_to_dict():
    d = at(sample("s_02"), WED, "15:30").to_dict()
    assert d == {"ok": False, "reason": "break", "text": "브레이크 타임", "openUntil": None, "opensAt": "17:00"}
