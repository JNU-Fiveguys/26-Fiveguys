"""김종연 CA1(integration/pr4-local)의 추천 준비 식당을 내 조사 양식(data/survey.csv)에 합친다.

    python -m meal.import_pr4 data/pr4_places.json            # 미리 보기(무엇이 바뀌는지만)
    python -m meal.import_pr4 data/pr4_places.json --apply    # 실제로 쓴다(전 파일은 survey.before_pr4.csv)

내보내기는 scripts/export_pr4_places.py (그쪽 CA1 폴더에서 그쪽 파이썬으로).

규칙 — 내가 조사한 값이 먼저다.
  - 같은 가게(이름이 겹치고 80m 안)가 내 양식에 있고
      · 내가 이미 채웠거나(가격대·영업시간) 폐업·못 찾음·제외로 정했으면 → 그대로 둔다
      · 아직 비어 있으면 → 그쪽 값으로 채운다
  - 내 양식에 없는 가게 → 새 줄(id `pr4-p_XXXX`)로 넣고 좌표는 data/places.extra.json 에 둔다
  - 채우는 칸: 종류 · 식사 유형 · 가격대(대표 가격 → A~D) · 영업시간 · 브레이크 · 라스트오더 · 휴무 · 대표 메뉴와 가격 · 영업 상태 ·
    확인일 · 확인자(`김종연(pr4)`) · 메모(출처 URL, 날짜·월별 휴무). 혼밥·좌석은 비워 둔다(그쪽 데이터에 없음)
  - 날짜·월별 휴무(예: 11/19 임시휴무, 둘째·넷째 월요일)는 내 양식에 칸이 없어 메모에만 남긴다
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shutil
from difflib import SequenceMatcher
from pathlib import Path

from .places import AGENT_DIR, SURVEY_FILL, SURVEY_FIXED, read_text
from .survey import CLOSED_STATES, EXTRA_PLACES, load_coords

DAYS = "월화수목금토일"
CHECKER = "김종연(pr4)"
MATCH_M = 80
NEAR_M = 30
QUICK = {"분식", "패스트푸드"}
GROUP = {"고기"}


def _norm(name: str) -> str:
    """비교용 이름 — 공백·괄호·'전남대/전대/후문/용봉 …점' 같은 지점 꼬리를 뗀다."""
    s = re.sub(r"\(.*?\)|\s+", "", name or "")
    s = re.sub(r"(광주)?(전남대|전대|전남대학교|용봉|북구청|상대|후문|정문|예대)?(후문|정문)?\d*호?점$", "", s)
    return s.lower()


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    k = math.pi / 180
    x = (b[1] - a[1]) * k * math.cos((a[0] + b[0]) / 2 * k)
    return math.hypot(x, (b[0] - a[0]) * k) * 6371000


def same_place(row_name: str, row_xy, p: dict) -> bool:
    a, b = _norm(row_name), _norm(p["name"])
    if not a or not b or row_xy is None:
        return False
    d = _dist(row_xy, (p["lat"], p["lng"]))
    named = a == b or (min(len(a), len(b)) >= 2 and (a.startswith(b) or b.startswith(a)))
    if named and d <= MATCH_M:
        return True
    # 거의 같은 자리(30m)면 이름이 조금 달라도 같은 가게로 본다 — 나름/엄마손맛나름, 백련옥/백년옥, ○○써브웨이/써브웨이○○
    common = SequenceMatcher(None, a, b).find_longest_match(0, len(a), 0, len(b)).size
    return d <= NEAR_M and (common >= 2 and common >= min(len(a), len(b)) * 0.6 or SequenceMatcher(None, a, b).ratio() >= 0.6)


def price_band(price: int | None) -> str:
    if price is None:
        return ""
    return "A" if price <= 7000 else "B" if price <= 10000 else "C" if price <= 15000 else "D"


def _days(days: list[str]) -> str:
    """요일 목록 → 짧은 글자. 이어지는 3일 이상은 `월-금`, 나머지는 쉼표(`월,수,금`)."""
    idx = sorted({DAYS.index(d) for d in days})
    runs: list[list[int]] = []
    for i in idx:
        if runs and i == runs[-1][1] + 1:
            runs[-1][1] = i
        else:
            runs.append([i, i])
    if len(idx) == 7:
        return "매일"
    return ",".join(f"{DAYS[a]}-{DAYS[b]}" if b - a >= 2 else ",".join(DAYS[a:b + 1]) for a, b in runs)


def hours_text(hours: list[dict]) -> tuple[str, str, str, str]:
    """→ (영업시간, 브레이크, 라스트오더, 휴무)."""
    open_, brk, last = [], [], []
    for h in hours:
        d = _days(h["days"])
        open_.append(f"{d} {h['open']}-{h['close']}")
        if h.get("break"):
            brk.append(f"{d} {h['break']['start']}-{h['break']['end']}")
        if h.get("lastOrder"):
            last.append(f"{d} {h['lastOrder']}")
    covered = {d for h in hours for d in h["days"]}
    off = ",".join(d for d in DAYS if d not in covered) or "없음"
    return " / ".join(open_), " / ".join(brk), " / ".join(last), off


def menu_text(menus: list[dict]) -> str:
    main = [m for m in menus if m.get("main")] or menus
    out = []
    for m in main[:4]:
        w = m.get("orderWindow")
        out.append(f"{m['name']} {m['price']}" + (f" ({w['start']}-{w['end']} 주문)" if w else ""))
    return " / ".join(out)


def meal_type(category: str) -> str:
    return "분식·덮밥·패스트푸드" if category in QUICK else "고기·단체" if category in GROUP else "일반 식당"


def fill(p: dict) -> dict:
    hours, brk, last, off = hours_text(p["hours"])
    notes = ["김종연 CA1(pr4)에서 가져옴 — 현장 확인 필요"]
    if p.get("menuSource", {}).get("url"):
        notes.append(f"메뉴 출처 {p['menuSource']['url']}")
    if p.get("hoursSource", {}).get("url"):
        notes.append(f"영업시간 출처 {p['hoursSource']['url']}")
    if p.get("closedDates"):
        notes.append("날짜 휴무 " + ", ".join(p["closedDates"]))
    for rule in p.get("monthlyClosed", []):
        notes.append(f"매달 {'·'.join(map(str, rule['weeks']))}번째 {rule['day']}요일 휴무")
    checked = (p.get("hoursSource") or {}).get("checkedAt") or (p.get("menuSource") or {}).get("checkedAt") or ""
    return {"종류(확인)": p["category"], "식사 유형(확인)": meal_type(p["category"]), "가격대": price_band(p.get("repPrice")),
            "영업시간": hours, "브레이크": brk, "라스트오더": last, "휴무": off, "대표 메뉴와 가격": menu_text(p["menus"]),
            "영업 상태": "영업", "확인일": checked, "확인자": CHECKER, "메모": " · ".join(notes)}


def plan(rows: list[dict], coords: dict, places: list[dict], refresh: bool = False) -> dict:
    """무엇을 할지 — keep(내 값 유지) · fill(빈 줄 채움) · add(새 줄)."""
    out = {"keep": [], "fill": [], "add": []}
    used: set[str] = set()
    for p in places:
        hit = next((r for r in rows if r["id"] not in used and same_place(r["식당 이름"], coords.get(r["id"]), p)), None)
        if hit is None:
            out["add"].append(p)
            continue
        used.add(hit["id"])
        mine_done = bool((hit.get("가격대") or "").strip() or (hit.get("영업시간") or "").strip())
        if refresh and hit.get("확인자") == CHECKER:      # 전에 이 스크립트가 채운 줄은 다시 채운다
            mine_done = False
        mine_closed = any(s in (hit.get("영업 상태") or "") for s in CLOSED_STATES)
        out["keep" if mine_done or mine_closed else "fill"].append((hit, p))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m meal.import_pr4", description="김종연 CA1 식당을 내 조사 양식에 합친다")
    ap.add_argument("export", type=Path, help="scripts/export_pr4_places.py 로 만든 JSON")
    ap.add_argument("--survey", type=Path, default=AGENT_DIR / "data" / "survey.csv")
    ap.add_argument("--candidates", type=Path, default=AGENT_DIR / "data" / "places.candidates.json")
    ap.add_argument("--apply", action="store_true", help="실제로 쓴다(없으면 미리 보기)")
    ap.add_argument("--refresh", action="store_true", help="전에 가져온 줄(확인자 김종연(pr4))도 다시 채운다")
    args = ap.parse_args(argv)

    places = json.loads(args.export.read_text(encoding="utf-8"))["places"]
    rows = list(csv.DictReader(read_text(args.survey).splitlines()))
    coords = load_coords(args.candidates)
    todo = plan(rows, coords, places, refresh=args.refresh)
    print(f"그쪽 추천 준비 {len(places)}곳 → 내 값 유지 {len(todo['keep'])} · 빈 줄 채움 {len(todo['fill'])} · 새로 추가 {len(todo['add'])}")
    for hit, p in todo["keep"]:
        print(f"  유지  {hit['식당 이름']}  ←(그쪽 {p['name']})")
    for hit, p in todo["fill"]:
        print(f"  채움  {hit['식당 이름']}  ← {p['name']}")
    for p in todo["add"]:
        print(f"  추가  {p['name']}")
    if not args.apply:
        print("미리 보기만 했다 — 쓰려면 --apply")
        return 0

    fields = SURVEY_FIXED + SURVEY_FILL
    for hit, p in todo["fill"]:
        hit.update(fill(p))
    for p in todo["add"]:
        rows.append({**{k: "" for k in fields}, "id": f"pr4-{p['id']}", "식당 이름": p["name"], "주소": p.get("address") or "",
                     "종류(추정)": p["category"], "식사 유형(추정)": meal_type(p["category"]), **fill(p)})
    extra_path = args.candidates.with_name(EXTRA_PLACES)
    extra = json.loads(extra_path.read_text(encoding="utf-8")) if extra_path.exists() else {"places": []}
    known = {e["id"] for e in extra["places"]}
    extra["source"] = "김종연 CA1(integration/pr4-local) data/places — 상가정보에 없는 식당 좌표"
    extra["places"] += [{"id": f"pr4-{p['id']}", "name": p["name"], "lat": p["lat"], "lng": p["lng"]}
                        for p in todo["add"] if f"pr4-{p['id']}" not in known]
    shutil.copyfile(args.survey, args.survey.with_name("survey.before_pr4.csv"))
    tmp = args.survey.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, args.survey)
    extra_path.write_text(json.dumps(extra, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"썼다: {args.survey} (전 파일 survey.before_pr4.csv) · 좌표 {extra_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
