"""기존 F3 시간표의 실제 수업 회차 → 식사 맥락.

학사 앱의 GET /api/attendance/summary 계약을 읽는다. 휴강·보강·학기 중
시간표 변경은 F3가 계산한 sessions를 그대로 사용하고 주간 시간표를 재생성하지
않는다. 학사 모듈을 import하지 않으며, 개인 시간표를 파일에 저장하거나 로그하지 않는다.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .ranking import MealContext
from .settings import to_hhmm

BUILDINGS_PATH = Path(__file__).resolve().parent.parent / "data" / "buildings.json"
DEFAULT_ACADEMIC_URL = "http://127.0.0.1:8020"
SEOUL = timezone(timedelta(hours=9), name="Asia/Seoul")
LocationInput = str | tuple[float, float] | list[float]
# Exact labels only: a building such as "온라인교육관" must still resolve as a building.
ONLINE_ROOM_LABELS = frozenset({
    "온라인", "온라인수업", "온라인강의", "비대면", "비대면수업", "비대면강의",
    "원격", "원격수업", "원격강의", "online", "remote",
})


class TimetableError(ValueError):
    """사용자가 시간표 또는 직접 입력을 보완할 수 있는 오류."""


def _minute(value: str) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise TimetableError("시각은 HH:MM 형식이어야 합니다.")
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def _coords(value) -> tuple[float, float]:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise TimetableError("좌표는 [위도, 경도] 두 숫자여야 합니다.")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        raise TimetableError("좌표는 유한한 숫자여야 합니다.")
    lat, lng = map(float, value)
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise TimetableError("위도·경도 범위를 확인하세요.")
    return lat, lng


def _key(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _is_online_room(value: str) -> bool:
    return _key(value) in ONLINE_ROOM_LABELS


def load_buildings(path: str | Path = BUILDINGS_PATH) -> list[dict]:
    """OSM 출처를 가진 건물 목록. 자동 생성한 별칭도 충돌하면 거부한다."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    buildings = data.get("buildings") if isinstance(data, dict) else data
    if not isinstance(buildings, list):
        raise TimetableError("건물 목록 형식이 잘못되었습니다.")
    seen: dict[str, str] = {}
    for b in buildings:
        if not isinstance(b, dict) or not all(isinstance(b.get(k), str) and b[k].strip() for k in ("code", "name")):
            raise TimetableError("건물에는 code와 name이 필요합니다.")
        _coords((b.get("lat"), b.get("lng")))
        aliases = b.get("aliases", [])
        if not isinstance(aliases, list) or any(not isinstance(a, str) or not a.strip() for a in aliases):
            raise TimetableError("건물 aliases는 비어 있지 않은 문자열 목록이어야 합니다.")
        for alias in [b["code"], b["name"], *aliases]:
            key = _key(alias)
            if key in seen and seen[key] != b["code"]:
                raise TimetableError(f"건물 별칭이 중복되었습니다: {alias}")
            seen[key] = b["code"]
    return buildings


@dataclass(frozen=True)
class ResolvedLocation:
    coords: tuple[float, float]
    label: str
    code: str | None = None
    coordinate_type: str = "user_coordinate"

    def to_dict(self) -> dict:
        return {"code": self.code, "name": self.label, "lat": self.coords[0],
                "lng": self.coords[1], "coordinateType": self.coordinate_type}


def resolve_location(value: LocationInput, buildings: list[dict] | None = None) -> ResolvedLocation:
    """건물 코드·이름·강의실(공7-223), 또는 명시 좌표를 해석한다."""
    if not isinstance(value, str):
        return ResolvedLocation(_coords(value), "직접 지정한 위치")
    value = value.strip()
    if not value:
        raise TimetableError("건물 또는 강의실을 입력하세요.")
    coordinate = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", value)
    if coordinate:
        return ResolvedLocation(_coords(tuple(map(float, coordinate.groups()))), "직접 지정한 위치")
    buildings = load_buildings() if buildings is None else buildings
    requested = _key(value)
    hits: dict[str, dict] = {}
    for b in buildings:
        for alias in [b["code"], b["name"], *b.get("aliases", [])]:
            key = _key(alias)
            # 강의실 번호는 구분자가 있을 때만 분리한다(공7을 공70으로 오인하지 않음).
            roomless = re.sub(r"(?:\s*[-_:]\s*|\s+)[A-Za-z]?\d{1,4}(?:호)?$", "", value)
            if requested == key or _key(roomless) == key:
                hits[b["code"]] = b
    if len(hits) != 1:
        raise TimetableError("강의실의 건물을 찾지 못했거나 모호합니다. 목록에서 건물을 선택하거나 위도,경도를 직접 입력하세요.")
    b = next(iter(hits.values()))
    return ResolvedLocation(_coords((b["lat"], b["lng"])), b["name"], b["code"],
                            b.get("coordinateType", "building_centroid"))


def sanitize_attendance(overview: dict) -> dict:
    """F3 응답에서 위치 해석에 필요한 시각·강의실만 남긴다. 과목명·성적·메모 제거."""
    if not isinstance(overview, dict) or not isinstance(overview.get("courses"), list):
        raise TimetableError("학사 시간표 응답 형식이 맞지 않습니다. 학사 앱 상태를 확인하거나 직접 입력 모드를 선택하세요.")
    result = {"semester": {}, "current": overview.get("current"), "courses": []}
    semester = overview.get("semester")
    if isinstance(semester, dict):
        result["semester"] = {k: semester.get(k) for k in ("id", "start", "end")}
    for c in overview["courses"]:
        if not isinstance(c, dict) or not isinstance(c.get("sessions"), list):
            raise TimetableError("시간표 courses[].sessions 형식을 확인하세요.")
        row = {"excluded": bool(c.get("excluded", False)), "sessions": []}
        timetable = c.get("timetable")
        if isinstance(timetable, dict):
            # 내용·과목 식별자 대신 입력 여부만 전달한다.
            row["hasTimetable"] = bool(timetable.get("meetings"))
        elif "hasTimetable" in c:
            row["hasTimetable"] = bool(c["hasTimetable"])
        for s in c["sessions"]:
            if not isinstance(s, dict):
                raise TimetableError("시간표 회차 형식을 확인하세요.")
            row["sessions"].append({k: s.get(k) for k in ("date", "start", "end", "room", "state", "kind")})
        result["courses"].append(row)
    return result


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_attendance(base_url: str = DEFAULT_ACADEMIC_URL, semester: str | None = None,
                     *, timeout: float = 5.0) -> dict:
    """같은 PC의 정상 학사 API만 읽는다. 인증을 우회하거나 쿠키를 추출하지 않는다."""
    parts = urlsplit(base_url)
    try:
        port = parts.port
    except ValueError as exc:
        raise TimetableError("학사 앱 포트가 올바르지 않습니다.") from exc
    if (parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parts.username or parts.password or parts.query or parts.fragment
            or parts.path not in {"", "/"} or (port is not None and not 1 <= port <= 65535)):
        raise TimetableError("학사 앱 주소는 같은 PC의 http://127.0.0.1:포트 형식이어야 합니다.")
    url = base_url.rstrip("/") + "/api/attendance/summary"
    if semester:
        if not isinstance(semester, str) or not re.fullmatch(r"\d{4}-[12]", semester):
            raise TimetableError("학기 형식은 YYYY-1 또는 YYYY-2입니다.")
        url += "?" + urlencode({"semester": semester})
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(Request(url, headers={"Accept": "application/json"}), timeout=timeout) as response:
            # 개인 데이터의 무제한 읽기와 의도하지 않은 응답을 막는다.
            raw = response.read(8_000_001)
            if len(raw) > 8_000_000:
                raise TimetableError("시간표 응답이 너무 큽니다.")
            result = json.loads(raw.decode("utf-8"))
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise TimetableError("학사 앱 인증이 필요합니다. 학사 앱을 확인하거나 출발·다음 강의실과 시각을 직접 입력하세요.") from exc
        raise TimetableError(f"학사 앱에서 시간표를 읽지 못했습니다(HTTP {exc.code}).") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise TimetableError("학사 앱에 연결할 수 없습니다. 로컬 앱을 확인하거나 직접 입력 모드를 선택하세요.") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise TimetableError("학사 앱이 올바른 시간표 응답을 반환하지 않았습니다.") from exc
    return sanitize_attendance(result)


@dataclass(frozen=True)
class _Slot:
    start: int
    end: int
    room: str
    kind: str = "class"


def _day_slots(overview: dict, day: date) -> tuple[list[_Slot], list[str]]:
    if isinstance(overview, dict) and "blocks" in overview:
        if overview.get("date") != day.isoformat():
            raise TimetableError("시간표 날짜가 선택한 날짜와 다릅니다.")
        if not isinstance(overview["blocks"], list):
            raise TimetableError("오늘 맥락의 blocks는 목록이어야 합니다.")
        slots = []
        for block in overview["blocks"]:
            if not isinstance(block, dict) or block.get("kind") not in {"class", "exam", "busy"}:
                raise TimetableError("오늘 맥락의 kind는 class, exam, busy여야 합니다.")
            if block.get("state") == "canceled":
                continue
            start, end = _minute(block.get("start")), _minute(block.get("end"))
            if end <= start:
                raise TimetableError("시간표 수업 종료는 시작보다 늦어야 합니다.")
            building = block.get("building") or ""
            if not isinstance(building, str):
                raise TimetableError("오늘 맥락의 building은 건물 코드 문자열이어야 합니다.")
            slots.append(_Slot(start, end, building.strip(), block["kind"]))
        return sorted(slots, key=lambda s: (s.start, s.end, s.room)), []
    overview = sanitize_attendance(overview)
    warnings: list[str] = []
    semester = overview["semester"]
    for key in ("start", "end"):
        if semester.get(key):
            try:
                bound = date.fromisoformat(semester[key])
            except (ValueError, TypeError) as exc:
                raise TimetableError("시간표 학기 날짜 형식이 잘못되었습니다.") from exc
            if (key == "start" and day < bound) or (key == "end" and day > bound):
                return [], ["선택한 날짜는 시간표 학기 기간 밖입니다."]
    live = [c for c in overview["courses"] if not c["excluded"]]
    if not live:
        raise TimetableError("사용할 시간표가 없습니다. 출발 위치와 다음 강의실·시작 시각을 직접 입력하세요.")
    if any(c.get("hasTimetable") is False and not c["sessions"] for c in live):
        raise TimetableError("시간표가 입력되지 않은 과목이 있습니다. 다음 강의실·시작 시각을 직접 지정하거나 학사 앱 시간표를 완성하세요.")
    if ("start" in semester or "end" in semester) and not (semester.get("start") and semester.get("end")):
        raise TimetableError("학기 시작·종료일이 없어 실제 수업 회차를 확인할 수 없습니다. 학사 앱에서 학기를 설정하거나 직접 입력하세요.")
    slots = []
    for c in live:
        for s in c["sessions"]:
            if s["date"] != day.isoformat():
                continue
            if s["state"] == "canceled":
                continue
            if s["state"] != "scheduled":
                raise TimetableError("시간표 회차의 scheduled/canceled 상태를 확인하세요.")
            start, end = _minute(s["start"]), _minute(s["end"])
            if end <= start:
                raise TimetableError("시간표 수업 종료는 시작보다 늦어야 합니다.")
            if s["room"] is not None and not isinstance(s["room"], str):
                raise TimetableError("시간표 강의실은 문자열이어야 합니다.")
            slots.append(_Slot(start, end, (s["room"] or "").strip()))
    return sorted(slots, key=lambda s: (s.start, s.end, s.room)), warnings


@dataclass(frozen=True)
class ContextSelection:
    context: MealContext
    date: str
    origin_source: str
    destination_source: str
    origin_location: ResolvedLocation
    destination_location: ResolvedLocation | None
    requested_start: str
    warnings: tuple[str, ...] = ()

    @property
    def origin_label(self) -> str:
        return self.origin_location.label

    @property
    def destination_label(self) -> str | None:
        return self.destination_location.label if self.destination_location else None

    @property
    def next_class_start(self) -> str | None:
        return to_hhmm(self.context.end) if self.context.end is not None else None

    def to_dict(self) -> dict:
        return {"date": self.date, "start": to_hhmm(self.context.start), "end": self.next_class_start,
                "requestedStart": self.requested_start, "originSource": self.origin_source,
                "destinationSource": self.destination_source,
                "origin": self.origin_location.to_dict(),
                "destination": self.destination_location.to_dict() if self.destination_location else None,
                "warnings": list(self.warnings)}


def resolve_context(overview: dict | None, *, at: datetime, origin: LocationInput | None = None,
                    next_room: LocationInput | None = None, next_start: str | None = None,
                    no_next_class: bool = False, buildings: list[dict] | None = None,
                    rain: bool = False, exam_period: bool = False) -> ContextSelection:
    """직접 목적지 > 시간표 목적지. 알 수 없는 강의실은 '다음 수업 없음'으로 숨기지 않는다.

    naive datetime은 한국 현지 시각으로 취급한다. 수업 중이어도 식사 시작을 미루지 않고
    고른 시각 그대로 계산한다(2026-11-12 결정).
    origin을 생략하면 지금 듣고 있는 수업, 없으면 가장 최근에 끝난 수업의 건물을 사용한다.
    명시된 온라인 수업은 다음 수업 시작 시각만 반영하며 수강 위치는 추측하지 않는다.
    """
    if not isinstance(at, datetime):
        raise TimetableError("날짜·시각은 datetime이어야 합니다.")
    local = at.replace(tzinfo=SEOUL) if at.tzinfo is None else at.astimezone(SEOUL)
    if local.second or local.microsecond:
        local = local.replace(second=0, microsecond=0) + timedelta(minutes=1)
    minute = local.hour * 60 + local.minute
    requested_start = to_hhmm(minute)
    if no_next_class and (next_room is not None or next_start is not None):
        raise TimetableError("다음 수업 없음과 다음 강의실·시각을 동시에 지정할 수 없습니다.")
    manual_complete = origin is not None and (no_next_class or (next_room is not None and next_start is not None))
    if isinstance(overview, dict) and "blocks" in overview and overview.get("date") != local.date().isoformat():
        raise TimetableError("시간표 날짜가 선택한 날짜와 다릅니다.")
    warnings: list[str] = []
    slots: list[_Slot] = []
    if overview is not None:
        try:
            slots, warnings = _day_slots(overview, local.date())
        except TimetableError:
            if not manual_complete:
                raise
            warnings.append("시간표를 확인할 수 없어 직접 입력한 위치·시각으로 계산합니다.")
    elif not manual_complete:
        raise TimetableError("시간표가 없으면 출발 위치와 다음 강의실·시각을 지정하거나 다음 수업 없음을 선택하세요.")

    # 수업 중이어도 시작 시각을 조정하지 않는다.
    start = minute

    if origin is not None:
        from_location, from_source = resolve_location(origin, buildings), "manual"
    else:
        # 지금 듣고 있는 수업이 있으면 그 건물, 없으면 가장 최근에 끝난 수업의 건물
        active = [s for s in slots if s.start <= start < s.end]
        previous = active or [s for s in slots if s.end <= start]
        if not previous:
            raise TimetableError("직전 수업 위치가 없습니다. 출발 건물을 직접 선택하세요.")
        latest_end = max(s.end for s in previous)
        rooms = {s.room for s in previous if s.end == latest_end}
        if any(_is_online_room(room) for room in rooms):
            raise TimetableError("직전 수업에 온라인 수업이 있어 현재 위치를 알 수 없습니다. 출발 건물을 직접 선택하세요.")
        if len(rooms) != 1 or not next(iter(rooms)):
            raise TimetableError("직전 수업의 건물이 없거나 겹칩니다. 출발 건물을 직접 선택하세요.")
        from_location, from_source = resolve_location(next(iter(rooms)), buildings), "timetable"

    future = [s for s in slots if s.start >= start]
    upcoming = [s for s in future if s.start == future[0].start] if future else []
    deadline = _minute(next_start) if next_start is not None else (upcoming[0].start if upcoming else None)
    if no_next_class:
        destination, destination_source, deadline = None, "manual_none", None
    elif next_room is not None:
        if deadline is None:
            raise TimetableError("다음 강의실을 직접 지정하면 시작 시각도 입력하세요.")
        destination, destination_source = resolve_location(next_room, buildings), "manual"
    elif upcoming:
        rooms = {s.room for s in upcoming}
        if all(s.kind == "busy" and not s.room for s in upcoming):
            destination, destination_source = None, "timetable_busy"
            warnings.append("다음 일정의 위치가 없어 복귀 도보를 계산하지 않았습니다. 필요하면 다음 건물을 직접 지정하세요.")
        elif all(s.kind in {"class", "exam"} and _is_online_room(s.room) for s in upcoming):
            destination, destination_source = None, "online"
            warnings.append("다음 수업은 온라인이므로 건물 이동 없이 수업 시작 시각만 반영합니다.")
        elif len(rooms) != 1 or not next(iter(rooms)):
            raise TimetableError("다음 수업의 강의실이 없거나 같은 시각에 겹칩니다. 다음 강의실을 직접 지정하세요.")
        else:
            destination = resolve_location(next(iter(rooms)), buildings)
            destination_source = "timetable"
    elif next_start is not None:
        raise TimetableError("다음 시작 시각을 입력하면 강의실도 직접 지정하세요.")
    else:
        destination, destination_source = None, "no_next_class"
    if deadline is not None and deadline <= start:
        raise TimetableError("다음 수업 시작 시각은 식사 시작보다 늦어야 합니다.")
    if next_start is not None and upcoming and deadline > upcoming[0].start and not no_next_class:
        warnings.append("직접 입력한 시작 시각이 시간표의 다음 수업보다 늦습니다.")
    if isinstance(overview, dict):
        exam_period = exam_period or bool(overview.get("examPeriod", False))
    context = MealContext(local.weekday(), start, deadline, from_location.coords,
                          destination.coords if destination else None, rain=rain, exam_period=exam_period,
                          on_date=local.date())
    return ContextSelection(context, local.date().isoformat(), from_source, destination_source,
                            from_location, destination, requested_start, tuple(warnings))
