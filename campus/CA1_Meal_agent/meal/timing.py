"""필요 시간 · 판정 · 출발 마감 시각 (기능 정의서 CA1 5절, CA1-R20~R22 · R04).

    필요 시간 = 도보(출발 → 식당) + 대기 + 식사 + 도보(식당 → 다음 건물) + 여유
    도보(분)  = ⌈ OSM 보행 경로(m) ÷ 분속 ⌉ (경로가 없으면 직선거리 × 우회 계수)
    판정      = 필요 ≤ 공강 → ok · 필요 ≤ 공강 + 빠듯 여유 → tight · 그 밖 → out

시각은 모두 '자정부터 분'(int). 화면용 'HH:MM' 은 to_hhmm 으로 바꾼다.
영업 여부는 여기서 보지 않는다 — arriveAt 을 hours 검사에 넘긴다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Literal

from .places import distance_m, recommendation_ready, meal_type_for_timing
from .routing import PedestrianRouter, WalkRoute
from .settings import Settings, load_settings, to_hhmm

LatLng = tuple[float, float]
Fit = Literal["ok", "tight", "out"]


@dataclass(frozen=True)
class Breakdown:
    walk_to: int
    wait: int
    eat: int
    walk_back: int  # 다음 건물이 없으면 0
    buffer: int

    @property
    def walk_total(self) -> int:
        """출발 → 식당 → 다음 건물의 전체 도보. 다음 건물이 없으면 편도."""
        return self.walk_to + self.walk_back

    @property
    def total(self) -> int:
        return self.walk_to + self.wait + self.eat + self.walk_back + self.buffer

    def to_dict(self) -> dict:
        return {"walkTo": self.walk_to, "wait": self.wait, "eat": self.eat,
                "walkBack": self.walk_back, "buffer": self.buffer}


@dataclass(frozen=True)
class Timing:
    need_min: int
    gap_min: int | None     # 공강 길이. 끝이 없으면 None
    slack_min: int | None   # 공강 − 필요 (빠듯이면 음수)
    fit: Fit
    arrive_at: int          # 식당 도착 (영업 여부 판단 기준)
    done_at: int            # 식사 끝
    leave_by: int | None    # 이때까지 식당에서 나오면 다음 수업에 늦지 않는다
    peak: bool
    breakdown: Breakdown
    route_to: WalkRoute | None = None
    route_back: WalkRoute | None = None

    def to_dict(self) -> dict:
        return {
            "needMin": self.need_min,
            "totalWalkMin": self.breakdown.walk_total,
            "gapMin": self.gap_min,
            "slackMin": self.slack_min,
            "fit": self.fit,
            "arriveAt": to_hhmm(self.arrive_at),
            "doneAt": to_hhmm(self.done_at),
            "leaveBy": to_hhmm(self.leave_by) if self.leave_by is not None else None,
            "peak": self.peak,
            "breakdown": self.breakdown.to_dict(),
            "walkRoutes": {
                "to": self.route_to.to_dict() if self.route_to is not None else None,
                "back": self.route_back.to_dict() if self.route_back is not None else None,
            },
        }


def walk_min(a: LatLng, b: LatLng, s: Settings | None = None) -> int:
    """기존 직선거리 기반 편도 추정(비교용). 추천은 compute_for_place의 OSM 경로를 쓴다."""
    s = s or load_settings()
    d = distance_m(a[0], a[1], b[0], b[1])
    if d < 1:
        return 0
    return math.ceil(d * s.detour_factor / s.meters_per_min)


def load_router(max_snap_m: float = 150.0) -> PedestrianRouter:
    """공개 보행망만 메모리에 보관한다. 사용자 이동 경로는 파일에 저장하지 않는다."""
    return PedestrianRouter.from_file(max_snap_m=max_snap_m)


def is_peak(minute: int, s: Settings | None = None) -> bool:
    s = s or load_settings()
    return any(a <= minute < b for a, b in s.peaks)


def compute(
    *,
    start: int,
    end: int | None,
    walk_to: int,
    walk_back: int | None,
    meal_type: str,
    s: Settings | None = None,
) -> Timing:
    """이미 구한 도보 분으로 필요 시간과 판정을 낸다.

    start     공강 시작(또는 지금)
    end       다음 블록 시작. None 이면 끝이 정해지지 않은 시간(그날 마지막 블록 뒤)
    walk_back 식당 → 다음 건물 도보. None 이면 다음 건물이 없다 → 계산에서 뺀다 (CA1-R04)
    """
    s = s or load_settings()
    if meal_type not in s.meal_types:
        raise ValueError(f"알 수 없는 식사 유형: {meal_type}")
    if end is not None and end < start:
        raise ValueError("공강 끝이 시작보다 빠릅니다")
    if walk_to < 0 or (walk_back is not None and walk_back < 0):
        raise ValueError("도보 시간은 0 이상이어야 합니다")

    times = s.meal_types[meal_type]
    arrive = start + walk_to
    peak = is_peak(arrive, s)
    wait = times.wait_peak if peak else times.wait
    back = walk_back or 0
    # 여유는 '다음 일정에 늦지 않기' 위한 것이라 끝이 없으면 붙이지 않는다
    buffer = s.buffer_min if end is not None else 0

    bd = Breakdown(walk_to=walk_to, wait=wait, eat=times.eat, walk_back=back, buffer=buffer)
    need = bd.total
    done = arrive + wait + times.eat

    if end is None:
        return Timing(need_min=need, gap_min=None, slack_min=None, fit="ok",
                      arrive_at=arrive, done_at=done, leave_by=None, peak=peak, breakdown=bd)

    gap = end - start
    slack = gap - need
    if need <= gap:
        fit: Fit = "ok"
    elif need <= gap + s.tight_margin_min:
        fit = "tight"
    else:
        fit = "out"

    return Timing(need_min=need, gap_min=gap, slack_min=slack, fit=fit,
                  arrive_at=arrive, done_at=done, leave_by=end - back - buffer,
                  peak=peak, breakdown=bd)


def compute_for_place(
    place: dict,
    *,
    start: int,
    end: int | None,
    origin: LatLng,
    dest: LatLng | None,
    s: Settings | None = None,
    router=None,
) -> Timing | None:
    """확인된 메뉴·영업시간이 있어야 계산한다. 식사유형 미확인 시 일반 설정 사용."""
    attrs = place.get("attrs")
    if not recommendation_ready(place):
        return None
    s = s or load_settings()
    here = (place["lat"], place["lng"])
    router = router if router is not None else load_router(s.max_snap_m)
    options = {"meters_per_min": s.meters_per_min, "detour_factor": s.detour_factor}
    to_route = router.route(origin, here, **options)
    back_route = router.route(here, dest, **options) if dest is not None else None
    timing = compute(
        start=start,
        end=end,
        walk_to=to_route.duration_min,
        walk_back=back_route.duration_min if back_route is not None else None,
        meal_type=meal_type_for_timing(place),
        s=s,
    )
    return replace(timing, route_to=to_route, route_back=back_route)
