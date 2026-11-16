"""추천 순위 (기능 정의서 CA1 4-라 · 5절) — 빼기 → 점수 → 다양성 → 이유 칩.

    1. 반드시 빼기  정보 없음 · 다시 안 보기 · 종류/특징 필터
                   · 문장에 적은 예산 초과 · 최대 도보 초과 · 시간 초과 · 영업 아님
    2. 점수        Σ wᵢ · fᵢ (fᵢ 는 0~1, 가중치는 settings.json — 비 오면 거리 ×2 뒤 다시 정규화)
                   거리 = 전체 도보(출발 → 식당 → 다음 건물), 다음 건물이 없으면 편도
                   + 새 곳 +0.05 · 최근 추천 −0.05 · 시험기간 빠른 식사 +0.05
    3. 다양성      상위 3곳은 종류가 겹치지 않게
    4. 이유 칩     점수에 가장 크게 기여한 요소 2개

학식은 식당 추천과 나란히 보이는 별도 목록(cafeteria)으로 돌려준다 (CA1-R34).
같은 입력이면 언제나 같은 결과가 나온다 (CA1-R30) — 동점은 필요 시간 → id 순.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import date
from functools import lru_cache
from statistics import median

from .hours import OpenStatus, check_timing
from .places import (SCHEMA_PATH, price_band, price_label, rep_price, recommendation_ready,
                     meal_type_for_timing, meal_menus, effective_price_source)
from .availability import candidate_active
from .settings import Settings, load_settings, to_min
from .timing import LatLng, Timing, compute_for_place

BANDS = "ABCD"
QUICK = "빠른 식사"
CAFETERIA = "학식"

# 빠진 이유 — 화면의 '왜 없는지 한 줄'과 완화 칩에 쓴다 (먼저 걸린 이유 하나만 센다)
EXCLUDE_KEYS = ("unavailable", "noInfo", "never", "filtered", "overBudget", "tooFar", "overTime", "closed")


@dataclass(frozen=True)
class MealContext:
    """언제 · 어디서 — 오늘 맥락이나 직접 입력에서 만든다."""
    weekday: int                 # 0=월 … 6=일
    start: int                   # 공강 시작(또는 지금), 자정부터 분
    end: int | None              # 다음 블록 시작. None = 끝이 없음
    origin: LatLng               # 출발 위치
    dest: LatLng | None          # 다음 건물. None = 없음
    rain: bool = False           # 도착 1시간 안 비 예보
    exam_period: bool = False
    on_date: date | None = None


@dataclass(frozen=True)
class Filters:
    """무엇을 — 필터 화면이나 MealQuery 에서 만든다. 모든 필터는 빼기 조건이다 (CA1-R36)."""
    budget: int | None = None            # 1인 예산(원)
    budget_explicit: bool = False        # 문장·필터에서 직접 정한 예산이면 초과는 뺀다. 기본 예산이면 점수로만
    include: tuple[str, ...] = ()        # 종류는 OR. 기존 입력의 특징 태그는 required_tags 와 합쳐 AND
    exclude: tuple[str, ...] = ()        # 하나라도 맞으면 뺀다
    max_walk_min: int | None = None      # 편도 최대 도보
    required_tags: tuple[str, ...] = ()  # 모두 만족해야 하는 특징('빠른 식사' 포함). 종류는 include 에
    budget_min: int | None = None        # 명시적 예산 범위 하한(포함). 미지정이면 하한 없음
    budget_max: int | None = None        # 명시적 예산 범위 상한(포함). 미지정이면 상한 없음


@dataclass(frozen=True)
class TasteSummary:
    """폰이 보내는 취향 요약 (CA1-R52). 서버는 저장하지 않는다."""
    likes: dict[str, float] = field(default_factory=dict)  # 종류 → −1~1
    never: frozenset[str] = frozenset()                    # '다시 안 보기' 한 placeId
    budget_default: int | None = None
    visited: frozenset[str] = frozenset()                  # 가 본 곳 — 없으면 '새 곳'
    recent: frozenset[str] = frozenset()                   # 최근 3번 안에 추천된 곳


@dataclass
class Candidate:
    place: dict
    timing: Timing
    open: OpenStatus
    price: int | None
    features: dict[str, float]
    contributions: dict[str, float]
    score: float
    reasons: list[dict]
    menu_choices: list[dict] = field(default_factory=list)

    @property
    def place_id(self) -> str:
        return self.place["id"]

    def to_dict(self) -> dict:
        p = self.place
        return {
            "placeId": p["id"],
            "name": p["name"],
            "category": p["category"],
            "lat": p["lat"],
            "lng": p["lng"],
            "repPrice": self.price,
            "priceSource": effective_price_source(p),
            "effectiveMenus": [dict(m) for m in self.menu_choices],
            "priceBand": price_band(self.price),
            "priceLabel": price_label(self.price),
            "takeout": p["attrs"].get("takeout"),
            "mealTimeEstimated": "mealType" not in p["attrs"],
            **self.timing.to_dict(),
            "openUntil": self.open.to_dict()["openUntil"],
            "score": round(self.score, 4),
            "reasons": self.reasons,
        }


@dataclass
class RankResult:
    top: list[Candidate]
    more: list[Candidate]
    tight: list[Candidate]
    cafeteria: list[Candidate]
    excluded: dict[str, int]

    def to_dict(self) -> dict:
        return {
            "candidates": [c.to_dict() for c in self.top],
            "more": [c.to_dict() for c in self.more],
            "tight": [c.to_dict() for c in self.tight],
            "cafeteria": [c.to_dict() for c in self.cafeteria],
            "excluded": self.excluded,
        }


# ── 사전 ────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _vocabularies() -> tuple[frozenset[str], frozenset[str]]:
    """종류와 필수 특징을 나눠 읽는다 — 특징에는 계산으로 얻는 '빠른 식사'도 포함."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    cats = schema["properties"]["category"]["enum"]
    tags = schema["$defs"]["attrs"]["properties"]["tags"]["items"]["enum"]
    return frozenset(cats), frozenset([*tags, QUICK])


def vocabulary() -> frozenset[str]:
    """include · exclude 에 쓸 수 있는 종류·특징 사전."""
    cats, tags = _vocabularies()
    return cats | tags


def _normalize_filters(f: Filters) -> Filters:
    """종류는 하나 이상, 필수 특징은 모두 일치하도록 기존 혼합 include 도 분리한다."""
    for value in (f.budget_min, f.budget_max):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise ValueError("예산 범위는 0 이상의 정수로 지정하세요.")
    if _has_budget_range(f):
        if f.budget is not None:
            raise ValueError("예산은 budget 또는 budget_min/budget_max 중 한 방식으로 지정하세요.")
        if f.budget_min is not None and f.budget_max is not None and f.budget_min > f.budget_max:
            raise ValueError("최소 예산은 최대 예산보다 클 수 없습니다.")
    cats, tags = _vocabularies()
    invalid_tags = set(f.required_tags) - tags
    if invalid_tags:
        raise ValueError(f"required_tags 에는 특징 태그만 넣으세요(종류는 include): {sorted(invalid_tags)}")
    requested = set(f.include)
    unknown = (requested | set(f.exclude)) - vocabulary()
    if unknown:
        raise ValueError(f"사전에 없는 종류·특징: {sorted(unknown)}")
    return replace(f, include=tuple(sorted(requested & cats)),
                   required_tags=tuple(sorted((requested & tags) | set(f.required_tags))))


def _labels(place: dict, s: Settings) -> set[str]:
    attrs = place["attrs"]
    out = {place["category"], *attrs.get("tags", [])}
    if s.meal_types[meal_type_for_timing(place)].eat <= s.quick_eat_max_min:
        out.add(QUICK)
    return out


# ── 1. 빼기 ─────────────────────────────────────────────────

def _has_budget_range(f: Filters) -> bool:
    return f.budget_min is not None or f.budget_max is not None


def _price_for_filters(place: dict, f: Filters, order_at: int | None = None) -> int | None:
    """범위 선택 시 실제 범위 안 대표 메뉴들만 가격·점수에 사용한다."""
    if not _has_budget_range(f) and order_at is None:
        return rep_price(place)
    prices = [m["price"] for m in _menus_for_filters(place, f, order_at)]
    return int(median(prices)) if prices else None


def _menus_for_filters(place: dict, f: Filters, order_at: int | None = None) -> list[dict]:
    return [m for m in meal_menus(place)
            if (f.budget_min is None or m["price"] >= f.budget_min)
            and (f.budget_max is None or m["price"] <= f.budget_max)
            and (order_at is None or not m.get("orderWindow") or
                 to_min(m["orderWindow"]["start"]) <= order_at < to_min(m["orderWindow"]["end"]))]


def _exclusion(place: dict, f: Filters, taste: TasteSummary, s: Settings, *, skip_budget=False, order_at=None) -> str | None:
    """시간·영업을 보기 전에 가게 속성만으로 빼는 이유."""
    if not candidate_active(place):
        return "unavailable"
    attrs = place.get("attrs")
    if not recommendation_ready(place):
        return "noInfo"
    if place["id"] in taste.never:
        return "never"
    labels = _labels(place, s)
    if f.exclude and labels & set(f.exclude):
        return "filtered"
    if f.include and place["category"] not in f.include:
        return "filtered"
    if not set(f.required_tags).issubset(labels):
        return "filtered"
    if skip_budget:
        return None
    if order_at is not None and not _menus_for_filters(place, Filters(), order_at):
        return "filtered"
    if _has_budget_range(f) and _price_for_filters(place, f, order_at) is None:
        return "overBudget"  # 기존 응답 키 호환: 범위의 아래/위 모두 포함
    # 직접 정한 예산: 예산 안에서 먹을 대표 메뉴가 하나도 없으면 뺀다 (점수는 대표 가격 중앙값으로)
    cheapest = min((m["price"] for m in _menus_for_filters(place, Filters(), order_at)), default=None)
    if f.budget_explicit and f.budget is not None and cheapest is not None and cheapest > f.budget:
        return "overBudget"
    return None


# ── 2. 점수 ─────────────────────────────────────────────────

def _features(place: dict, t: Timing, f: Filters, taste: TasteSummary, ctx: MealContext, s: Settings) -> dict[str, float]:
    r = s.ranking
    # 여유: 끝이 없으면 1, 빠듯(음수)이면 0
    slack = 1.0 if t.slack_min is None else max(0.0, min(t.slack_min / r.slack_full_min, 1.0))

    budget = f.budget if f.budget is not None else taste.budget_default
    price = _price_for_filters(place, f, t.arrive_at + t.breakdown.wait)
    if _has_budget_range(f):
        f_budget = 1.0 if price is not None else 0.0
    elif budget is None or price is None:
        f_budget = 0.5  # 예산을 모르면 모두 같게
    elif price <= budget:
        f_budget = 1.0
    elif BANDS.index(price_band(price)) <= BANDS.index(price_band(budget)) + 1:
        f_budget = r.over_budget_one_band
    else:
        f_budget = 0.0

    like = max(-1.0, min(1.0, taste.likes.get(place["category"], 0.0)))
    f_taste = (like + 1) / 2

    # 왕복/다음 건물 이동은 두 구간이므로 편도당 기준의 2배를 사용한다.
    # 같은 건물로 돌아올 때 기존 거리 감도를 유지하면서 돌아갈 경로도 점수에 반영한다.
    distance_limit = r.distance_zero_min * (2 if ctx.dest is not None else 1)
    f_dist = max(0.0, 1 - t.breakdown.walk_total / distance_limit)

    # 혼잡은 학식 피크 신호만 사용한다. 좌석 규모를 점수 근거로 쓰지 않는다.
    crowded = t.peak and meal_type_for_timing(place) == "학식"
    f_crowd = 0.0 if crowded else 1.0

    return {"slack": slack, "budget": f_budget, "taste": f_taste, "distance": f_dist, "crowd": f_crowd}


def _weights(ctx: MealContext, s: Settings) -> dict[str, float]:
    w = dict(s.ranking.weights)
    if ctx.rain:
        w["distance"] *= s.ranking.rain_distance_multiplier
    total = sum(w.values())
    return {k: v / total for k, v in w.items()}


def _bonus(place: dict, taste: TasteSummary, ctx: MealContext, s: Settings) -> float:
    r = s.ranking
    b = 0.0
    if taste.visited and place["id"] not in taste.visited:
        b += r.bonus_new_place
    if place["id"] in taste.recent:
        b -= r.penalty_recent
    if ctx.exam_period and s.meal_types[meal_type_for_timing(place)].eat <= s.quick_eat_max_min:
        b += r.bonus_exam_quick
    return b


# ── 4. 이유 칩 ──────────────────────────────────────────────

def _chip_text(key: str, place: dict, t: Timing, price: int | None, ctx: MealContext) -> str | None:
    if key == "slack":
        return f"여유 {t.slack_min}분" if t.slack_min is not None else None
    if key == "budget":
        return price_label(price)
    if key == "taste":
        return f"좋아하는 {place['category']}"
    if key == "distance":
        text = f"총 도보 {t.breakdown.walk_total}분"
        return f"비 · {text}" if ctx.rain else text
    return None  # crowd 는 칩으로 내지 않는다


def _reasons(place: dict, t: Timing, price: int | None, feats: dict, contribs: dict, mean: dict,
             f: Filters, taste: TasteSummary, ctx: MealContext, s: Settings) -> list[dict]:
    """점수 기여가 다른 후보 평균보다 큰 순서로 칩을 고른다 — 모두가 같은 값(예: 여유 만점)은 이유가 되지 않는다."""
    chips: list[dict] = []
    edge = {k: contribs[k] - mean.get(k, 0.0) for k in contribs}
    for key in sorted(edge, key=lambda k: (-edge[k], -contribs[k], k)):
        if len(chips) == 2:
            break
        if feats[key] < s.ranking.chip_min_factor:
            continue
        if key == "taste" and taste.likes.get(place["category"], 0.0) <= 0:
            continue  # 취향 정보가 없거나 싫어하는 종류는 이유가 아니다
        text = _chip_text(key, place, t, price, ctx)
        if text:
            chips.append({"factor": key, "text": text})
    if not chips:
        chips.append({"factor": "need", "text": f"왕복 포함 {t.need_min}분"})
    return chips[:3]


# ── 전체 ────────────────────────────────────────────────────

def _candidate_order(c: Candidate) -> tuple[float, int, str]:
    return -c.score, c.timing.need_min, c.place_id


def _diverse_top(cands: list[Candidate], n: int = 3) -> tuple[list[Candidate], list[Candidate]]:
    """점수 순으로 보되 종류가 겹치지 않게 n 곳. 모자라면 남은 순서대로 채운다 (CA1-R31)."""
    top: list[Candidate] = []
    seen: set[str] = set()
    for c in cands:
        if len(top) < n and c.place["category"] not in seen:
            top.append(c)
            seen.add(c.place["category"])
    for c in cands:
        if len(top) >= n:
            break
        if c not in top:
            top.append(c)
    # 종류가 모자라 보충한 후보도 최종 카드에서는 점수·필요 시간·id 순으로 보여 준다.
    top.sort(key=_candidate_order)
    rest = [c for c in cands if c not in top]
    return top, rest


def rank(
    places: list[dict],
    ctx: MealContext,
    filters: Filters | None = None,
    taste: TasteSummary | None = None,
    s: Settings | None = None,
    router=None,
) -> RankResult:
    f = _normalize_filters(filters or Filters())
    taste = taste or TasteSummary()
    s = s or load_settings()

    weights = _weights(ctx, s)
    excluded = {k: 0 for k in EXCLUDE_KEYS}
    ok: list[Candidate] = []
    tight: list[Candidate] = []
    cafeteria: list[Candidate] = []

    for place in places:
        why = _exclusion(place, f, taste, s, skip_budget=True)
        if why:
            excluded[why] += 1
            continue

        t = compute_for_place(place, start=ctx.start, end=ctx.end, origin=ctx.origin, dest=ctx.dest, s=s, router=router)
        order_at = t.arrive_at + t.breakdown.wait
        why = _exclusion(place, f, taste, s, order_at=order_at)
        if why:
            excluded[why] += 1
            continue
        if f.max_walk_min is not None and t.breakdown.walk_to > f.max_walk_min:
            excluded["tooFar"] += 1
            continue
        if t.fit == "out":
            excluded["overTime"] += 1
            continue
        st = check_timing(place, ctx.weekday, t, on_date=ctx.on_date)
        if not st.ok:
            excluded["closed"] += 1
            continue

        feats = _features(place, t, f, taste, ctx, s)
        contribs = {k: weights[k] * feats[k] for k in weights}
        score = sum(contribs.values()) + _bonus(place, taste, ctx, s)
        price = _price_for_filters(place, f, order_at)
        cand = Candidate(place=place, timing=t, open=st, price=price, features=feats,
                         contributions=contribs, score=score, reasons=[], menu_choices=_menus_for_filters(place, f, order_at))

        if place["category"] == CAFETERIA:
            cafeteria.append(cand)
        elif t.fit == "tight":
            tight.append(cand)
        else:
            ok.append(cand)

    everyone = ok + tight + cafeteria
    mean = {k: sum(c.contributions[k] for c in everyone) / len(everyone) for k in weights} if everyone else {}
    for c in everyone:
        c.reasons = _reasons(c.place, c.timing, c.price, c.features, c.contributions, mean, f, taste, ctx, s)

    ok.sort(key=_candidate_order)
    tight.sort(key=_candidate_order)
    cafeteria.sort(key=_candidate_order)
    top, more = _diverse_top(ok)
    return RankResult(top=top, more=more, tight=tight, cafeteria=cafeteria, excluded=excluded)
