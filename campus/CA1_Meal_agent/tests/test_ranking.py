import copy
import json
from itertools import permutations
from functools import partial

import pytest

from meal.places import SAMPLES_DIR, check_dirs
from meal.ranking import Filters, MealContext, TasteSummary, _features, rank
from meal.routing import EstimateRouter
from meal.settings import SETTINGS_PATH, Settings, load_settings, to_min
from meal.timing import compute

WED, SUN = 2, 6
CENTER = (35.1765, 126.9095)
PLACES = check_dirs([SAMPLES_DIR]).places
# 점수 정책 회귀는 보행망 갱신과 분리한다. OSM 통합은 test_route_integration에서 검증한다.
rank = partial(rank, router=EstimateRouter())


def ctx(start="11:50", end="13:30", day=WED, **kw):
    return MealContext(weekday=day, start=to_min(start), end=to_min(end) if end else None,
                       origin=CENTER, dest=CENTER, **kw)


def ids(cands):
    return [c.place_id for c in cands]


def all_ids(r):
    return set(ids(r.top) + ids(r.more) + ids(r.tight) + ids(r.cafeteria))


def place_variant(pid, category, *, meters=0, tags=(), meal_type="일반"):
    p = copy.deepcopy(next(p for p in PLACES if p["id"] == "s_01"))
    p.update(id=pid, category=category, lat=CENTER[0] + meters / 111_195, lng=CENTER[1])
    p["attrs"].update(tags=list(tags), mealType=meal_type)
    return p


# ── 기본 동작 ───────────────────────────────────────────────

def test_top3_and_cafeteria_separate():
    r = rank(PLACES, ctx())
    assert len(r.top) == 3
    assert ids(r.cafeteria) == ["s_08"]
    assert "s_08" not in ids(r.top) + ids(r.more)


def test_deterministic():
    a = rank(PLACES, ctx()).to_dict()
    b = rank(list(reversed(PLACES)), ctx()).to_dict()
    assert a == b


def test_scores_sorted_desc_in_more():
    r = rank(PLACES, ctx())
    scores = [c.score for c in r.more]
    assert scores == sorted(scores, reverse=True)


def test_excluded_counts_sum_to_total():
    r = rank(PLACES, ctx())
    assert sum(r.excluded.values()) + len(all_ids(r)) == len(PLACES)


# ── 반드시 빼기 ─────────────────────────────────────────────

def test_no_info_excluded_without_solo_or_party_policy():
    r = rank(PLACES, ctx())
    assert "s_06" not in all_ids(r) and r.excluded["noInfo"] == 1
    assert "solo" not in r.excluded and "party" not in r.excluded


def test_closed_excluded_sunday():
    r = rank(PLACES, ctx(day=SUN, start="12:00", end="14:00"))
    assert {"s_01", "s_02", "s_03", "s_08"}.isdisjoint(all_ids(r))
    assert r.excluded["closed"] >= 4


def test_over_time_excluded_short_gap():
    r = rank(PLACES, ctx(start="11:00", end="11:40"))
    assert "s_07" not in all_ids(r)
    assert r.excluded["overTime"] >= 1


def test_tight_listed_separately():
    # s_07: 필요 91분(피크 대기) — 공강 88분이면 3분 초과 → 빠듯
    r = rank(PLACES, ctx(start="11:50", end="13:18"))
    assert "s_07" in ids(r.tight)
    assert "s_07" not in ids(r.top) + ids(r.more)


def test_never_again():
    r = rank(PLACES, ctx(), taste=TasteSummary(never=frozenset({"s_01"})))
    assert "s_01" not in all_ids(r) and r.excluded["never"] == 1


def test_exclude_tag_spicy():
    r = rank(PLACES, ctx(), Filters(exclude=("매운 음식",)))
    assert {"s_03", "s_05"}.isdisjoint(all_ids(r))


def test_include_category():
    r = rank(PLACES, ctx(), Filters(include=("일식",)))
    assert ids(r.top) == ["s_02"]


def test_include_quick_meal():
    r = rank(PLACES, ctx(), Filters(include=("빠른 식사",)))
    assert all_ids(r) == {"s_05"}  # 간단식(식사 15분)만


def test_unknown_vocabulary_rejected():
    with pytest.raises(ValueError):
        rank(PLACES, ctx(), Filters(include=("맛집",)))


@pytest.mark.parametrize("explicit", [False, True])
def test_category_and_required_tag_both_must_match(explicit):
    places = [
        place_variant("s_91", "일식"),
        place_variant("s_92", "한식", tags=("채식 가능",)),
        place_variant("s_93", "일식", tags=("채식 가능",)),
    ]
    filters = (Filters(include=("일식",), required_tags=("채식 가능",)) if explicit
               else Filters(include=("일식", "채식 가능")))
    result = rank(places, ctx(), filters)
    assert all_ids(result) == {"s_93"}
    assert result.excluded["filtered"] == 2


@pytest.mark.parametrize("explicit", [False, True])
def test_categories_are_or_but_required_traits_are_all_required(explicit):
    places = [
        place_variant("s_91", "일식", tags=("채식 가능",), meal_type="간단식"),
        place_variant("s_92", "중식", tags=("채식 가능",), meal_type="간단식"),
        place_variant("s_93", "한식", tags=("채식 가능",), meal_type="간단식"),
        place_variant("s_94", "일식", tags=("채식 가능",)),
        place_variant("s_95", "중식", meal_type="간단식"),
    ]
    filters = (Filters(include=("일식", "중식"), required_tags=("채식 가능", "빠른 식사")) if explicit
               else Filters(include=("일식", "중식", "채식 가능", "빠른 식사")))
    result = rank(places, ctx(), filters)
    assert all_ids(result) == {"s_91", "s_92"}
    assert result.excluded["filtered"] == 3


def test_explicit_and_legacy_traits_are_combined_and_exclusions_still_win():
    places = [
        place_variant("s_91", "일식", tags=("채식 가능",), meal_type="간단식"),
        place_variant("s_92", "일식", tags=("채식 가능",)),
        place_variant("s_93", "일식", tags=("채식 가능", "매운 음식"), meal_type="간단식"),
        place_variant("s_94", "중식", tags=("채식 가능",), meal_type="간단식"),
        place_variant("s_95", "한식", meal_type="간단식"),
    ]
    result = rank(places, ctx(), Filters(include=("채식 가능",), required_tags=("빠른 식사",),
                                         exclude=("매운 음식", "중식")))
    assert all_ids(result) == {"s_91"}
    assert result.excluded["filtered"] == 4


def test_required_tags_without_category_accept_any_matching_category():
    places = [
        place_variant("s_91", "일식", tags=("채식 가능",)),
        place_variant("s_92", "한식", tags=("채식 가능",)),
        place_variant("s_93", "중식"),
    ]
    assert all_ids(rank(places, ctx(), Filters(required_tags=("채식 가능",)))) == {"s_91", "s_92"}


@pytest.mark.parametrize("value", ["일식", "맛집"])
def test_required_tags_reject_categories_and_unknown_values(value):
    with pytest.raises(ValueError, match="required_tags"):
        rank(PLACES, ctx(), Filters(required_tags=(value,)))


def test_explicit_budget_keeps_place_with_cheap_menu():
    # s_01 대표가 7250 이지만 제육 7000 이 있으니 '7천 원 이하'에 남는다
    r = rank(PLACES, ctx(), Filters(budget=7000, budget_explicit=True))
    assert "s_01" in all_ids(r)
    assert {"s_02", "s_07"}.isdisjoint(all_ids(r))  # 대표 메뉴가 모두 7천 원 초과


def test_default_budget_only_scores():
    r = rank(PLACES, ctx(), taste=TasteSummary(budget_default=7000))
    assert {"s_02", "s_07"} <= all_ids(r)


def test_max_walk():
    r = rank(PLACES, ctx(), Filters(max_walk_min=5))
    assert all(c.timing.breakdown.walk_to <= 5 for c in r.top + r.more + r.tight + r.cafeteria)
    assert r.excluded["tooFar"] >= 1


def test_max_walk_filter_still_limits_only_the_outbound_leg():
    place = place_variant("s_91", "한식", meters=-100)
    dest = (CENTER[0] + 600 / 111_195, CENTER[1])
    result = rank([place], MealContext(WED, 660, 840, CENTER, dest), Filters(max_walk_min=3))
    assert ids(result.top) == ["s_91"]
    assert result.top[0].timing.breakdown.walk_to <= 3
    assert result.top[0].timing.breakdown.walk_total > 3


def test_removed_seating_and_solo_filters_are_not_accepted():
    for option in ({"party_size": 5}, {"solo": True}):
        with pytest.raises(TypeError):
            Filters(**option)


def test_bbq_dinner_does_not_require_seating_or_solo_information():
    """확인된 메뉴와 시간만 있으면 저녁 고기집도 추천한다."""
    r = rank(PLACES, ctx(start="18:00", end="20:30"), Filters(include=("고기",)))
    assert ids(r.top) == ["s_04"]


# ── 점수 ────────────────────────────────────────────────────

def test_taste_moves_liked_category_up():
    base = rank(PLACES, ctx())
    liked = rank(PLACES, ctx(), taste=TasteSummary(likes={"일식": 1.0}))
    assert ids(base.top)[0] != "s_02"
    assert ids(liked.top)[0] == "s_02"


def test_rain_favors_near():
    dry = rank(PLACES, ctx())
    wet = rank(PLACES, ctx(rain=True))
    assert ids(wet.top)[0] == "s_05"   # 총 도보 4분
    dry_scores = {c.place_id: c.score for c in dry.top + dry.more}
    wet_scores = {c.place_id: c.score for c in wet.top + wet.more}
    # 맑을 때 이미 1위여도, 비가 오면 가까운 곳의 상대 이점은 더 커져야 한다.
    assert wet_scores["s_05"] - wet_scores["s_01"] > dry_scores["s_05"] - dry_scores["s_01"]


@pytest.mark.parametrize("walk_to,walk_back,dest,expected", [
    (0, 0, CENTER, 1),
    (4, 6, CENTER, 2 / 3),
    (6, 6, CENTER, 0.6),  # 같은 건물 왕복이면 기존 편도/15와 같다.
    (6, 0, CENTER, 0.8),  # 다음 건물까지 0분이어도 목적지가 있으면 기준은 30분.
    (15, 15, CENTER, 0),
    (16, 20, CENTER, 0),
    (6, None, None, 0.6),
])
def test_distance_factor_counts_whole_route_and_clamps(walk_to, walk_back, dest, expected):
    context = MealContext(WED, 660, 960, CENTER, dest)
    timing = compute(start=660, end=960, walk_to=walk_to, walk_back=walk_back, meal_type="일반")
    p = place_variant("s_91", "한식")
    features = _features(p, timing, Filters(), TasteSummary(), context, load_settings())
    assert features["distance"] == pytest.approx(expected)


@pytest.mark.parametrize("rain", [False, True])
def test_route_to_next_building_can_beat_nearest_restaurant(rain):
    # 가까운 A는 출발점 남쪽, B는 다음 건물로 가는 북쪽 경로에 있다.
    places = [place_variant("s_91", "한식", meters=-100),
              place_variant("s_92", "한식", meters=300)]
    dest = (CENTER[0] + 600 / 111_195, CENTER[1])
    result = rank(places, MealContext(WED, 660, 840, CENTER, dest, rain=rain))
    assert ids(result.top) == ["s_92", "s_91"]
    b, a = result.top
    assert a.timing.breakdown.walk_to < b.timing.breakdown.walk_to
    assert a.timing.breakdown.walk_total > b.timing.breakdown.walk_total
    assert b.features["distance"] > a.features["distance"]
    for candidate in result.top:
        assert candidate.to_dict()["totalWalkMin"] == candidate.timing.breakdown.walk_total


def test_no_next_building_uses_outbound_distance_only():
    places = [place_variant("s_91", "한식", meters=-100),
              place_variant("s_92", "한식", meters=300)]
    result = rank(places, MealContext(WED, 660, None, CENTER, None))
    assert ids(result.top) == ["s_91", "s_92"]
    for c in result.top:
        assert c.timing.breakdown.walk_back == 0
        assert c.features["distance"] == pytest.approx(1 - c.timing.breakdown.walk_to / 15)


def test_restaurant_at_next_building_keeps_two_leg_distance_scale():
    place = place_variant("s_91", "한식", meters=300)
    dest = (place["lat"], place["lng"])
    candidate = rank([place], MealContext(WED, 660, 840, CENTER, dest)).top[0]
    walk = candidate.timing.breakdown
    assert walk.walk_to > 0 and walk.walk_back == 0
    assert candidate.features["distance"] == pytest.approx(1 - walk.walk_to / 30)


def test_legacy_seat_and_solo_values_cannot_change_scores_or_filtering():
    small = place_variant("s_91", "한식")
    small["attrs"]["seats"] = "S"
    small["attrs"]["soloOk"] = False
    large = place_variant("s_92", "한식")
    large["attrs"]["seats"] = "L"
    large["attrs"]["soloOk"] = True
    current = load_settings()
    old_data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    old_data["ranking"]["weights"]["crowd"] = 0.1
    old = Settings.from_dict(old_data)

    def gap(context, settings):
        candidates = rank([small, large], context, s=settings).top
        scores = {c.place_id: c.score for c in candidates}
        return scores["s_92"] - scores["s_91"]

    assert current.ranking.weights["crowd"] == 0.03
    assert gap(ctx(), current) == pytest.approx(0)
    assert gap(ctx(), old) == pytest.approx(0)
    assert gap(ctx(start="11:00", end="14:00"), current) == pytest.approx(0)


def test_recent_penalty_and_new_bonus():
    base = {c.place_id: c.score for c in rank(PLACES, ctx()).top}
    t = TasteSummary(visited=frozenset({"s_01", "s_03"}), recent=frozenset({"s_01"}))
    after = {c.place_id: c.score for c in rank(PLACES, ctx(), taste=t).top + rank(PLACES, ctx(), taste=t).more}
    assert after["s_01"] == pytest.approx(base["s_01"] - 0.05)
    assert after["s_05"] == pytest.approx(base["s_05"] + 0.05)  # 안 가 본 곳


def test_exam_period_quick_bonus():
    base = {c.place_id: c.score for c in rank(PLACES, ctx()).top}
    exam = {c.place_id: c.score for c in rank(PLACES, ctx(exam_period=True)).top}
    assert exam["s_05"] == pytest.approx(base["s_05"] + 0.05)
    assert exam["s_01"] == pytest.approx(base["s_01"])


# ── 다양성 ──────────────────────────────────────────────────

def test_top3_categories_distinct():
    extra = copy.deepcopy(json.loads((SAMPLES_DIR / "s_01.json").read_text(encoding="utf-8")))
    extra.update(id="s_91", name="한식 2호점", lat=CENTER[0], lng=CENTER[1])  # 아주 가까운 한식 → 점수 높음
    r = rank(PLACES + [extra], ctx())
    cats = [c.place["category"] for c in r.top]
    assert len(cats) == len(set(cats))
    assert "s_91" in ids(r.top) and "s_01" in ids(r.more)


def test_fill_when_categories_run_out():
    only_korean = [p for p in PLACES if p["id"] == "s_01"]
    twin = copy.deepcopy(only_korean[0])
    twin.update(id="s_92")
    r = rank(only_korean + [twin], ctx())
    assert len(r.top) == 2  # 종류가 하나뿐이어도 있는 만큼 채운다


def test_filled_top_stays_in_score_order_for_every_input_order():
    places = [
        place_variant("s_91", "한식", meters=0),
        place_variant("s_92", "한식", meters=150),
        place_variant("s_93", "중식", meters=500),
    ]
    for order in permutations(places):
        result = rank(list(order), ctx(start="11:00", end="14:00"))
        assert ids(result.top) == ["s_91", "s_92", "s_93"]
        assert result.top[0].score > result.top[1].score > result.top[2].score
        assert result.more == []


def test_filled_top_breaks_score_ties_by_need_then_id_for_every_input_order():
    # 전체 도보 30분 이상이라 거리 점수는 모두 0, 여유는 모두 만점이다.
    places = [
        place_variant("s_91", "한식", meters=900),
        place_variant("s_92", "한식", meters=850),
        place_variant("s_93", "중식", meters=900),
    ]
    for order in permutations(places):
        result = rank(list(order), ctx(start="11:00", end="14:00"))
        assert ids(result.top) == ["s_92", "s_91", "s_93"]
        assert len({c.score for c in result.top}) == 1
        assert result.top[0].timing.need_min < result.top[1].timing.need_min
        assert result.top[1].timing.need_min == result.top[2].timing.need_min


# ── 이유 칩 ─────────────────────────────────────────────────

def test_chips_are_2_to_3_and_match_factors():
    r = rank(PLACES, ctx())
    for c in r.top + r.cafeteria:
        assert 1 <= len(c.reasons) <= 3
        for chip in c.reasons:
            if chip["factor"] in c.features:
                assert c.features[chip["factor"]] >= 0.5  # 약한 요소는 이유로 내지 않는다


def test_no_solo_chip_and_rain_chip():
    r = rank(PLACES, ctx(rain=True))
    texts = [x["text"] for x in r.top[0].reasons]
    assert not any("혼밥" in text for text in texts)
    assert f"비 · 총 도보 {r.top[0].timing.breakdown.walk_total}분" in texts


def test_taste_chip_only_when_liked():
    r = rank(PLACES, ctx(), taste=TasteSummary(likes={"일식": 1.0}))
    assert r.top[0].reasons[0]["text"] == "좋아하는 일식"
    assert all("좋아하는" not in x["text"] for c in rank(PLACES, ctx()).top for x in c.reasons)


def test_to_dict_has_card_fields():
    d = rank(PLACES, ctx()).to_dict()
    card = d["candidates"][0]
    for key in ("placeId", "name", "needMin", "totalWalkMin", "slackMin", "leaveBy", "breakdown", "priceLabel", "reasons", "openUntil"):
        assert key in card
    assert set(d["excluded"]) >= {"noInfo", "closed", "overTime"}
    assert "soloOk" not in card and "seats" not in card
    assert card["mealTimeEstimated"] is False


def test_missing_meal_type_uses_configured_general_time_without_inventing_attrs():
    p = place_variant("s_91", "일식")
    p["attrs"].pop("mealType")
    p["attrs"].pop("takeout")
    attrs_before = copy.deepcopy(p["attrs"])
    data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    data["mealTypes"]["일반"]["eat"] = 37
    result = rank([p], ctx(start="11:00", end="14:00"), s=Settings.from_dict(data))
    card = result.top[0]
    assert card.timing.breakdown.eat == 37
    assert card.to_dict()["mealTimeEstimated"] is True
    assert card.to_dict()["takeout"] is None
    assert p["attrs"] == attrs_before
