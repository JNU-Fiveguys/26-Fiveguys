"""시간표 → 식사 공강 — univ_us 의 F3 출결 '오늘 수업'(GET /api/attendance/today)을 그대로 받는다.

수업 한 줄 모양(F3 service.today 의 classes): {"name", "start": "HH:MM", "end": "HH:MM", "room": "공7-223", "state": "scheduled"|"canceled"}
- 휴강(state=canceled) 수업은 빼고 계산한다 → 공강이 길어진다
- 강의실 `공7-223` → 건물 `공과대 7호관` 을 찾아 출발·도착 건물로 쓴다. 못 찾으면 None(화면이 고르게 한다)
- 공강 찾기 규칙은 timing.find_meal_slots 그대로(첫 수업 앞은 잡지 않음 · 마지막 수업 뒤는 식사 시간대 안쪽만)

강의실 코드 → 건물은 정의서 '건물 마스터'(데이터·인프라, /v1/buildings)가 나오기 전까지 쓰는 임시 규칙이다.
  1. directory/room_codes.json 에 적은 것(사람이 확인한 것)이 먼저
  2. 없으면 건물 이름에서 만든 코드: `공과대 7호관` → `공7`, `자연과학대학 1호관` → `자1` (추정 — 팀 확인 필요)
  3. 강의실 글자에 건물 이름이 그대로 들어 있으면(`진리관 101`) 그 건물
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Iterable, Mapping

from .timing import DEFAULT_CONFIG, MealSlot, TimingConfig, find_meal_slots, to_hhmm

AGENT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ROOM_CODES = AGENT_DIR / "directory" / "room_codes.json"
F3_AGENT_DIR = Path(os.environ.get("F3_AGENT_DIR") or AGENT_DIR.parent.parent / "F3_Attendance_agent")

_HOGWAN = re.compile(r"^(?P<head>[가-힣A-Za-z]+?)\s*(?P<n>\d+)호관$")
_ROOM = re.compile(r"^\s*(?P<code>[가-힣A-Za-z]+\d*[A-Za-z]?)\s*[-\s]")
_ROOM_NODASH = re.compile(r"^\s*(?P<code>[가-힣A-Za-z]+)\d{2,4}\s*$")   # `생활100` — 코드 바로 뒤에 호실 번호


def auto_codes(names: Iterable[str]) -> dict[str, str]:
    """건물 이름 → 강의실 코드(추정). `공과대 7호관` → `공7`. 같은 코드가 두 건물에 나오면 둘 다 버린다."""
    seen: dict[str, str | None] = {}
    for name in names:
        m = _HOGWAN.match(name.strip())
        if not m or name.startswith("생활관"):
            continue
        code = m["head"][0] + m["n"]
        seen[code] = None if code in seen and seen[code] != name else name
    return {c: n for c, n in seen.items() if n}


def load_room_codes(path: Path = DEFAULT_ROOM_CODES) -> dict[str, str]:
    if not Path(path).exists():
        return {}
    return dict(json.loads(Path(path).read_text(encoding="utf-8")).get("codes", {}))


def room_building(room: str | None, buildings: Mapping[str, object], codes: Mapping[str, str]) -> str | None:
    """강의실 글자 → 건물 이름(buildings 의 키). 모르면 None."""
    if not room:
        return None
    room = room.strip()
    for pat in (_ROOM, _ROOM_NODASH):
        m = pat.match(room)
        if m and m["code"] in codes and codes[m["code"]] in buildings:
            return codes[m["code"]]
    for name in sorted(buildings, key=len, reverse=True):     # 긴 이름부터(`공과대 2호관 별관` 이 `공과대 2호관` 보다 먼저)
        if room.startswith(name):
            return name
    return None


def slots_from_classes(
    classes: Iterable[Mapping], buildings: Mapping[str, object], codes: Mapping[str, str] | None = None,
    config: TimingConfig = DEFAULT_CONFIG,
) -> list[dict]:
    """오늘 수업 → 식사 공강 목록(화면용). 휴강은 뺀다."""
    codes = {**auto_codes(buildings), **(codes or {})}
    live = [c for c in classes if c.get("state", "scheduled") != "canceled" and c.get("start") and c.get("end")]
    blocks = [{"start": c["start"], "end": c["end"], "building": room_building(c.get("room"), buildings, codes)} for c in live]
    return [_slot_json(s) for s in find_meal_slots(blocks, config)]


def _slot_json(s: MealSlot) -> dict:
    return {"start": to_hhmm(s.start), "end": to_hhmm(s.end), "minutes": s.minutes, "label": s.label,
            "from": s.from_building, "to": s.to_building, "lastOfDay": s.last_of_day}


def parse_classes_text(text: str) -> list[dict]:
    """미리보기에서 손으로 넣는 시간표 — 한 줄에 `09:00-10:15 공7-223 운영체제`. `휴강` 이 들어 있으면 휴강."""
    out = []
    for line in text.splitlines():
        m = re.match(r"\s*(\d{1,2}:\d{2})\s*[-~]\s*(\d{1,2}:\d{2})\s*(\S+)?\s*(.*)$", line)
        if not m:
            continue
        a, b, room, rest = m.groups()
        out.append({"start": a.zfill(5), "end": b.zfill(5), "room": room or "", "name": rest.replace("휴강", "").strip(),
                    "state": "canceled" if "휴강" in line else "scheduled"})
    return out


def _f3_data_root() -> Path | None:
    """F3 출결 DB 가 있는 곳 — ① F3_DATA_DIR ② 저장소 F3 폴더 ③ 데스크톱 앱 개발용 desktop/.dev-data ④ 설치한 앱(%LOCALAPPDATA%\\kr.univus.desktop\\data).
    ③④ 는 앱 데이터 폴더(root)를 돌려주고, ① ② 는 None 으로 표시해 환경변수를 건드리지 않는다."""
    if os.environ.get("F3_DATA_DIR"):
        return None
    if (F3_AGENT_DIR / "data" / "attendance.db").exists():
        return None
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    for root in (AGENT_DIR.parent.parent / "desktop" / ".dev-data", base / "kr.univus.desktop" / "data"):
        if (root / "F3_Attendance_agent" / "data" / "attendance.db").exists():
            return root
    return None


def _f3_api():
    """F3 attendance.api — 출결 DB 가 없으면 None(빈 DB 를 새로 만들지 않는다)."""
    if not (F3_AGENT_DIR / "attendance" / "api.py").exists():
        return None
    root = _f3_data_root()
    if root is not None:
        osenv = AGENT_DIR.parent.parent / "C0_Platform_agent"
        if str(osenv) not in sys.path:
            sys.path.insert(0, str(osenv))
        try:
            from osenv import appdata   # type: ignore[import-not-found]   # 앱과 같은 규칙으로 F1·F3·F6 데이터 자리를 맞춘다
            appdata.apply(root)
        except Exception:                # noqa: BLE001
            os.environ.setdefault("F3_DATA_DIR", str(root / "F3_Attendance_agent" / "data"))
    db = Path(os.environ.get("F3_DATA_DIR") or F3_AGENT_DIR / "data") / "attendance.db"
    if not db.exists():
        return None
    if str(F3_AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(F3_AGENT_DIR))
    try:   # attendance.api 는 fastapi 가 필요해서, 표준 라이브러리만 쓰는 store·service 를 바로 부른다
        from attendance import service, store   # type: ignore[import-not-found]
        return service, store
    except Exception:                       # noqa: BLE001 — F3 가 못 떠도 미리보기는 손 입력으로 돈다
        return None


def classes_from_events(events: Iterable[Mapping]) -> list[dict]:
    """F3 calendar_events(kind=class) → 수업 줄. 시작 순."""
    out = []
    for e in events:
        x = e.get("extendedProps") or {}
        if x.get("kind") != "class":
            continue
        out.append({"name": e.get("title", ""), "start": e["start"][11:16], "end": e["end"][11:16],
                    "room": x.get("room") or "", "state": x.get("state") or "scheduled"})
    return sorted(out, key=lambda c: c["start"])


def f3_classes(day: date) -> list[dict] | None:
    """그날 수업(univ_us F3 출결 시간표). 출결 데이터가 없거나 못 읽으면 None."""
    f3 = _f3_api()
    if f3 is None:
        return None
    service, store = f3
    try:
        with store.connect() as con:
            return classes_from_events(service.calendar_events(con, day.isoformat(), day.isoformat()))
    except Exception:                       # noqa: BLE001
        return None


def f3_has_times() -> bool | None:
    """UnivUs 출결에 수업 시간(요일·교시)이 하나라도 들어 있나 — 과목만 있고 시간이 없으면 False. 출결 데이터가 없으면 None."""
    f3 = _f3_api()
    if f3 is None:
        return None
    try:
        with f3[1].connect() as con:
            return con.execute("SELECT 1 FROM meetings LIMIT 1").fetchone() is not None
    except Exception:                       # noqa: BLE001
        return None
