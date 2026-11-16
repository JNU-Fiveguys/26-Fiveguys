"""PR #6 timeline, using the same validated daily context as recommendations."""
from datetime import datetime

from .settings import to_hhmm
from .timetable import SEOUL, TimetableError, _day_slots, resolve_context, resolve_location


def daily_timeline(snapshot, day):
    result = {"date": day.isoformat(), "status": snapshot.summary["status"],
              "start": "09:00", "end": "20:00", "classes": [], "slots": [],
              "warnings": snapshot.summary.get("warnings", [])}
    if snapshot.summary["status"] != "ready":
        return result
    context = snapshot.require_usable()
    blocks, _ = _day_slots(context, day)
    occupied = []
    for block in blocks:
        # Course titles and identifiers are unnecessary for choosing a lunch gap.
        try:
            building = resolve_location(block.room).label
        except TimetableError:
            building = "위치 확인 필요"
        result["classes"].append({"start": to_hhmm(block.start), "end": to_hhmm(block.end),
                                  "kind": block.kind, "building": building})
        if occupied and block.start <= occupied[-1][1]:
            occupied[-1][1] = max(occupied[-1][1], block.end)
        else:
            occupied.append([block.start, block.end])
    for index, (_, release) in enumerate(occupied):
        next_start = occupied[index + 1][0] if index + 1 < len(occupied) else None
        start, end = max(9 * 60, release), min(20 * 60, next_start or 20 * 60)
        if end - start < 30:
            continue
        # The 09–20 axis clips drawing only; the next class remains the real deadline.
        slot = {"start": to_hhmm(start), "end": to_hhmm(next_start if next_start is not None else end), "lastOfDay": next_start is None,
                "enabled": True, "reason": ""}
        try:
            selection = resolve_context(context, at=datetime.combine(day, datetime.min.time(), SEOUL).replace(
                hour=start // 60, minute=start % 60))
            slot.update(origin=selection.origin_label, destination=selection.destination_label)
        except TimetableError as exc:
            slot.update(enabled=False, reason=str(exc), origin=None, destination=None)
        result["slots"].append(slot)
    return result
