"""채운 현장 조사 양식(survey.csv) → 추천에 쓰는 `Place` 목록 (정의서 D3 · 7절 PlaceAttr · CA1-R11).

    python -m meal.survey data/survey.csv                       # 검사: 몇 곳이 추천 가능한지, 어느 줄이 왜 안 되는지
    python -m meal.survey data/survey.csv --try 11:30 90        # 캠퍼스 기준점에서 11:30 부터 90분 공강으로 추천해 보기

엑셀에서 'CSV(쉼표로 분리)'로 다시 저장하면 CP949 가 되는데, 그것도 읽는다.
좌표는 조사 양식에 없고 `places.candidates.json` 에서 id 로 찾는다.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping, Sequence

from .places import AGENT_DIR, DEFAULT_CAMPUS, MEAL_TYPE_KO, PRICE_BANDS_NOTE, Campus, read_text
from .ranking import MealRequest, Place, Taste, recommend
from .timing import MealType, to_min

DAYS = "월화수목금토일"
DAY_GROUPS = {"매일": range(7), "평일": range(5), "주중": range(5), "주말": range(5, 7)}
MEAL_TYPE_FROM_KO = {v: MealType(k) for k, v in MEAL_TYPE_KO.items()} | {
    "학식": MealType.CAFETERIA, "분식": MealType.QUICK, "덮밥": MealType.QUICK, "패스트푸드": MealType.QUICK,
    "일반": MealType.REGULAR, "고기": MealType.GROUP, "단체": MealType.GROUP, "카페": MealType.CAFE,
}
YES = {"o", "ㅇ", "○", "y", "yes", "가능", "예", "네", "true", "1"}
NO = {"x", "×", "n", "no", "불가", "불가능", "아니오", "false", "0"}
HOURS_UNKNOWN = "영업시간 확인 필요"     # 영업시간을 못 찾은 곳에 적는 말 — 추천은 하되 칩으로 알린다
HOURS_UNKNOWN_MARK = "확인 필요"
CLOSED_STATES = ("폐업", "못 찾음", "못찾음", "휴업", "이전", "제외")   # '제외' = 팀이 조사·추천에서 뺀 곳

_TIME = r"(\d{1,2}):?(\d{2})"
_RANGE = re.compile(rf"{_TIME}\s*[-~]\s*{_TIME}")


class SurveyError(ValueError):
    pass


# ---------------------------------------------------------------------------
# 칸 하나씩 읽기
# ---------------------------------------------------------------------------

def parse_days(text: str) -> set[int]:
    """`월-금` · `월~금` · `월,수,금` · `토` · `평일` · `주말` · `매일` → 요일 번호(0=월)."""
    text = text.strip().replace(" ", "")
    if not text:
        return set(range(7))
    days: set[int] = set()
    for part in re.split(r"[,·/]", text):
        if not part:
            continue
        if part in DAY_GROUPS:
            days |= set(DAY_GROUPS[part])
            continue
        m = re.fullmatch(rf"([{DAYS}])요?일?(?:[-~]([{DAYS}])요?일?)?", part)
        if not m:
            raise SurveyError(f"요일을 모르겠다: {part!r}")
        a = DAYS.index(m.group(1))
        b = DAYS.index(m.group(2)) if m.group(2) else a
        days |= {d % 7 for d in range(a, (b if b >= a else b + 7) + 1)}
    return days


def _span(m: re.Match) -> tuple[int, int]:
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    start = to_min(f"{h1:02d}:{m1:02d}")
    end = to_min(f"{h2 % 24:02d}:{m2:02d}") if h2 < 24 or m2 else 1440
    if end <= start:          # 새벽까지 영업 → 자정에서 끊는다(공강 계산은 그날 안)
        end = 1440
    return start, end


def parse_schedule(text: str) -> dict[int, list[tuple[int, int]]]:
    """`월-금 11:00-21:00 / 토 11:00-15:00` · `매일 10:30~22:00` · `11:00-21:00`(요일 없으면 매일).

    여러 묶음은 `/` · `;` · 줄바꿈으로 나눈다. 시각은 `11:00` · `1100` 둘 다 받는다.
    """
    out: dict[int, list[tuple[int, int]]] = {}
    for chunk in re.split(r"[/;\n]", text):
        chunk = chunk.strip()
        if not chunk:
            continue
        spans = list(_RANGE.finditer(chunk))
        if not spans:
            raise SurveyError(f"시각을 모르겠다: {chunk!r}")
        days = parse_days(chunk[: spans[0].start()])
        for d in days:
            out.setdefault(d, []).extend(_span(s) for s in spans)
    return out


_CLOCK = re.compile(r"(\d{1,2}):(\d{2})")


def parse_last_order(text: str) -> dict[int, list[int]]:
    """`14:30 20:00` · `월-금 20:30 / 토 14:00` → 요일 → 라스트오더(분) 목록. 요일 없으면 매일."""
    out: dict[int, list[int]] = {}
    for chunk in re.split(r"[/;\n]", text):
        chunk = chunk.strip()
        if not chunk:
            continue
        times = list(_CLOCK.finditer(chunk))
        if not times:
            raise SurveyError(f"라스트오더 시각을 모르겠다: {chunk!r}")
        days = parse_days(chunk[: times[0].start()])
        mins = [to_min(f"{int(t.group(1)) % 24:02d}:{t.group(2)}") for t in times]
        for d in days:
            out.setdefault(d, []).extend(mins)
    return out


def apply_breaks(hours: dict[int, list[tuple[int, int]]], breaks: dict[int, list[tuple[int, int]]]) -> dict[int, list[tuple[int, int]]]:
    """영업 구간에서 브레이크를 뺀다."""
    out: dict[int, list[tuple[int, int]]] = {}
    for d, spans in hours.items():
        pieces = sorted(spans)
        for bs, be in breaks.get(d, []):
            nxt = []
            for s, e in pieces:
                if be <= s or e <= bs:
                    nxt.append((s, e))
                    continue
                if s < bs:
                    nxt.append((s, bs))
                if be < e:
                    nxt.append((be, e))
            pieces = nxt
        if pieces:
            out[d] = pieces
    return out


def parse_yes_no(text: str) -> bool | None:
    t = text.strip().lower()
    if not t:
        return None
    if t in YES:
        return True
    if t in NO:
        return False
    raise SurveyError(f"O/X 로 적어 주세요: {text!r}")


def parse_meal_type(text: str) -> MealType | None:
    t = text.strip()
    if not t:
        return None
    if t in MEAL_TYPE_FROM_KO:
        return MEAL_TYPE_FROM_KO[t]
    try:
        return MealType(t.lower())
    except ValueError:
        raise SurveyError(f"식사 유형을 모르겠다: {text!r}") from None


# ---------------------------------------------------------------------------
# 줄 하나 → Place
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Problem:
    line: int        # 엑셀에서 보이는 줄 번호(머리줄 = 1)
    id: str
    name: str
    message: str


def row_to_place(row: Mapping[str, str], coords: Mapping[str, tuple[float, float]]) -> Place | None:
    """조사가 안 된 줄은 None(빈칸). 잘못 적힌 줄은 SurveyError."""
    g = lambda k: (row.get(k) or "").strip()

    if any(s in g("영업 상태") for s in CLOSED_STATES):
        return None
    if not (g("가격대") or g("영업시간")):
        return None          # 아직 조사 안 함

    pid = g("id")
    if pid not in coords:
        raise SurveyError("후보 목록(places.candidates.json)에 없는 id")
    band = g("가격대").upper()
    if band not in ("A", "B", "C", "D"):
        raise SurveyError(f"가격대는 A·B·C·D 중 하나 ({PRICE_BANDS_NOTE})")
    if not g("영업시간"):
        raise SurveyError(f"영업시간이 비었다 — 모르면 '{HOURS_UNKNOWN}' 이라고 적는다")

    hours_unknown = HOURS_UNKNOWN_MARK in g("영업시간")
    if hours_unknown:      # 하루 종일 연 것으로 두고 휴무·브레이크만 뺀다 — 카드에 '영업시간 확인 필요' 칩
        hours = {d: [(0, 1440)] for d in range(7)}
    else:
        hours = parse_schedule(g("영업시간"))
    if g("브레이크"):
        hours = apply_breaks(hours, parse_schedule(g("브레이크")))
    closed = g("휴무")
    if closed and closed not in ("없음", "-", "연중무휴"):
        for d in parse_days(closed):
            hours.pop(d, None)

    seats = g("좌석(S/M/L)").upper() or None
    if seats not in (None, "S", "M", "L"):
        raise SurveyError(f"좌석은 S·M·L 중 하나: {seats!r}")

    meal_type = parse_meal_type(g("식사 유형(확인)")) or parse_meal_type(g("식사 유형(추정)")) or MealType.REGULAR
    lat, lng = coords[pid]
    return Place(
        id=pid,
        name=g("식당 이름"),
        category=g("종류(확인)") or g("종류(추정)") or "미분류",
        meal_type=meal_type,
        lat=lat,
        lng=lng,
        price_band=band,
        solo_ok=parse_yes_no(g("혼밥(O/X)")),
        seats=seats,
        hours=hours,
        last_order=parse_last_order(g("라스트오더")) if g("라스트오더") else None,
        hours_unknown=hours_unknown,
    )


def load_survey(text: str, coords: Mapping[str, tuple[float, float]]) -> tuple[list[Place], list[Problem], int]:
    """(추천에 쓸 곳, 고칠 줄, 아직 조사 안 한 줄 수)."""
    places: list[Place] = []
    problems: list[Problem] = []
    blank = 0
    for i, row in enumerate(csv.DictReader(io.StringIO(text)), start=2):
        try:
            p = row_to_place(row, coords)
        except (SurveyError, ValueError) as e:    # ValueError: 25:00 같은 시각
            problems.append(Problem(i, (row.get("id") or "").strip(), (row.get("식당 이름") or "").strip(), str(e)))
            continue
        if p is None:
            blank += 1
        else:
            places.append(p)
    return places, problems, blank


def count_closed(text: str) -> int:
    """영업 상태가 폐업 · 못 찾음 등인 줄 수 — '아직 조사 안 함'과 따로 센다."""
    return sum(
        1 for r in csv.DictReader(io.StringIO(text))
        if any(s in (r.get("영업 상태") or "") for s in CLOSED_STATES)
    )


EXTRA_PLACES = "places.extra.json"   # 상가정보에 없는 식당 좌표(예: 김종연 CA1 에서 가져온 곳) — 후보 파일 옆에 둔다


def load_coords(candidates: Path) -> dict[str, tuple[float, float]]:
    """후보(places.candidates.json) 좌표 + 옆의 places.extra.json(있으면) 좌표."""
    coords: dict[str, tuple[float, float]] = {}
    for path in (Path(candidates), Path(candidates).with_name(EXTRA_PLACES)):
        if path.exists():
            bundle = json.loads(path.read_text(encoding="utf-8"))
            coords.update({p["id"]: (p["lat"], p["lng"]) for p in bundle["places"]})
    return coords


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m meal.survey", description="채운 조사 양식 검사 · 추천 시험")
    ap.add_argument("survey", type=Path, help="채운 survey.csv")
    ap.add_argument("--candidates", type=Path, default=AGENT_DIR / "data" / "places.candidates.json")
    ap.add_argument("--campus", type=Path, default=DEFAULT_CAMPUS, help="--try 의 출발점(첫 기준점)")
    ap.add_argument("--try", dest="try_", nargs=2, metavar=("HH:MM", "분"), help="이 시각부터 이 길이 공강으로 추천해 보기")
    ap.add_argument("--weekday", type=int, choices=range(7), help="0=월 … 6=일 (기본: 오늘)")
    ap.add_argument("--budget", type=int, help="1인 예산(원) — 넘는 곳은 뺀다")
    ap.add_argument("--party", type=int, default=1)
    args = ap.parse_args(argv)

    text = read_text(args.survey)
    places, problems, blank = load_survey(text, load_coords(args.candidates))
    closed = count_closed(text)
    print(f"추천 가능 {len(places)}곳 · 폐업·못 찾음·제외 {closed}곳 · 아직 조사 안 함 {blank - closed}곳 · 고칠 줄 {len(problems)}곳")
    for pr in problems:
        print(f"  {pr.line}번째 줄 {pr.name or pr.id}: {pr.message}")

    if args.try_:
        center = Campus.load(args.campus).center
        start, minutes = to_min(args.try_[0]), int(args.try_[1])
        weekday = args.weekday if args.weekday is not None else date.today().weekday()
        req = MealRequest(
            slot_start=start, slot_min=minutes, weekday=weekday,
            origin=center, dest=center,
            party_size=args.party, budget=args.budget,
        )
        r = recommend(places, req, Taste())
        print(f"\n{DAYS[weekday]}요일 {args.try_[0]}부터 {minutes}분 · 출발·도착 캠퍼스 중심(건물 좌표가 생기면 바꾼다)")
        for title, group in (("추천", r.top), ("더 보기", r.more[:5]), ("빠듯", r.tight), ("학식", r.cafeteria)):
            for c in group:
                print(f"  [{title}] {c.name} ({c.category}) · 왕복 포함 {c.needMin}분 · 여유 {c.slackMin}분 · {' · '.join(c.reasons)}")
        if not places:
            print("  아직 조사된 곳이 없다 — survey.csv 의 가격대·영업시간을 채우면 추천이 나온다")
        elif not (r.top or r.tight or r.cafeteria):
            print("  추천할 곳이 없다 — 뺀 이유: " + ", ".join(f"{k} {v}" for k, v in r.excluded.items()))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
