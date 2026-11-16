"""추천 순위 — 정의서 CA1 4-라 · 5절 · 수용 기준을 가짜 식당으로 고정한다."""

import pytest

from meal.ranking import MealRequest, Place, Taste, Weights, recommend
from meal.timing import MealType

O = (35.1765, 126.9055)          # 출발 = 다음 건물(같은 건물로 돌아온다)
WED = 2
# 위도 0.001도 ≈ 111m → 도보 = ceil(m × 1.3 / 70)
NEAR, MID, FAR = 0.0015, 0.003, 0.006    # 도보 4분 · 7분 · 13분
LUNCH_HOURS = {WED: [(11 * 60, 15 * 60), (17 * 60, 21 * 60)]}   # 15:00~17:00 브레이크


def place(pid, category="한식", meal_type=MealType.REGULAR, dlat=NEAR, **kw):
    attrs = dict(price_band="A", solo_ok=True, seats="M", hours=LUNCH_HOURS)
    attrs.update(kw)
    return Place(pid, pid, category, meal_type, O[0] + dlat, O[1], **attrs)


def req(**kw):
    base = dict(slot_start=11 * 60, slot_min=100, weekday=WED, origin=O, dest=O)
    base.update(kw)
    return MealRequest(**base)


def ids(cands):
    return [c.placeId for c in cands]


# --- 기본 ---------------------------------------------------------------------

def test_three_places_with_times_and_chips():
    r = recommend([place("kor"), place("jpn", "일식", dlat=MID), place("snack", "분식", MealType.QUICK, dlat=FAR)], req())
    assert len(r.top) == 3 and not r.more and not r.tight
    kor = next(c for c in r.top if c.placeId == "kor")
    # 도보 4 + 대기 3 + 식사 30 + 돌아오는 도보 4 + 여유 5
    assert (kor.walkToMin, kor.walkBackMin, kor.needMin, kor.slackMin, kor.fit) == (4, 4, 46, 54, "ok")
    assert kor.openUntil == "15:00"
    assert 2 <= len(kor.reasons) <= 3 and "혼밥 OK" in kor.reasons


def test_same_input_same_output():  # CA1-R30
    ps = [place(f"p{i}", c, dlat=d) for i, (c, d) in enumerate([("한식", NEAR), ("일식", MID), ("중식", MID), ("양식", FAR)])]
    assert recommend(ps, req()) == recommend(list(ps), req())


def test_no_attrs_never_recommended():  # CA1-R11
    r = recommend([place("x", price_band=None), place("y", hours=None), place("ok")], req())
    assert ids(r.top) == ["ok"]
    assert r.excluded["정보 없음"] == 2


# --- 반드시 빼는 것 ----------------------------------------------------------

def test_break_time_excluded():  # CA1-R13 — 15:00~17:00 브레이크인 곳이 15:30 추천에 없다
    r = recommend([place("brk")], req(slot_start=15 * 60 + 30, slot_min=120))
    assert r.top == () and r.excluded == {"영업 아님": 1}


def test_meal_must_finish_before_break():
    # 14:20 출발 → 14:24 도착 → 14:57 식사 끝(대기 3 · 식사 30) → 15:00 브레이크 전이라 OK
    assert ids(recommend([place("a")], req(slot_start=14 * 60 + 20)).top) == ["a"]
    # 14:25 출발 → 15:02 끝 → 브레이크에 걸림
    assert recommend([place("a")], req(slot_start=14 * 60 + 25)).excluded == {"영업 아님": 1}


def test_last_order_excluded():
    # 점심 구간 라스트오더 13:50 — 13:46 도착은 OK, 13:56 도착은 주문 마감
    lo = {WED: [13 * 60 + 50, 20 * 60 + 30]}
    assert ids(recommend([place("a", last_order=lo)], req(slot_start=13 * 60 + 42)).top) == ["a"]
    assert recommend([place("a", last_order=lo)], req(slot_start=13 * 60 + 52)).excluded == {"주문 마감": 1}
    # 저녁 구간은 그 구간의 라스트오더(20:30)를 본다
    assert ids(recommend([place("a", last_order=lo)], req(slot_start=17 * 60)).top) == ["a"]


def test_last_order_after_midnight_ignored():
    # 새벽 00:40 라스트오더는 자정에서 끊은 구간 밖 → 무시
    p = place("late", hours={WED: [(11 * 60, 1440)]}, last_order={WED: [40]})
    assert ids(recommend([p], req(slot_start=20 * 60)).top) == ["late"]


def test_hours_unknown_kept_with_chip():
    # 영업시간을 모르는 곳은 하루 종일 연 것으로 두고, 카드에 '영업시간 확인 필요' 칩 · 마감 시각은 비운다
    allday = {d: [(0, 1440)] for d in range(7)}
    c = recommend([place("u", hours=allday, hours_unknown=True)], req(slot_start=16 * 60)).top[0]
    assert c.reasons[0] == "영업시간 확인 필요" and c.openUntil == ""


def test_router_replaces_straight_line():
    # 길 거리를 주면 우회 계수 없이 ⌈m ÷ 70⌉ — 직선으로는 도보 4분이지만 길로 700m 면 10분
    c = recommend([place("a")], req(), router=lambda a, b: 700.0).top[0]
    assert (c.walkToMin, c.walkBackMin) == (10, 10)
    # 길이 이어지지 않으면(None) 직선 × 1.3 으로 돌아간다
    c = recommend([place("a")], req(), router=lambda a, b: None).top[0]
    assert c.walkToMin == 4


def test_closed_day_excluded():
    assert recommend([place("a")], req(weekday=6)).excluded == {"영업 아님": 1}


def test_time_out_and_tight():  # D5 · CA1-R21
    ps = [place("near"), place("far", dlat=FAR)]   # 필요 46분 · 64분
    r = recommend(ps, req(slot_min=60))
    assert ids(r.top) == ["near"]
    assert ids(r.tight) == ["far"] and r.tight[0].fit == "tight" and r.tight[0].slackMin == -4
    r = recommend(ps, req(slot_min=58))
    assert r.excluded == {"시간 초과": 1}


def test_last_block_no_return_walk():  # CA1-R04
    c = recommend([place("a")], req(dest=None)).top[0]
    assert (c.walkBackMin, c.needMin) == (None, 42)


def test_explicit_budget_excludes_default_budget_only_scores():  # CA1-R05 · 5절
    ps = [place("cheap", price_band="A"), place("pricey", "일식", price_band="C")]
    r = recommend(ps, req(budget=7000))
    assert ids(r.top) == ["cheap"] and r.excluded == {"예산 초과": 1}
    r = recommend(ps, req(), Taste(budget_default=7000))
    assert ids(r.top) == ["cheap", "pricey"]   # 남지만 점수가 낮다


def test_solo_and_group_seats():  # CA1-R43
    ps = [place("solo", solo_ok=True, seats="S"), place("group", "고기", solo_ok=False, seats="L")]
    assert ids(recommend(ps, req()).top) == ["solo"]
    r = recommend(ps, req(party_size=5))
    assert ids(r.top) == ["group"] and r.excluded == {"좌석 부족": 1}


def test_solo_unknown_is_kept_without_chip():
    ps = [place("unknown", solo_ok=None), place("no", "일식", solo_ok=False)]
    r = recommend(ps, req())
    assert ids(r.top) == ["unknown"] and r.excluded == {"혼밥 불가": 1}
    assert "혼밥 OK" not in r.top[0].reasons


def test_never_exclude_include_max_walk():
    ps = [place("a"), place("b", "일식"), place("c", "분식", MealType.QUICK, dlat=FAR)]
    assert ids(recommend(ps, req(), Taste(never=frozenset({"a"}))).top) == ["b", "c"]
    assert ids(recommend(ps, req(exclude=frozenset({"일식"}))).top) == ["a", "c"]
    assert ids(recommend(ps, req(include=frozenset({"빠른 식사"}))).top) == ["c"]
    assert ids(recommend(ps, req(max_walk_min=5)).top) == ["a", "b"]


# --- 점수 · 다양성 -----------------------------------------------------------

def test_rain_moves_near_place_up():  # CA1-R06
    ps = [place("near"), place("liked_far", "일식", dlat=FAR)]
    taste = Taste(likes={"일식": 1.0})
    assert ids(recommend(ps, req(slot_min=200), taste).top)[0] == "liked_far"
    r = recommend(ps, req(slot_min=200, rain=True), taste)
    assert ids(r.top)[0] == "near"
    assert "비 와서 가까운 곳" in r.top[0].reasons


def test_rain_weights_renormalized():
    w = Weights().as_dict(rain=True)
    assert sum(w.values()) == pytest.approx(1.0)
    assert w["distance"] == pytest.approx(0.30 / 1.15)


def test_liked_category_rises():  # CA1-R53 수용 기준 — 일식에 👍 뒤 일식이 위로
    ps = [place("kor"), place("jpn", "일식", dlat=MID)]
    assert ids(recommend(ps, req()).top)[0] == "kor"
    r = recommend(ps, req(), Taste(likes={"일식": 0.488}))   # 0 → 👍 세 번(0.2·0.36·0.488)
    assert ids(r.top)[0] == "jpn" and "좋아하는 일식" in r.top[0].reasons


def test_price_chip_only_when_it_is_a_reason():
    ps = [place("cheap", price_band="A"), place("pricey", "양식", price_band="C")]
    chips = {c.placeId: c.reasons for c in recommend(ps, req()).top}
    assert "7천 원 이하" in chips["cheap"]
    assert "1만~1.5만 원" not in chips["pricey"]          # 비싼 건 이유가 아니다
    chips = {c.placeId: c.reasons for c in recommend(ps, req(), Taste(budget_default=15000)).top}
    assert "1만~1.5만 원" in chips["pricey"]              # 예산 안이면 칩


def test_top3_categories_do_not_repeat():  # CA1-R31
    ps = [place("k1"), place("k2"), place("k3"), place("j", "일식", dlat=FAR)]
    r = recommend(ps, req(slot_min=200))
    cats = [c.category for c in r.top]
    assert "일식" in cats and len(r.top) == 3
    assert ids(r.more) == [x for x in ["k1", "k2", "k3"] if x not in ids(r.top)]


def test_top3_filled_when_few_categories():
    r = recommend([place("k1"), place("k2"), place("k3"), place("k4")], req())
    assert len(r.top) == 3 and len(r.more) == 1


def test_explore_and_repeat():
    ps = [place("a"), place("b")]
    assert ids(recommend(ps, req(), Taste(visited=frozenset({"a"}))).top)[0] == "b"
    assert ids(recommend(ps, req(), Taste(recent=frozenset({"a"}))).top)[0] == "b"


def test_hurry_prefers_quick_meal():  # CA1-R35
    ps = [place("regular", dlat=NEAR), place("quick", "분식", MealType.QUICK, dlat=MID)]
    base = recommend(ps, req(slot_min=200)).top
    hurry = recommend(ps, req(slot_min=200, hurry=True)).top
    qs = lambda cands: next(c.score for c in cands if c.placeId == "quick")
    assert qs(hurry) == pytest.approx(qs(base) + 0.05)


def test_reputation_shrinks_small_samples():
    ps = [place("a"), place("b"), place("c")]
    # a: 1/1 👍 (표본 1개) · b: 18/20 · c: 5/20 → 전체 평균(24/41)으로 당기면 a 는 0.66, b 는 0.84
    r = recommend(ps, req(), reputation={"a": (1, 1), "b": (18, 20), "c": (5, 20)})
    assert ids(r.top) == ["b", "a", "c"]


def test_chips_show_what_beats_the_others():
    # 둘 다 여유·예산 만점 → 칩은 서로 다른 점: kor 는 가까움, jpn 은 취향
    r = recommend([place("kor"), place("jpn", "일식", dlat=MID)], req(), Taste(likes={"일식": 0.488}))
    chips = {c.placeId: c.reasons for c in r.top}
    assert chips["jpn"][0] == "좋아하는 일식"
    assert chips["kor"][0] == "도보 4분"


# --- 학식 ---------------------------------------------------------------------

def test_cafeteria_separate_with_same_timing():  # CA1-R34
    ps = [place("caf", "학식", MealType.CAFETERIA), place("kor")]
    r = recommend(ps, req())
    assert ids(r.cafeteria) == ["caf"] and ids(r.top) == ["kor"]
    assert r.cafeteria[0].needMin == 4 + 3 + 20 + 4 + 5


def test_cafeteria_crowded_at_peak():
    caf = place("caf", "학식", MealType.CAFETERIA)
    calm = recommend([caf], req(slot_start=11 * 60)).cafeteria[0]
    peak = recommend([caf], req(slot_start=12 * 60)).cafeteria[0]
    assert peak.needMin == calm.needMin + 7          # 피크 대기 10분
    assert peak.score < calm.score
