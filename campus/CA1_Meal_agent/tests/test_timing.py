"""정의서 CA1 5절 · 4-가 · 4-다 의 예시 값을 그대로 고정한다 (코드 규칙 8)."""

import pytest

from meal.timing import (
    Fit,
    MealType,
    TimingConfig,
    distance_m,
    evaluate,
    find_meal_slots,
    is_peak,
    judge,
    need_minutes,
    next_meal_slot,
    to_hhmm,
    to_min,
    walk_minutes,
)


# --- 5절 예시 -------------------------------------------------------------

def test_spec_example_100min_slot_needs_48_ok_slack_52():
    # 공강 100분 · 도보 4분 · 일반 식당 평시(대기 3 · 식사 30) · 돌아오는 도보 6분
    r = evaluate(100, walk_to=4, walk_back=6, meal_type=MealType.REGULAR)
    assert r.need_min == 48
    assert r.fit is Fit.OK
    assert r.slack_min == 52


# --- 도보 -------------------------------------------------------------------

@pytest.mark.parametrize(
    "meters, minutes",
    [
        (0, 0),
        (100, 2),     # 130 / 70 = 1.86 → 2
        (700, 13),    # 910 / 70 = 13.0 정확히 → 13 (부동소수로 14 가 되면 안 된다)
        (701, 14),
        (215, 4),     # 279.5 / 70 = 3.99 → 4
    ],
)
def test_walk_minutes_ceil(meters, minutes):
    assert walk_minutes(meters) == minutes


def test_walk_minutes_uses_config():
    assert walk_minutes(700, TimingConfig(detour=1.0, walk_m_per_min=70)) == 10


def test_walk_minutes_rejects_negative():
    with pytest.raises(ValueError):
        walk_minutes(-1)


def test_distance_m_roughly_right():
    # 위도 0.001도 ≈ 111m
    assert distance_m(35.176, 126.906, 35.177, 126.906) == pytest.approx(111.2, abs=0.5)
    assert distance_m(35.176, 126.906, 35.176, 126.906) == 0


# --- 필요 시간 ----------------------------------------------------------------

@pytest.mark.parametrize(
    "meal_type, peak, need",
    [
        (MealType.CAFETERIA, False, 0 + 3 + 20 + 0 + 5),
        (MealType.CAFETERIA, True, 0 + 10 + 20 + 0 + 5),
        (MealType.QUICK, False, 22),
        (MealType.QUICK, True, 27),
        (MealType.REGULAR, True, 43),
        (MealType.GROUP, False, 70),
        (MealType.GROUP, True, 75),
        (MealType.CAFE, False, 23),
        (MealType.CAFE, True, 25),
    ],
)
def test_need_minutes_table(meal_type, peak, need):
    assert need_minutes(0, 0, meal_type, peak=peak) == need


def test_need_minutes_accepts_string_type():
    assert need_minutes(4, 6, "regular") == 48


def test_no_next_building_drops_return_walk():  # CA1-R04
    assert need_minutes(4, None, MealType.REGULAR) == 42


# --- 판정 (D5 · CA1-R21) -----------------------------------------------------

@pytest.mark.parametrize(
    "need, slot, fit",
    [
        (48, 100, Fit.OK),
        (50, 50, Fit.OK),     # 같으면 적합
        (51, 50, Fit.TIGHT),
        (55, 50, Fit.TIGHT),  # 5분까지 빠듯
        (56, 50, Fit.OUT),
    ],
)
def test_judge_boundaries(need, slot, fit):
    assert judge(need, slot) is fit


def test_tight_has_negative_slack():
    r = evaluate(45, walk_to=4, walk_back=6, meal_type=MealType.REGULAR)
    assert (r.need_min, r.slack_min, r.fit) == (48, -3, Fit.TIGHT)


# --- 피크 ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "at, peak",
    [("11:49", False), ("11:50", True), ("12:29", True), ("12:30", False), ("17:50", True), ("18:30", False)],
)
def test_is_peak(at, peak):
    assert is_peak(at) is peak


def test_peak_is_decided_at_arrival_not_departure():
    # 11:45 출발 · 도보 6분 → 11:51 도착 → 피크 대기(8분)
    r = evaluate(100, walk_to=6, walk_back=6, meal_type=MealType.REGULAR, depart_at="11:45")
    assert r.peak is True
    assert r.need_min == 6 + 8 + 30 + 6 + 5
    # 11:40 출발 · 도보 6분 → 11:46 도착 → 평시
    r = evaluate(100, walk_to=6, walk_back=6, meal_type=MealType.REGULAR, depart_at="11:40")
    assert r.peak is False
    assert r.need_min == 50


# --- 시각 ---------------------------------------------------------------------

def test_time_roundtrip():
    assert to_min("10:15") == 615
    assert to_min("24:00") == 1440
    assert to_hhmm(615) == "10:15"


@pytest.mark.parametrize("bad", ["24:01", "25:00", "10:60", "abc"])
def test_to_min_rejects_bad(bad):
    with pytest.raises(ValueError):
        to_min(bad)


# --- 식사 공강 (CA1-R01 · R04) ------------------------------------------------

def test_spec_r01_example_whole_gap_is_slot():
    # 10:15 수업 종료 · 13:00 시험 → 10:15~13:00
    blocks = [
        {"kind": "class", "start": "09:00", "end": "10:15", "building": "공7"},
        {"kind": "exam", "start": "13:00", "end": "14:15", "building": "공2"},
    ]
    slots = find_meal_slots(blocks)
    first = slots[0]
    assert (to_hhmm(first.start), to_hhmm(first.end)) == ("10:15", "13:00")
    assert first.minutes == 165
    assert (first.from_building, first.to_building, first.last_of_day) == ("공7", "공2", False)
    # 시험 뒤 저녁 공강 (마지막 블록 뒤 → 저녁 시간대 안쪽, 다음 건물 없음)
    last = slots[-1]
    assert last.label == "17:00~19:30 · 150분"
    assert (last.from_building, last.to_building, last.last_of_day) == ("공2", None, True)
    assert len(slots) == 2


def test_schema_example_busy_block_splits_gap():
    # 스키마 예시: 10:30~11:00 내 일정이 끼면 11:00~13:00 이 식사 공강, 출발 건물은 모른다(busy)
    blocks = [
        {"kind": "class", "start": "09:00", "end": "10:15", "building": "공7"},
        {"kind": "busy", "start": "10:30", "end": "11:00"},
        {"kind": "exam", "start": "13:00", "end": "14:15", "building": "공2"},
    ]
    slots = [s for s in find_meal_slots(blocks) if not s.last_of_day]
    assert [s.label for s in slots] == ["11:00~13:00 · 120분"]
    assert slots[0].from_building is None
    assert slots[0].to_building == "공2"


def test_gap_under_30_or_outside_meal_hours_is_not_slot():
    blocks = [
        {"kind": "class", "start": "09:00", "end": "11:40", "building": "A"},
        {"kind": "class", "start": "12:05", "end": "16:00", "building": "B"},   # 25분 → 아님
        {"kind": "class", "start": "16:30", "end": "20:00", "building": "C"},   # 16:00~16:30 시간대 밖
    ]
    assert find_meal_slots(blocks) == []


def test_gap_exactly_30_counts():
    blocks = [
        {"kind": "class", "start": "10:00", "end": "12:00", "building": "A"},
        {"kind": "class", "start": "12:30", "end": "20:00", "building": "B"},
    ]
    assert [s.label for s in find_meal_slots(blocks)] == ["12:00~12:30 · 30분"]


def test_unsorted_and_overlapping_blocks_are_merged():
    blocks = [
        {"kind": "exam", "start": "13:00", "end": "14:00", "building": "B"},
        {"kind": "class", "start": "09:00", "end": "10:30", "building": "A"},
        {"kind": "busy", "start": "10:00", "end": "11:00"},                      # A 와 겹침 → 끝은 11:00, 건물 없음
    ]
    slot = find_meal_slots(blocks)[0]
    assert slot.label == "11:00~13:00 · 120분"
    assert slot.from_building is None


def test_no_blocks_means_no_slot():
    assert find_meal_slots([]) == []


def test_late_last_block_leaves_short_dinner_ignored():
    blocks = [{"kind": "class", "start": "09:00", "end": "19:10", "building": "A"}]
    assert find_meal_slots(blocks) == []  # 19:10~19:30 은 20분


def test_next_meal_slot_trims_ongoing_slot_to_now():
    blocks = [
        {"kind": "class", "start": "09:00", "end": "10:15", "building": "공7"},
        {"kind": "exam", "start": "13:00", "end": "14:15", "building": "공2"},
    ]
    s = next_meal_slot(blocks, "11:50")
    assert s.label == "11:50~13:00 · 70분"
    assert s.from_building == "공7"
    assert next_meal_slot(blocks, "09:30").label == "10:15~13:00 · 165분"
    assert next_meal_slot(blocks, "12:40").label == "17:00~19:30 · 150분"  # 남은 20분은 건너뜀
    assert next_meal_slot(blocks, "19:10") is None
