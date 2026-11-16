"""41곳 추가 조사 결과를 기존 ID·attrs·research 보존 후 재적용한다."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from meal.import_places import _assert_unchanged, _atomic_write, _check_place, _encoded, _lock
from meal.places import _validator, load_places, recommendation_ready
from meal.survey import missing_info
from meal.timetable import SEOUL

FOLDER = ROOT / "data/survey/continued_2026-11-12"


def apply(*, dry_run=False):
    manifest = json.loads((FOLDER / "updates.json").read_text(encoding="utf-8"))
    baseline = {p["id"]: p for p in json.loads((FOLDER / "baseline_places.json").read_text(encoding="utf-8"))["places"]}
    rows = manifest["places"]
    assert manifest["version"] == 1 and manifest["reviewed"] == len(rows) == 41
    assert len({r["id"] for r in rows}) == 41
    today = datetime.now(SEOUL).date()
    checked_at = manifest["checkedAt"]
    assert date.fromisoformat(checked_at) <= today
    planned, report = [], []
    validator = _validator()
    for row in rows:
        path = ROOT / "data/places" / (row["id"] + ".json")
        raw = path.read_bytes()
        old = json.loads(raw)
        assert old["availability"]["naverPlaceId"] == row["naverPlaceId"]
        assert row["id"] in baseline and not recommendation_ready(baseline[row["id"]])
        p = copy.deepcopy(old)
        for field in ("menuObservation", "hoursObservation"):
            if field in row and p.get(field, {}).get("checkedAt", "") <= checked_at:
                p[field] = copy.deepcopy(row[field])
        m, h = p["menuObservation"], p["hoursObservation"]
        if "menus" not in p["attrs"] and m.get("status") == "confirmed" and m.get("priceContext") == "in_store":
            menus = [{k: item[k] for k in ("name", "price", "main")} for item in m["menus"]
                     if type(item.get("price")) is int and item["price"] > 0 and "main" in item]
            if any(item["main"] for item in menus):
                p["attrs"]["menus"] = menus
        if "hours" not in p["attrs"] and h.get("status") == "confirmed" and h.get("hoursContext", "in_store") == "in_store":
            p["attrs"]["hours"] = copy.deepcopy(h["hours"])
        missing = missing_info(p)
        reasons = [item["label"] + ": " + item["detail"] for item in missing]
        reasons.extend(row.get("unconfirmedNotes", []))
        if recommendation_ready(p):
            reasons = ["공개 매장 식사 가격과 요일별 영업시간을 연결하여 추천 대상으로 전환했습니다."]
        p["followupReview"] = {"checkedAt": checked_at, "reasons": reasons,
                               "sources": list(dict.fromkeys(row["sources"] + old.get("followupReview", {}).get("sources", [])))}
        for field in ("id", "source", "sourceId", "research", "availability"):
            assert p.get(field) == old.get(field), (row["id"], field)
        assert all(p["attrs"][key] == value for key, value in old["attrs"].items())
        _check_place(p, path, validator, today)
        planned.append((path, raw, p))
        report.append({"id": p["id"], "name": p["name"], "ready": recommendation_ready(p),
                       "missing": missing, "sources": p["followupReview"]["sources"], "reasons": reasons})
    if not dry_run:
        with _lock(FOLDER / ".apply.lock"):
            for path, raw, _ in planned:
                _assert_unchanged(path, raw)
            for path, raw, p in planned:
                if json.loads(raw) != p:
                    _atomic_write(path, _encoded(p))
    current = {p["id"]: p for p in load_places()}
    current.update({p["id"]: p for _, _, p in planned})
    confirmed_menus = [r for r in rows if r.get("menuObservation", {}).get("status") == "confirmed"]
    confirmed_hours = [r for r in rows if r.get("hoursObservation", {}).get("status") == "confirmed"]
    result = {"version": 1, "checkedAt": checked_at, "reviewed": 41,
              "confirmedPlaces": len({r["id"] for r in confirmed_menus + confirmed_hours}),
              "confirmedStoreMenus": len(confirmed_menus), "confirmedHours": len(confirmed_hours),
              "newReady": sum(r["ready"] for r in report), "remaining": sum(not r["ready"] for r in report),
              "totalReady": sum(recommendation_ready(p) for p in current.values()),
              "placesChanged": sum(json.loads(raw) != p for _, raw, p in planned), "dryRun": dry_run,
              "places": report}
    if not dry_run:
        _atomic_write(FOLDER / "report.json", _encoded(result))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    result = apply(dry_run=parser.parse_args().dry_run)
    print(json.dumps({k: v for k, v in result.items() if k != "places"}, ensure_ascii=False))
