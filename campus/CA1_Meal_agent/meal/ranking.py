"""추천 순위 — 반드시 빼는 조건 · 규칙 점수 · 다양성 · 이유 칩 (정의서 CA1 4-라 · 5절).

식사 화면과 CA2 대화(`recommend_meal`)가 모두 `recommend()` 하나를 부른다(코드 규칙 3).
같은 입력이면 언제나 같은 결과가 나온다(CA1-R30) — 무작위·현재 시각을 쓰지 않는다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

from .timing import (
    DEFAULT_CONFIG,
    Fit,
    MealType,
    TimingConfig,
    distance_m,
    evaluate,
    to_hhmm,
    walk_minutes,
    walk_minutes_path,
)

LatLng = tuple[float, float]


# ---------------------------------------------------------------------------
# 입력
# ---------------------------------------------------------------------------

PRICE_BANDS = ("A", "B", "C", "D")
PRICE_BAND_UPPER = {"A": 7000, "B": 10000, "C": 15000, "D": None}   # 정의서 7절 ⏳
PRICE_BAND_LABEL = {"A": "7천 원 이하", "B": "7천~1만 원", "C": "1만~1.5만 원", "D": "1.5만 원 넘음"}
SEAT_ORDER = {"S": 0, "M": 1, "L": 2}
HOURS_UNKNOWN_CHIP = "영업시간 확인 필요"


@dataclass(frozen=True)
class Place:
    """`Place` + `PlaceAttr` (정의서 7절) 중 계산에 쓰는 것.

    hours: 요일(0=월 … 6=일) → 영업 구간 [(시작분, 끝분), …]. 구간 사이 빈 곳이 브레이크, 요일이 없으면 휴무.
    """

    id: str
    name: str
    category: str
    meal_type: MealType
    lat: float
    lng: float
    price_band: str | None = None
    solo_ok: bool | None = None
    seats: str | None = None
    hours: Mapping[int, Sequence[tuple[int, int]]] | None = None
    tags: frozenset[str] = frozenset()
    # 요일 → 라스트오더 시각(분) 목록. 영업 구간 안에 있는 것만 그 구간의 주문 마감으로 본다.
    last_order: Mapping[int, Sequence[int]] | None = None
    # 영업시간을 몰라 하루 종일 연 것으로 두고 추천한 곳 — 카드에 `영업시간 확인 필요` 칩을 붙인다 (2026-11-13 팀 결정)
    hours_unknown: bool = False

    @property
    def has_attrs(self) -> bool:
        """조사 속성이 없는 곳은 추천하지 않는다 (CA1-R11)."""
        return self.price_band in PRICE_BANDS and self.hours is not None


@dataclass(frozen=True)
class Taste:
    """폰에서 보내는 취향 요약 (CA1-R52). 서버는 저장하지 않는다."""

    likes: Mapping[str, float] = field(default_factory=dict)   # 카테고리 → −1~1
    never: frozenset[str] = frozenset()                        # 다시 안 보기 placeId
    budget_default: int | None = None
    visited: frozenset[str] = frozenset()                      # 가 본 곳
    recent: frozenset[str] = frozenset()                       # 최근 3번 추천된 곳


@dataclass(frozen=True)
class MealRequest:
    slot_start: int                    # 출발(공강 시작 또는 지금), 자정부터 분
    slot_min: int                      # 쓸 수 있는 분
    weekday: int                       # 0=월
    origin: LatLng                     # 출발 위치(건물 좌표 또는 반올림한 폰 위치)
    dest: LatLng | None = None         # 다음 건물. 없으면 돌아오는 도보 없음 (CA1-R04)
    party_size: int = 1
    budget: int | None = None          # 요청 문장에 적은 1인 예산 → 넘으면 뺀다 (CA1-R05)
    include: frozenset[str] = frozenset()
    exclude: frozenset[str] = frozenset()
    solo: bool | None = None           # None 이면 party_size == 1
    max_walk_min: int | None = None
    rain: bool = False                 # 도착 1시간 안 비 예보 (CA1-R06)
    hurry: bool = False                # 시험기간 또는 24시간 안 마감 (CA1-R35)


# ---------------------------------------------------------------------------
# 가중치 (5절 초기값 ⏳ — 2차에서 학습으로 다시 정한다)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Weights:
    slack: float = 0.20
    budget: float = 0.20
    taste: float = 0.25
    distance: float = 0.15
    crowd: float = 0.10
    reputation: float = 0.10

    def as_dict(self, rain: bool) -> dict[str, float]:
        w = {
            "slack": self.slack, "budget": self.budget, "taste": self.taste,
            "distance": self.distance * (2 if rain else 1), "crowd": self.crowd, "reputation": self.reputation,
        }
        total = sum(w.values())
        return {k: v / total for k, v in w.items()}


@dataclass(frozen=True)
class RankingConfig:
    weights: Weights = Weights()
    top_n: int = 3
    explore_bonus: float = 0.05     # 가 본 적 없는 곳
    repeat_penalty: float = 0.05    # 최근 3번 추천된 곳
    hurry_bonus: float = 0.05       # 시험기간 · 빠른 식사 유형
    rep_prior_n: int = 5            # 평판 표본이 적을 때 전체 평균 쪽으로 당기는 세기
    group_party: int = 5            # 이 인원 이상이면 좌석 M 이상 (CA1-R43 ⏳)
    timing: TimingConfig = DEFAULT_CONFIG


DEFAULT_RANKING = RankingConfig()


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MealCandidate:
    """정의서 7절 `MealCandidate` — 응답에만 있고 저장하지 않는다."""

    placeId: str
    name: str
    category: str
    walkToMin: int
    walkBackMin: int | None
    needMin: int
    slackMin: int
    fit: str            # ok | tight
    score: float
    reasons: tuple[str, ...]
    openUntil: str      # 지금 영업 구간이 끝나는 시각(마감 또는 브레이크 시작)
    priceBand: str


@dataclass(frozen=True)
class Recommendation:
    top: tuple[MealCandidate, ...]       # 적합 상위 3곳 (카테고리 겹치지 않게)
    more: tuple[MealCandidate, ...]      # 적합 나머지 (`더 보기`)
    tight: tuple[MealCandidate, ...]     # 빠듯 — 적합 아래에 칩과 함께
    cafeteria: tuple[MealCandidate, ...] # 학식 카드 — 같은 시간 계산 (CA1-R34)
    excluded: Mapping[str, int]          # 뺀 이유별 개수 — '후보 0곳' 화면의 한 줄·완화 칩용


# ---------------------------------------------------------------------------
# 계산
# ---------------------------------------------------------------------------

QUICK_TYPES = frozenset({MealType.QUICK, MealType.CAFETERIA, MealType.CAFE})


# 두 점 사이 길 거리(m)를 주는 함수 — 길이 없으면 None. 주지 않으면 직선거리 × 우회 계수.
Router = Callable[[LatLng, LatLng], "float | None"]


def _walk(a: LatLng, b: LatLng, cfg: TimingConfig, router: Router | None = None) -> int:
    if router is not None:
        m = router(a, b)
        if m is not None:
            return walk_minutes_path(m, cfg)
    return walk_minutes(distance_m(a[0], a[1], b[0], b[1]), cfg)


def _open_interval(place: Place, weekday: int, start: int, end: int) -> tuple[int, int] | None:
    """[start, end] 가 통째로 들어가는 영업 구간 — 도착해서 다 먹을 때까지 브레이크·마감에 걸리지 않아야 한다."""
    for o, c in (place.hours or {}).get(weekday, ()):
        if o <= start and end <= c:
            return o, c
    return None


def _order_deadline(place: Place, weekday: int, interval: tuple[int, int]) -> int | None:
    """그 영업 구간의 라스트오더 — 구간 안(시작 뒤 · 마감 이전)에 있는 것 중 가장 이른 것. 없으면 None.

    새벽까지 여는 곳의 00:40 같은 라스트오더는 자정에서 끊은 구간 밖이라 무시된다.
    """
    o, c = interval
    times = [t for t in (place.last_order or {}).get(weekday, ()) if o < t <= c]
    return min(times) if times else None


def _band_over(band: str, budget: int) -> int:
    """예산 안 가격대보다 몇 단계 위인지 (0 이하 = 예산 안)."""
    ok = [b for b in PRICE_BANDS if PRICE_BAND_UPPER[b] is not None and PRICE_BAND_UPPER[b] <= budget]
    max_ok = PRICE_BANDS.index(ok[-1]) if ok else -1
    return PRICE_BANDS.index(band) - max_ok


def _matches(place: Place, terms: frozenset[str], cfg: TimingConfig) -> bool:
    if not terms:
        return False
    if place.category in terms or place.tags & terms:
        return True
    if "빠른 식사" in terms and cfg.durations[place.meal_type].eat <= 15:
        return True
    return False


def _reputation(rep: Mapping[str, tuple[int, int]], cfg: RankingConfig) -> tuple[float, dict[str, float]]:
    ups = sum(u for u, _ in rep.values())
    total = sum(n for _, n in rep.values())
    prior = ups / total if total else 0.5
    k = cfg.rep_prior_n
    return prior, {pid: (u + prior * k) / (n + k) for pid, (u, n) in rep.items()}


@dataclass
class _Scored:
    place: Place
    walk_to: int
    walk_back: int | None
    need: int
    slack: int
    fit: Fit
    open_until: int
    score: float
    parts: dict[str, float]     # 요소별 기여 wᵢ·fᵢ
    reasons: tuple[str, ...] = ()


def _reasons(
    s: _Scored, req: MealRequest, taste: Taste, mean_parts: Mapping[str, float], budget: int | None = None,
) -> tuple[str, ...]:
    """점수에 가장 크게 기여한 요소 2개 + (혼밥 요청이면) `혼밥 OK` (5절 이유 칩 · CA1-R32).

    '기여'는 같은 요청의 다른 후보들 평균보다 얼마나 더 받았는지(wᵢ·fᵢ − 평균)로 잰다.
    그냥 wᵢ·fᵢ 로 고르면 거의 모든 곳이 만점인 여유·예산이 늘 칩을 차지해 이유가 되지 못한다.
    평균보다 나은 요소가 모자라면 기여가 큰 순서로 채운다.
    """
    labels: dict[str, str | None] = {
        "slack": f"여유 {s.slack}분" if s.slack > 0 else None,
        # 가격 칩은 '이유'가 될 때만 — 예산을 정했으면 예산 안, 안 정했으면 가장 싼 가격대(A)
        "budget": PRICE_BAND_LABEL[s.place.price_band] if (
            (budget is not None and _band_over(s.place.price_band, budget) <= 0)
            or (budget is None and s.place.price_band == "A")
        ) else None,
        "taste": f"좋아하는 {s.place.category}" if taste.likes.get(s.place.category, 0) > 0 else None,
        "distance": ("비 와서 가까운 곳" if req.rain else f"도보 {s.walk_to}분") if s.parts["distance"] > 0 else None,
        "crowd": None,       # 감점 요소라 칩으로는 보이지 않는다
        "reputation": None,  # 익명 피드백이 쌓이면 '평이 좋은 곳' 칩을 검토 ⏳
    }
    edge = {k: s.parts[k] - mean_parts.get(k, 0.0) for k in s.parts}
    ahead = [k for k in sorted(edge, key=lambda k: (-edge[k], k)) if edge[k] > 1e-9]
    rest = [k for k in sorted(s.parts, key=lambda k: (-s.parts[k], k)) if k not in ahead]
    chips = [labels[k] for k in ahead + rest if labels[k]][:2]
    solo = req.solo if req.solo is not None else req.party_size == 1
    if solo and s.place.solo_ok:
        chips.append("혼밥 OK")
    if s.place.hours_unknown:
        chips.insert(0, HOURS_UNKNOWN_CHIP)
    return tuple(chips[:3])


def _to_candidate(s: _Scored) -> MealCandidate:
    return MealCandidate(
        placeId=s.place.id, name=s.place.name, category=s.place.category,
        walkToMin=s.walk_to, walkBackMin=s.walk_back, needMin=s.need, slackMin=s.slack,
        fit=s.fit.value, score=round(s.score, 4), reasons=s.reasons,
        openUntil="" if s.place.hours_unknown else to_hhmm(s.open_until), priceBand=s.place.price_band or "",
    )


def _diverse_top(items: list[_Scored], n: int) -> tuple[list[_Scored], list[_Scored]]:
    """상위 n 곳의 카테고리가 겹치지 않게 (5절 다양성 · CA1-R31). 종류가 모자라면 점수순으로 채운다."""
    top: list[_Scored] = []
    seen: set[str] = set()
    for s in items:
        if len(top) == n:
            break
        if s.place.category not in seen:
            top.append(s)
            seen.add(s.place.category)
    for s in items:
        if len(top) == n:
            break
        if s not in top:
            top.append(s)
    rest = [s for s in items if s not in top]
    return top, rest


def recommend(
    places: Sequence[Place],
    req: MealRequest,
    taste: Taste = Taste(),
    reputation: Mapping[str, tuple[int, int]] | None = None,   # placeId → (👍 수, 전체 수)
    config: RankingConfig = DEFAULT_RANKING,
    router: Router | None = None,      # 길 기준 도보(meal.walkgraph) — 없으면 직선 × 1.3
) -> Recommendation:
    cfg = config.timing
    weights = config.weights.as_dict(req.rain)
    prior, rep = _reputation(reputation or {}, config)
    solo = req.solo if req.solo is not None else req.party_size == 1
    budget = req.budget if req.budget is not None else taste.budget_default

    excluded: Counter[str] = Counter()
    scored: list[_Scored] = []

    for p in places:
        # --- 반드시 빼는 것 (5절) --------------------------------------------
        if not p.has_attrs:
            excluded["정보 없음"] += 1
            continue
        if p.id in taste.never:
            excluded["다시 안 보기"] += 1
            continue
        if _matches(p, req.exclude, cfg):
            excluded["제외한 종류"] += 1
            continue
        if req.include and not _matches(p, req.include, cfg):
            excluded["요청한 종류 아님"] += 1
            continue
        if solo and p.solo_ok is False:     # 모름(None)은 빼지 않는다 — 조사 초기에도 추천이 나오게 (2026-11-12 결정)
            excluded["혼밥 불가"] += 1
            continue
        if req.party_size >= config.group_party and SEAT_ORDER.get(p.seats or "", -1) < SEAT_ORDER["M"]:
            excluded["좌석 부족"] += 1
            continue
        if req.budget is not None and _band_over(p.price_band, req.budget) > 0:
            excluded["예산 초과"] += 1
            continue

        walk_to = _walk(req.origin, (p.lat, p.lng), cfg, router)
        if req.max_walk_min is not None and walk_to > req.max_walk_min:
            excluded["도보 초과"] += 1
            continue
        walk_back = _walk(req.dest, (p.lat, p.lng), cfg, router) if req.dest else None   # 길은 양방향 — 다음 건물에서 한 번 계산해 재사용

        t = evaluate(req.slot_min, walk_to, walk_back, p.meal_type, depart_at=req.slot_start, config=cfg)
        if t.fit is Fit.OUT:
            excluded["시간 초과"] += 1
            continue

        arrive = req.slot_start + walk_to
        d = cfg.durations[p.meal_type]
        meal_end = arrive + (d.wait_peak if t.peak else d.wait) + d.eat
        interval = _open_interval(p, req.weekday, arrive, meal_end)
        if interval is None:
            excluded["영업 아님"] += 1
            continue
        deadline = _order_deadline(p, req.weekday, interval)
        if deadline is not None and arrive > deadline:
            excluded["주문 마감"] += 1
            continue

        # --- 점수 Σ wᵢ·fᵢ ---------------------------------------------------
        f = {
            "slack": min(max(t.slack_min, 0) / 20, 1.0),
            "budget": 1.0 if budget is None else (1.0 if (o := _band_over(p.price_band, budget)) <= 0 else 0.4 if o == 1 else 0.0),
            "taste": (max(-1.0, min(1.0, taste.likes.get(p.category, 0.0))) + 1) / 2,
            "distance": max(0.0, 1 - walk_to / 15),
            "crowd": 0.0 if t.peak and (p.meal_type is MealType.CAFETERIA or p.seats == "S") else 1.0,
            "reputation": rep.get(p.id, prior),
        }
        parts = {k: weights[k] * v for k, v in f.items()}
        score = sum(parts.values())
        if p.id not in taste.visited:
            score += config.explore_bonus
        if p.id in taste.recent:
            score -= config.repeat_penalty
        if req.hurry and p.meal_type in QUICK_TYPES:
            score += config.hurry_bonus

        scored.append(_Scored(p, walk_to, walk_back, t.need_min, t.slack_min, t.fit, interval[1], score, parts))

    if scored:
        mean_parts = {k: sum(s.parts[k] for s in scored) / len(scored) for k in scored[0].parts}
        for s in scored:
            s.reasons = _reasons(s, req, taste, mean_parts, budget)

    scored.sort(key=lambda s: (-s.score, s.need, s.place.id))
    cafeteria = [s for s in scored if s.place.meal_type is MealType.CAFETERIA]
    ok = [s for s in scored if s.fit is Fit.OK and s.place.meal_type is not MealType.CAFETERIA]
    tight = [s for s in scored if s.fit is Fit.TIGHT and s.place.meal_type is not MealType.CAFETERIA]
    top, more = _diverse_top(ok, config.top_n)

    return Recommendation(
        top=tuple(map(_to_candidate, top)),
        more=tuple(map(_to_candidate, more)),
        tight=tuple(map(_to_candidate, tight)),
        cafeteria=tuple(map(_to_candidate, cafeteria)),
        excluded=dict(excluded),
    )
