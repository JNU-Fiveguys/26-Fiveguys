"""식사 공강 계산 · 필요 시간 · 적합/빠듯/제외 판정 (정의서 CA1 4-가 · 4-다 · 5절).

이 모듈은 계산만 한다 — 저장·로그·네트워크가 없다.
식사 화면과 CA2 대화가 모두 이 함수들을 거쳐 같은 값을 받는다(코드 규칙 3 '계산기는 하나').

시각은 `"HH:MM"` 문자열(오늘 맥락 스키마의 `hhmm`, `"24:00"` 허용) 또는 자정부터의 분(int)으로 다룬다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping, Sequence


# ---------------------------------------------------------------------------
# 설정 값 (CA1-R23 — 관리자 화면에서 고칠 수 있게 코드 밖으로 뺄 값들)
# ---------------------------------------------------------------------------

class MealType(str, Enum):
    """5절 식사 유형. `PlaceAttr.mealType` 과 같은 값."""

    CAFETERIA = "cafeteria"  # 학식
    QUICK = "quick"          # 분식·덮밥·패스트푸드
    REGULAR = "regular"      # 일반 식당
    GROUP = "group"          # 고기·단체
    CAFE = "cafe"            # 카페·베이커리 ⏳


@dataclass(frozen=True)
class MealDuration:
    eat: int        # 식사(분)
    wait: int       # 대기(분) 평시
    wait_peak: int  # 대기(분) 피크


@dataclass(frozen=True)
class Window:
    """[start, end) 분 단위 구간."""

    start: int
    end: int

    def overlaps(self, other: "Window") -> bool:
        return self.start < other.end and other.start < self.end


def _default_durations() -> dict[MealType, MealDuration]:
    return {
        MealType.CAFETERIA: MealDuration(eat=20, wait=3, wait_peak=10),
        MealType.QUICK: MealDuration(eat=15, wait=2, wait_peak=7),
        MealType.REGULAR: MealDuration(eat=30, wait=3, wait_peak=8),
        MealType.GROUP: MealDuration(eat=60, wait=5, wait_peak=10),
        MealType.CAFE: MealDuration(eat=15, wait=3, wait_peak=5),
    }


@dataclass(frozen=True)
class TimingConfig:
    """5절 초기값(⏳). 실측(Q4)으로 보정하면 여기만 바꾼다."""

    detour: float = 1.3              # 우회 계수
    walk_m_per_min: float = 70.0     # 분속(m)
    buffer_min: int = 5              # 여유
    tight_min: int = 5               # 공강을 이만큼까지 넘으면 '빠듯'
    min_slot_min: int = 30           # 식사 공강 최소 길이 (CA1-R01)
    durations: Mapping[MealType, MealDuration] = field(default_factory=_default_durations)
    meal_windows: tuple[Window, ...] = (
        Window(11 * 60, 14 * 60),              # 점심 11:00~14:00
        Window(17 * 60, 19 * 60 + 30),         # 저녁 17:00~19:30 ⏳
    )
    peak_windows: tuple[Window, ...] = (
        Window(11 * 60 + 50, 12 * 60 + 30),    # 11:50~12:30 ⏳
        Window(17 * 60 + 50, 18 * 60 + 30),    # 17:50~18:30 ⏳
    )


DEFAULT_CONFIG = TimingConfig()


# ---------------------------------------------------------------------------
# 시각
# ---------------------------------------------------------------------------

def to_min(t: str | int) -> int:
    """`"HH:MM"` → 자정부터의 분. int 는 그대로."""
    if isinstance(t, int):
        return t
    hh, mm = t.split(":")
    h, m = int(hh), int(mm)
    if not (0 <= m < 60 and (0 <= h < 24 or (h == 24 and m == 0))):
        raise ValueError(f"잘못된 시각: {t!r}")
    return h * 60 + m


def to_hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def is_peak(at: str | int, config: TimingConfig = DEFAULT_CONFIG) -> bool:
    m = to_min(at)
    return any(w.start <= m < w.end for w in config.peak_windows)


# ---------------------------------------------------------------------------
# 도보
# ---------------------------------------------------------------------------

_EARTH_RADIUS_M = 6_371_000.0


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """두 좌표 사이 직선거리(m, 하버사인)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


def walk_minutes(straight_m: float, config: TimingConfig = DEFAULT_CONFIG) -> int:
    """도보(분) = ⌈ 직선거리(m) × 우회 계수 ÷ 분속 ⌉."""
    if straight_m < 0:
        raise ValueError("거리는 음수일 수 없다")
    # 부동소수 오차로 정수 경계가 한 칸 올라가지 않게 살짝 깎는다 (예: 700×1.3/70 = 13.000000000000002)
    return math.ceil(round(straight_m * config.detour / config.walk_m_per_min, 9))


def walk_minutes_path(path_m: float, config: TimingConfig = DEFAULT_CONFIG) -> int:
    """길을 따라 잰 거리일 때 — 이미 돌아가는 길이 들어 있어 우회 계수를 곱하지 않는다."""
    if path_m < 0:
        raise ValueError("거리는 음수일 수 없다")
    return math.ceil(round(path_m / config.walk_m_per_min, 9))


# ---------------------------------------------------------------------------
# 필요 시간 · 판정
# ---------------------------------------------------------------------------

class Fit(str, Enum):
    OK = "ok"        # 적합
    TIGHT = "tight"  # 빠듯
    OUT = "out"      # 제외


@dataclass(frozen=True)
class TimingResult:
    need_min: int    # 왕복 포함 필요 시간
    slack_min: int   # 여유 = 공강 − 필요 (빠듯이면 음수)
    fit: Fit
    peak: bool       # 도착 시각이 피크였는지


def need_minutes(
    walk_to: int,
    walk_back: int | None,
    meal_type: MealType | str,
    *,
    peak: bool = False,
    config: TimingConfig = DEFAULT_CONFIG,
) -> int:
    """필요 시간 = 도보(출발→식당) + 대기 + 식사 + 도보(식당→다음 건물) + 여유.

    `walk_back` 이 None 이면 다음 건물이 없는 것(그날 마지막 블록 뒤)이라 돌아오는 도보를 뺀다 (CA1-R04).
    """
    d = config.durations[MealType(meal_type)]
    wait = d.wait_peak if peak else d.wait
    return walk_to + wait + d.eat + (walk_back or 0) + config.buffer_min


def judge(need_min: int, slot_min: int, config: TimingConfig = DEFAULT_CONFIG) -> Fit:
    """필요 ≤ 공강 → 적합 · 필요 ≤ 공강 + 5 → 빠듯 · 그 밖 → 제외 (D5 · CA1-R21)."""
    if need_min <= slot_min:
        return Fit.OK
    if need_min <= slot_min + config.tight_min:
        return Fit.TIGHT
    return Fit.OUT


def evaluate(
    slot_min: int,
    walk_to: int,
    walk_back: int | None,
    meal_type: MealType | str,
    *,
    depart_at: str | int | None = None,
    config: TimingConfig = DEFAULT_CONFIG,
) -> TimingResult:
    """한 후보의 필요 시간과 판정. 피크 여부는 도착 시각(출발 + 도보)으로 정한다.

    `depart_at` 을 모르면 평시로 계산한다.
    """
    peak = depart_at is not None and is_peak(to_min(depart_at) + walk_to, config)
    need = need_minutes(walk_to, walk_back, meal_type, peak=peak, config=config)
    return TimingResult(need_min=need, slack_min=slot_min - need, fit=judge(need, slot_min, config), peak=peak)


# ---------------------------------------------------------------------------
# 식사 공강 (CA1-R01 · R04)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MealSlot:
    start: int
    end: int
    from_building: str | None  # 직전 블록 건물 (busy 면 없음 → 폰 위치·사용자 선택으로, CA1-R03)
    to_building: str | None    # 다음 블록 건물. 그날 마지막이면 None (CA1-R04)
    last_of_day: bool          # 다음 블록이 없음

    @property
    def minutes(self) -> int:
        return self.end - self.start

    @property
    def label(self) -> str:
        """`11:50~13:30 · 100분` (CA1-S01)."""
        return f"{to_hhmm(self.start)}~{to_hhmm(self.end)} · {self.minutes}분"


@dataclass(frozen=True)
class _Busy:
    start: int
    end: int
    building: str | None


def _merge(blocks: Iterable[Mapping]) -> list[_Busy]:
    """오늘 맥락 blocks → 시작순 정렬 · 겹치거나 맞닿은 블록 합치기. 합친 블록의 건물은 끝나는 쪽 블록 것."""
    items = sorted(
        (_Busy(to_min(b["start"]), to_min(b["end"]), b.get("building") or None) for b in blocks),
        key=lambda x: (x.start, x.end),
    )
    merged: list[_Busy] = []
    for b in items:
        if b.end < b.start:
            raise ValueError(f"끝이 시작보다 빠른 블록: {to_hhmm(b.start)}~{to_hhmm(b.end)}")
        if merged and b.start <= merged[-1].end:
            last = merged[-1]
            if b.end >= last.end:
                merged[-1] = _Busy(last.start, b.end, b.building)
            continue
        merged.append(b)
    return merged


def find_meal_slots(
    blocks: Sequence[Mapping],
    config: TimingConfig = DEFAULT_CONFIG,
) -> list[MealSlot]:
    """blocks 사이 빈 구간 중 30분 이상이면서 식사 시간대와 겹치는 구간.

    - 블록 사이 구간은 잘라내지 않고 통째로 쓴다 (10:15 수업 끝 · 13:00 시험 → `10:15~13:00`).
    - 마지막 블록 뒤는 식사 시간대 안쪽만 쓴다 (예: 15:00 끝 → `17:00~19:30`). 다음 건물은 없다.
    - 첫 블록 앞은 학교에 있는지 모르므로 잡지 않는다 — 그때는 직접 입력(CA1-R02).
    """
    busy = _merge(blocks)
    slots: list[MealSlot] = []

    for prev, nxt in zip(busy, busy[1:]):
        gap = Window(prev.end, nxt.start)
        if gap.end - gap.start >= config.min_slot_min and any(gap.overlaps(w) for w in config.meal_windows):
            slots.append(MealSlot(gap.start, gap.end, prev.building, nxt.building, last_of_day=False))

    if busy:
        last = busy[-1]
        for w in config.meal_windows:
            start = max(last.end, w.start)
            if w.end - start >= config.min_slot_min:
                slots.append(MealSlot(start, w.end, last.building, None, last_of_day=True))

    return slots


def next_meal_slot(
    blocks: Sequence[Mapping],
    now: str | int,
    config: TimingConfig = DEFAULT_CONFIG,
) -> MealSlot | None:
    """지금 진행 중이거나 다음에 올 식사 공강. 진행 중이면 남은 구간(지금부터)으로 줄여서 돌려준다."""
    n = to_min(now)
    for s in find_meal_slots(blocks, config):
        if s.end <= n:
            continue
        if s.start < n:
            if s.end - n < config.min_slot_min:
                continue
            return MealSlot(n, s.end, s.from_building, s.to_building, s.last_of_day)
        return s
    return None
