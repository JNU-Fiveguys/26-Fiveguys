"""김종연 CA1(integration/pr4-local)의 '추천 준비' 식당을 한 파일로 내보낸다 — 은지 CA1 조사 양식에 합치기 전 단계.

그쪽 CA1 폴더에서 그쪽 파이썬으로 실행한다(그쪽 `meal` 패키지의 판정을 그대로 쓰기 위해):

    cd <26-Fiveguys-pr4>/campus/CA1_Meal_agent
    .venv/Scripts/python <이 파일> <내보낼 JSON>

내보내는 것: 이름 · 종류 · 좌표 · 주소 · 실제 적용 메뉴(가격 확인된 것) · 적용 영업시간 · 날짜/월별 휴무 · 출처 URL · 확인일.
개인 정보(시간표·입력 초안)는 없다. 다음 단계: 은지 CA1 에서 `python -m meal.import_pr4 <내보낸 JSON>`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))     # 실행한 폴더(그쪽 CA1)의 meal 패키지를 쓴다
from meal.availability import candidate_active  # noqa: E402
from meal.places import (effective_closed_dates, effective_hours, effective_menus, effective_price_source,
                         load_places, recommendation_ready, rep_price)


def main(out: str) -> int:
    rows = []
    for p in load_places():
        if p["source"] == "sample" or not candidate_active(p) or not recommendation_ready(p):
            continue
        hours_obs = p.get("hoursObservation") or {}
        menu_obs = p.get("menuObservation") or {}
        rows.append({
            "id": p["id"], "name": p["name"], "category": p["category"], "lat": p["lat"], "lng": p["lng"],
            "address": p.get("address"),
            "menus": [{"name": m["name"], "price": m["price"], "main": bool(m.get("main")),
                       **({"orderWindow": m["orderWindow"]} if m.get("orderWindow") else {})} for m in effective_menus(p)],
            "repPrice": rep_price(p),
            "hours": effective_hours(p),
            "closedDates": effective_closed_dates(p),
            "monthlyClosed": hours_obs.get("monthlyClosed", []),
            "priceSource": effective_price_source(p),
            "hoursSource": {"url": hours_obs.get("sourceUrl"), "checkedAt": hours_obs.get("checkedAt")},
            "menuSource": {"url": menu_obs.get("sourceUrl"), "checkedAt": menu_obs.get("checkedAt")},
        })
    Path(out).write_text(json.dumps({"source": "김종연 CA1 integration/pr4-local · data/places", "places": rows},
                                    ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"내보냄: 추천 준비 {len(rows)}곳 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
