"""Apply the reviewed public-screen evidence, preserving original attrs/research.

Run after the original catalog import: python scripts/apply_followup_survey.py
"""
from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from meal.availability import candidate_active
from meal.import_places import _atomic_write, _encoded, _check_place
from meal.places import _validator, recommendation_ready
from datetime import date

FOLDER = ROOT / "data/survey/followup_2026-11-12"
DAYS = list("월화수목금토일")


def hours(days, start, end, last=None, pause=None):
    row = {"days": list(days), "open": start, "close": end}
    if last: row["lastOrder"] = last
    if pause: row["break"] = {"start": pause[0], "end": pause[1]}
    return row


NEW_HOURS = {
    "2097797421": [hours("월화수목금", "11:00", "21:00", "20:30", ("15:00", "17:00")), hours("토", "11:00", "15:00", "14:30")],
    "37127492": [hours("월화수목금토", "11:30", "21:40", "21:00", ("15:00", "17:00"))],
    "38735624": [hours(DAYS, "10:00", "21:30", "21:00")],
    "1941363668": [hours(DAYS, "11:00", "20:30", "20:00", ("15:00", "16:30"))],
    "1865452317": [hours(DAYS, "11:30", "21:00", "20:30")],
    # 자정 이후 구간은 전날 개점일의 다음 요일에 속한다.
    "1992635726": [hours("일월화수목금", "17:00", "24:00"), hours("월화수목금토", "00:00", "03:00")],
    "1219930187": [hours("수목금토일월", "11:00", "24:00"), hours("목금토일월화", "00:00", "11:00", "10:30")],
}


def main():
    from meal.survey import missing_info
    continued_path = ROOT / "data/survey/continued_2026-11-12/updates.json"
    continued = ({r["id"]: r for r in json.loads(continued_path.read_text(encoding="utf-8"))["places"]}
                 if continued_path.exists() else {})
    raw = json.loads((FOLDER / "browser_observations.json").read_text(encoding="utf-8"))
    baseline = json.loads((FOLDER / "baseline_places.json").read_text(encoding="utf-8"))["places"]
    targets = {p["availability"]["naverPlaceId"]: p for p in baseline if candidate_active(p) and not recommendation_ready(p)}
    assert len(targets) == 55
    homes = {r["nid"]: r for r in raw["homes"]}
    assert set(homes) == set(targets)
    exact = {r["nid"]: r for r in raw["exactMenus"]}
    photos = {r["nid"]: r for r in raw["photoFacts"]}
    planned, report = [], []
    validator = _validator()
    for nid, original in targets.items():
        path = ROOT / "data/places" / (original["id"] + ".json")
        current = json.loads(path.read_text(encoding="utf-8"))
        p = copy.deepcopy(current)
        h = p["hoursObservation"]
        home = homes[nid]
        if nid in NEW_HOURS:
            h.update(hours=NEW_HOURS[nid], status="confirmed", checkedAt="2026-11-12", sourceUrl=home["url"])
        elif h.get("hours") and h.get("hoursContext", "in_store") == "in_store":
            # 기존 공개 홈의 확인된 주간 시간은 가격 채널과 독립적이다.
            h["status"] = "confirmed"
            if home["url"].endswith("/home") and "접기" in home["hours"]:
                h["checkedAt"] = "2026-11-12"
        if nid == "1865452317":
            h["monthlyClosed"] = [{"day": "월", "weeks": [2, 4]}]
        m = p["menuObservation"]
        if nid in {"2015557853", "1378290309", "38710685", "2022595457"}:
            observed = exact[nid]
            assert observed["menus"] and observed["priceContext"] == "in_store"
            menus = []
            for item in observed["menus"]:
                price_text = item["priceText"]
                price = int(price_text[:-1].replace(",", "")) if re.fullmatch(r"[0-9][0-9,]*원", price_text) else None
                name = item["name"]
                main = bool(re.search(r"국밥|밀면|짜장|짬뽕|볶음밥|잡채밥|김밥", name)) and not re.search(r"[2-9]~?[0-9]?인|탕수육|쟁반|짬뽕탕", name)
                if nid == "2022595457": main = "어린이" not in name
                menus.append({"name": name, "price": price, "main": main, "priceText": price_text, **({"orderWindow": item["orderWindow"]} if "orderWindow" in item else {})})
            m.update(menus=menus, priceContext="in_store", status="confirmed", checkedAt="2026-11-12", sourceUrl=observed["url"], note="공개 매장 메뉴 게시값 재확인. 곱빼기·세트 설명의 숫자를 기본 가격에 합치지 않음.")
        if nid in {"2079438672", "38710685", "13570391", "35523968"}:
            photo = photos[nid]
            base_menus = m["menus"] if nid == "38710685" else []
            merged = {x["name"]: x for x in base_menus}
            for item in photo["menus"]: merged[item["name"]] = item
            menus = list(merged.values())
            # 모든 사진의 원문·과거 배달 메뉴는 별도 파일 및 baseline에 보존한다.
            m.update(menus=menus, priceContext="in_store", status="confirmed" if any(type(x.get("price")) is int and x["price"] > 0 for x in menus) else "unconfirmed", checkedAt="2026-11-12", sourceUrl=f"https://pcmap.place.naver.com/restaurant/{nid}/menu/list", note="공개 매장 메뉴판 이미지 대조. 사진 촬영일·현장 현재 가격을 보증하지 않음. " + photo.get("note", ""))
        # 기존 속성값은 보존하고 실제로 새로 확인한 누락 필드만 채운다.
        if m["status"] == "confirmed" and m["priceContext"] == "in_store" and "menus" not in p["attrs"]:
            # 시간 한정 가격을 조건 없는 legacy attrs 가격으로 복사하지 않는다.
            legacy = [{k: x[k] for k in ("name", "price", "main")} for x in m["menus"]
                      if type(x.get("price")) is int and x["price"] > 0 and not x.get("orderWindow")]
            if any(x["main"] for x in legacy): p["attrs"]["menus"] = legacy
        if h["status"] == "confirmed" and "hours" not in p["attrs"]:
            p["attrs"]["hours"] = copy.deepcopy(h["hours"])
        reasons = []
        if m["priceContext"] != "in_store": reasons.append("매장 가격 미확인: 배달·가격 채널 불명 자료는 예산 계산에 사용하지 않습니다.")
        elif not any(x.get("main") and type(x.get("price")) is int and x["price"] > 0 for x in m.get("menus", [])):
            reasons.append("가격이 확인된 1인 주 식사 메뉴 미확인: 여러 명용 메뉴·사이드·가격 변동을 임의 환산하지 않았습니다.")
        if h["status"] != "confirmed": reasons.append("요일별 영업시간 미확인: 미게시·일부 시간·날짜 휴무만으로 주간 시간을 만들지 않았습니다.")
        if nid in {"1805335047", "13376550", "36894596", "37915784", "1037261717", "37398013", "1249369391", "85019125", "1697423048"}:
            reasons.append("오래된 메뉴판 또는 본사 권장가격만으로 현재 지점의 매장 가격을 확정하지 않았습니다.")
        if recommendation_ready(p): reasons = ["확인된 매장 식사 가격과 요일별 영업시간을 연결하여 추천 대상으로 전환했습니다."]
        sources = [home["url"], m["sourceUrl"], h["sourceUrl"]]
        if nid in photos: sources.append(photos[nid]["imageUrl"])
        if p["id"] in continued:
            extra = continued[p["id"]]
            reasons = [item["label"] + ": " + item["detail"] for item in missing_info(p)] + extra.get("unconfirmedNotes", [])
            if recommendation_ready(p):
                reasons = ["공개 매장 식사 가격과 요일별 영업시간을 연결하여 추천 대상으로 전환했습니다."]
            sources = extra["sources"] + current.get("followupReview", {}).get("sources", [])
        p["followupReview"] = {"checkedAt": "2026-11-12", "reasons": reasons, "sources": list(dict.fromkeys(sources))}
        for field in ("id", "source", "sourceId", "research", "availability"):
            assert p.get(field) == original.get(field), (nid, field)
        for key, value in original["attrs"].items(): assert p["attrs"][key] == value, (nid, key)
        _check_place(p, path, validator, date(2026, 11, 12))
        planned.append((path, p))
        report.append({"id": p["id"], "naverPlaceId": nid, "name": p["name"], "ready": recommendation_ready(p), "reasons": reasons, "sources": list(dict.fromkeys(sources))})
    for path, p in planned:
        if json.loads(path.read_text(encoding="utf-8")) != p: _atomic_write(path, _encoded(p))
    result = {"version": 1, "checkedAt": "2026-11-12", "reviewed": 55, "newReady": sum(r["ready"] for r in report), "remaining": sum(not r["ready"] for r in report), "places": report}
    (FOLDER / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "places"}, ensure_ascii=False))


if __name__ == "__main__": main()
