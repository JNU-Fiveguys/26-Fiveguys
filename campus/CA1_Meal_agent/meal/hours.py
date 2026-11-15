"""영업 여부 (CA1-R13) — 도착 예정 시각 기준으로 휴무 · 영업 전 · 브레이크 · 라스트오더 · 마감을 확인한다.

보수적으로 판단한다: 도착 ~ 식사 끝(timing 의 arriveAt ~ doneAt)이 **하나의 영업 구간 안에 통째로** 들어가야 하고,
주문 시각(도착 + 대기)이 라스트오더 이전이어야 한다. 브레이크 직전에 들어가 식사 중에 브레이크가 시작되는 경우도 뺀다
— 쫓겨나거나 주문을 못 하는 일이 '다녀올 수 있나' 판단을 깨기 때문이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from .settings import to_hhmm, to_min
from .timing import Timing
from .places import recommendation_ready, effective_hours, effective_closed_dates

DAYS = "월화수목금토일"  # date.weekday() 순서

Reason = Literal["open", "no_info", "closed_day", "not_yet", "break", "break_soon", "last_order", "closing", "closed"]

REASON_TEXT: dict[str, str] = {
    "open": "영업 중",
    "no_info": "영업시간 정보 없음",
    "closed_day": "오늘 휴무",
    "not_yet": "아직 영업 전",
    "break": "브레이크 타임",
    "break_soon": "식사 중 브레이크 시작",
    "last_order": "라스트오더 지남",
    "closing": "식사 중 마감",
    "closed": "영업 종료",
}


@dataclass(frozen=True)
class OpenStatus:
    ok: bool
    reason: Reason
    open_until: int | None = None  # ok 일 때: 지금 구간이 끝나는 시각(브레이크 시작 또는 마감)
    opens_at: int | None = None    # not_yet · break · closed 일 때: 다음에 여는 시각(같은 날, 있으면)

    @property
    def text(self) -> str:
        return REASON_TEXT[self.reason]

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "text": self.text,
            "openUntil": to_hhmm(self.open_until) if self.open_until is not None else None,
            "opensAt": to_hhmm(self.opens_at) if self.opens_at is not None else None,
        }


@dataclass(frozen=True)
class _Segment:
    start: int
    end: int
    last_order: int | None
    ends_with_break: bool


def _day_name(weekday: int | str) -> str:
    if isinstance(weekday, int):
        if not 0 <= weekday <= 6:
            raise ValueError("weekday 는 0(월) ~ 6(일)")
        return DAYS[weekday]
    if weekday not in DAYS or len(weekday) != 1:
        raise ValueError(f"알 수 없는 요일: {weekday}")
    return weekday


def _segments(hours: list[dict], day: str) -> tuple[list[_Segment], list[tuple[int, int]]]:
    """그날의 영업 구간들과 브레이크 구간들.

    break 가 있는 행의 lastOrder 는 마지막 영업 구간에만 적용한다.
    점심·저녁 라스트오더가 따로 있으면 hours 행을 나눠 기록해야 한다.
    """
    segs: list[_Segment] = []
    breaks: list[tuple[int, int]] = []
    for h in hours:
        if day not in h["days"]:
            continue
        o, c = to_min(h["open"]), to_min(h["close"])
        lo = to_min(h["lastOrder"]) if "lastOrder" in h else None
        if "break" in h:
            bs, be = to_min(h["break"]["start"]), to_min(h["break"]["end"])
            if lo is not None and not (be < lo <= c):
                raise ValueError(
                    "break 가 있는 행의 lastOrder 는 브레이크 종료 후부터 마감 사이여야 합니다. "
                    "점심·저녁 라스트오더는 hours 행을 나눠 기록하세요"
                )
            segs.append(_Segment(o, bs, None, True))
            segs.append(_Segment(be, c, lo, False))
            breaks.append((bs, be))
        else:
            segs.append(_Segment(o, c, lo, False))
    segs.sort(key=lambda s: s.start)
    return segs, breaks


def check_open(place: dict, weekday: int | str, *, arrive: int, order_at: int, done: int, on_date: date | None = None) -> OpenStatus:
    """도착 · 주문 · 식사 끝 시각(자정부터 분)으로 영업 여부를 판정한다."""
    if not (arrive <= order_at <= done):
        raise ValueError("arrive ≤ order_at ≤ done 이어야 합니다")
    if not recommendation_ready(place):
        return OpenStatus(False, "no_info")
    if on_date is not None and on_date.isoformat() in effective_closed_dates(place):
        return OpenStatus(False, "closed_day")
    from .availability import fresh_hours_observation
    monthly = place.get("hoursObservation", {}).get("monthlyClosed", []) if fresh_hours_observation(place) else []
    if monthly:
        if on_date is None:
            return OpenStatus(False, "no_info")
        for rule in monthly:
            if DAYS[on_date.weekday()] == rule["day"] and (on_date.day - 1) // 7 + 1 in rule["weeks"]:
                return OpenStatus(False, "closed_day")
    segs, breaks = _segments(effective_hours(place), _day_name(weekday))
    if not segs:
        return OpenStatus(False, "closed_day")

    later = [s.start for s in segs if s.start > arrive]
    next_open = min(later) if later else None

    seg = next((s for s in segs if s.start <= arrive < s.end), None)
    if seg is None:
        if any(bs <= arrive < be for bs, be in breaks):
            return OpenStatus(False, "break", opens_at=next_open)
        if arrive < segs[0].start:
            return OpenStatus(False, "not_yet", opens_at=next_open)
        return OpenStatus(False, "closed", opens_at=next_open)

    if seg.last_order is not None and order_at > seg.last_order:
        return OpenStatus(False, "last_order")
    if done > seg.end:
        return OpenStatus(False, "break_soon" if seg.ends_with_break else "closing")
    return OpenStatus(True, "open", open_until=seg.end)


def check_timing(place: dict, weekday: int | str, timing: Timing, *, on_date: date | None = None) -> OpenStatus:
    """timing.compute_for_place 결과로 바로 판정한다 (주문 = 도착 + 대기)."""
    return check_open(
        place,
        weekday,
        arrive=timing.arrive_at,
        order_at=timing.arrive_at + timing.breakdown.wait,
        done=timing.done_at,
        on_date=on_date,
    )
