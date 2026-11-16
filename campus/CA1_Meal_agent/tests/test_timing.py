import json
from functools import partial

import pytest

from meal.places import SAMPLES_DIR
from meal.settings import SETTINGS_PATH, Settings, to_hhmm, to_min
from meal.timing import compute, compute_for_place, is_peak, walk_min
from meal.routing import EstimateRouter

compute_for_place = partial(compute_for_place, router=EstimateRouter())

CENTER = (35.1765, 126.9095)
M_PER_DEG_LAT = 111_195  # 위도 1도 ≈ 111.195km


def north(p, meters):
    return (p[0] + meters / M_PER_DEG_LAT, p[1])


def sample(pid):
    return json.loads((SAMPLES_DIR / f"{pid}.json").read_text(encoding="utf-8"))


# ── 정의서 5절 예시 그대로 ───────────────────────────────────

def test_spec_example_need_48_ok_slack_52():
    """공강 100분, 도보 4분, 일반 식당 평시(대기 3 · 식사 30), 돌아가는 도보 6분 → 필요 48분 · 적합 · 여유 52분."""
    t = compute(start=to_min("10:00"), end=to_min("11:40"), walk_to=4, walk_back=6, meal_type="일반")
    assert t.need_min == 48
    assert t.fit == "ok"
    assert t.slack_min == 52
    assert t.breakdown.walk_total == 10
    assert t.to_dict()["totalWalkMin"] == 10
    assert t.breakdown.to_dict() == {"walkTo": 4, "wait": 3, "eat": 30, "walkBack": 6, "buffer": 5}


# ── 판정 경계 ───────────────────────────────────────────────

@pytest.mark.parametrize("gap, fit", [(48, "ok"), (47, "tight"), (43, "tight"), (42, "out")])
def test_fit_boundaries(gap, fit):
    start = to_min("10:00")
    t = compute(start=start, end=start + gap, walk_to=4, walk_back=6, meal_type="일반")
    assert t.need_min == 48
    assert t.fit == fit
    assert t.slack_min == gap - 48


# ── 출발 마감 시각 · 도착 · 식사 끝 ─────────────────────────

def test_leave_by_and_arrive_done():
    # 11:50~13:00 공강, 도보 4 · 돌아가는 도보 7 → 12:48 까지 나오면 13:00 수업 OK
    t = compute(start=to_min("11:00"), end=to_min("13:00"), walk_to=4, walk_back=7, meal_type="간단식")
    assert to_hhmm(t.leave_by) == "12:48"
    assert to_hhmm(t.arrive_at) == "11:04"
    assert to_hhmm(t.done_at) == "11:21"  # 11:04 + 대기 2 + 식사 15
    assert t.to_dict()["leaveBy"] == "12:48"


# ── 피크 대기 ───────────────────────────────────────────────

def test_peak_wait_by_arrival_time():
    off = compute(start=to_min("11:40"), end=to_min("13:30"), walk_to=5, walk_back=5, meal_type="학식")
    on = compute(start=to_min("11:45"), end=to_min("13:30"), walk_to=5, walk_back=5, meal_type="학식")
    assert (off.peak, off.breakdown.wait) == (False, 3)   # 11:45 도착 → 피크 전
    assert (on.peak, on.breakdown.wait) == (True, 10)     # 11:50 도착 → 피크
    assert on.need_min - off.need_min == 7


def test_peak_window_end_is_exclusive():
    assert is_peak(to_min("12:29"))
    assert not is_peak(to_min("12:30"))


# ── 다음 건물 · 끝이 없는 경우 (CA1-R04) ─────────────────────

def test_no_next_building_drops_walk_back_but_keeps_deadline():
    t = compute(start=to_min("17:00"), end=to_min("18:00"), walk_to=4, walk_back=None, meal_type="일반")
    assert t.breakdown.walk_back == 0
    assert t.to_dict()["totalWalkMin"] == 4
    assert t.need_min == 4 + 3 + 30 + 5
    assert to_hhmm(t.leave_by) == "17:55"


def test_open_ended_after_last_block():
    t = compute(start=to_min("18:30"), end=None, walk_to=4, walk_back=None, meal_type="고기·단체")
    assert t.fit == "ok"
    assert t.gap_min is None and t.slack_min is None and t.leave_by is None
    assert t.breakdown.buffer == 0
    assert t.need_min == 4 + 5 + 60


# ── 도보 ────────────────────────────────────────────────────

def test_walk_min_rounds_up():
    # 200m × 1.3 ÷ 70 = 3.71 → 4분
    assert walk_min(CENTER, north(CENTER, 200)) == 4
    # 700m × 1.3 ÷ 70 = 13
    assert walk_min(CENTER, north(CENTER, 700)) == 13


def test_walk_same_place_is_zero():
    assert walk_min(CENTER, CENTER) == 0


def test_settings_override_changes_result():
    base = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    base["walk"]["detourFactor"] = 1.0
    s = Settings.from_dict(base)
    assert walk_min(CENTER, north(CENTER, 700), s) == 10  # 700 ÷ 70


# ── 잘못된 입력 ─────────────────────────────────────────────

def test_unknown_meal_type():
    with pytest.raises(ValueError):
        compute(start=600, end=700, walk_to=1, walk_back=1, meal_type="뷔페")


def test_end_before_start():
    with pytest.raises(ValueError):
        compute(start=700, end=600, walk_to=1, walk_back=1, meal_type="일반")


# ── 샘플 가게로 ─────────────────────────────────────────────

def test_place_without_attrs_returns_none():
    assert compute_for_place(sample("s_06"), start=600, end=700, origin=CENTER, dest=CENTER) is None


def test_far_place_out_on_short_gap_ok_on_long_gap():
    far = sample("s_07")  # 약 1.3km
    short = compute_for_place(far, start=to_min("10:00"), end=to_min("11:00"), origin=CENTER, dest=CENTER)
    long = compute_for_place(far, start=to_min("10:00"), end=to_min("12:00"), origin=CENTER, dest=CENTER)
    assert short.fit == "out"
    assert long.fit == "ok"
    assert short.breakdown.walk_to == short.breakdown.walk_back  # 출발 = 다음 건물 → 같은 길 두 번


def test_near_snack_fits_short_gap():
    t = compute_for_place(sample("s_05"), start=to_min("10:00"), end=to_min("10:40"), origin=CENTER, dest=CENTER)
    assert t.fit == "ok"
