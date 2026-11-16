"""출처가 있는 웹 조사 결과만 부분 attrs에 반영한다. 기존 조사값은 덮어쓰지 않는다."""
from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

from .import_places import _assert_unchanged, _atomic_write, _encoded, _lock
from .places import PLACES_DIR, _check_rules, _validator, recommendation_ready


def merge_record(place: dict, record: dict, checked_at: str) -> dict:
    if record["placeId"] != place["id"] or record["sourceId"] != place.get("sourceId"):
        raise ValueError("조사 결과의 placeId/sourceId가 실제 가게와 다릅니다")
    result = copy.deepcopy(place)
    attrs = result.setdefault("attrs", {"status": "partial"})
    # 사용자 결정으로 좌석·혼밥 항목은 더 이상 수집하거나 계산하지 않는다.
    attrs.pop("soloOk", None)
    attrs.pop("seats", None)
    # 수동 조사 완료 상태를 웹 확인으로 승격하거나 덮어쓰지 않는다.
    for field in ("menus", "hours"):
        fact = record["facts"][field]
        if field not in attrs and fact["status"] == "confirmed" and fact.get("value"):
            attrs[field] = copy.deepcopy(fact["value"])
    research = {key: copy.deepcopy(record[key]) for key in ("identityStatus", "queries", "sources", "facts", "notes")}
    research.update(checkedAt=checked_at, method="web")
    research["unknownFields"] = [key for key in (
        "menus", "hours", "mealType", "takeout", "verifiedAt", "verifiedBy"
    ) if key not in attrs]
    # 게시 페이지가 휴게/마지막 주문을 쓰지 않았다는 사실을 '없음'으로 바꾸지 않는다.
    for field in ("break", "lastOrder"):
        if not attrs.get("hours") or not all(field in h for h in attrs["hours"]):
            research["unknownFields"].append(field)
    for field in ("menus", "hours"):
        if field in place.get("attrs", {}) and record["facts"][field].get("value") != attrs[field]:
            research["notes"].append(f"기존 attrs.{field}는 보존했습니다. 웹 관찰값과 다를 수 있어 검토가 필요합니다.")
    result["research"] = research
    return result


def research_summary(places: list[dict]) -> dict:
    researched = [p for p in places if p.get("research")]
    def status(p, key):
        return p["research"]["facts"][key]["status"]
    return {
        "total": len(places), "researched": len(researched),
        "checkedDates": sorted({p["research"]["checkedAt"] for p in researched}),
        "menusConfirmed": sum(status(p, "menus") == "confirmed" for p in researched),
        "hoursConfirmed": sum(status(p, "hours") == "confirmed" for p in researched),
        "bothConfirmed": sum(all(status(p, k) == "confirmed" for k in ("menus", "hours")) for p in researched),
        "withConflicts": sum(any(status(p, k) == "conflict" for k in ("menus", "hours")) for p in researched),
        "withoutConfirmedFields": sum(all(status(p, k) != "confirmed" for k in ("menus", "hours")) for p in researched),
        "ready": sum(recommendation_ready(p) for p in places),
        "places": [{"id": p["id"], "name": p["name"], "menus": status(p, "menus"),
                    "hours": status(p, "hours"), "unknownFields": p["research"]["unknownFields"],
                    "identityStatus": p["research"]["identityStatus"]} for p in researched],
    }


def apply_research(paths: list[Path], *, places_dir: Path = PLACES_DIR, dry_run: bool = False) -> dict:
    """전체 활성 명단의 결과를 사전 검증하고 파일별 원자 저장한다."""
    records = {}
    for path in paths:
        shard = json.loads(path.read_text(encoding="utf-8-sig"))
        if shard["version"] != 1:
            raise ValueError("지원하지 않는 조사 형식")
        for record in shard["places"]:
            pid = record["placeId"]
            if pid in records:
                raise ValueError(f"조사 결과 ID 중복: {pid}")
            records[pid] = (record, shard["checkedAt"])
    snapshots = {p: p.read_bytes() for p in places_dir.glob("*.json")}
    places = {p: json.loads(raw) for p, raw in snapshots.items()}
    active_ids = {p["id"] for p in places.values() if p.get("sourceStatus", "active") == "active"}
    if set(records) != active_ids:
        raise ValueError(f"조사 목록과 활성 식당 목록이 다릅니다: 누락={sorted(active_ids-set(records))}, 초과={sorted(set(records)-active_ids)}")
    validator = _validator()
    output = {}
    for path, place in places.items():
        if place["id"] not in records:
            output[path] = place
            continue
        record, checked_at = records[place["id"]]
        merged = merge_record(place, record, checked_at)
        errors = list(validator.iter_errors(merged))
        if errors:
            raise ValueError(f"{path.name}: {errors[0].json_path}: {errors[0].message}")
        rule_errors = [i.message for i in _check_rules(merged, path.name, False, date.today()) if i.level == "error"]
        if rule_errors:
            raise ValueError(f"{path.name}: {'; '.join(rule_errors)}")
        output[path] = merged
    summary = research_summary(list(output.values()))
    writes = {p: _encoded(value) for p, value in output.items() if value != places[p]}
    summary.update(changedFiles=len(writes), dryRun=dry_run)
    if not dry_run:
        with _lock(places_dir.parent / f".{places_dir.name}.import.lock"):
            if set(places_dir.glob("*.json")) != set(snapshots):
                raise ValueError("조사 적용 중 식당 파일 목록이 변경되었습니다")
            for path, raw in snapshots.items():
                _assert_unchanged(path, raw)
            for path, raw in writes.items():
                _assert_unchanged(path, snapshots[path])
                _atomic_write(path, raw)
    return summary
