"""확인한 네이버 식당 명단을 ID·과거 조사를 보존하며 반영한다.

python -m meal.naver_catalog manifest.json --dry-run
실제 웹 탐색은 하지 않으며 입력은 사람이 공개 화면에서 확인한 정규화 명단이다.
원본 manifest가 좌표 출처와 전체 관찰값을 보존한다. 파일별 원자 교체이므로
중단되면 같은 manifest로 다시 실행한다. 폴더 전체의 트랜잭션은 아니다.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import unicodedata
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from .availability import DISTRICTS, candidate_active
from .import_places import (
    DEFAULT_REGISTRY as LEGACY_REGISTRY, ImportPlacesError, _assert_unchanged,
    _atomic_write, _check_place, _encoded, _json, _lock, _read_optional, _registry,
)
from .places import CAMPUS_CENTER, DATA_DIR, PLACES_DIR, SCHEMA_PATH, _hours_errors, _validator, distance_m, recommendation_ready

DEFAULT_REGISTRY = DATA_DIR / "naver_place_ids.json"
EXCLUDED_REASON = "네이버 지도에서 확인한 네 식당가 명단에 포함되지 않아 제외"
EXCLUDED_URL = "https://map.naver.com/p/search/" + "%EC%A0%84%EB%82%A8%EB%8C%80%20%EC%9D%8C%EC%8B%9D%EC%A0%90"
MAX_CAMPUS_DISTANCE_M = 2000


def _manifest(raw: bytes, path: Path, today: date) -> dict:
    value = _json(raw, path)
    place_schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    fields = {
        "naverPlaceId": {"type": "string", "pattern": "^[1-9][0-9]*$"},
        "name": place_schema["properties"]["name"], "address": place_schema["properties"]["address"],
        "category": {"enum": [c for c in place_schema["properties"]["category"]["enum"] if c != "카페·베이커리"]},
        "district": {"enum": sorted(DISTRICTS)},
        "lat": place_schema["properties"]["lat"], "lng": place_schema["properties"]["lng"],
        "coordinateSource": {"type": "object", "minProperties": 1},
        "existingPlaceId": {"type": "string", "pattern": r"^p_[0-9]{3,4}$"},
        "priceContext": {"enum": ["in_store", "delivery", "unknown"]},
        "sourceUrl": {"$ref": "#/$defs/naverSourceUrl"},
        "menuSourceUrl": {"$ref": "#/$defs/naverSourceUrl"},
        "hoursSourceUrl": {"$ref": "#/$defs/naverSourceUrl"},
        "menuStatus": {"enum": ["confirmed", "unconfirmed", "not_found", "blocked", "empty"]},
        "menus": place_schema["$defs"]["menuObservation"]["properties"]["menus"],
        "hours": {"type": "array", "items": place_schema["$defs"]["attrs"]["properties"]["hours"]["items"]},
        "closedDates": {"type": "array", "uniqueItems": True, "items": {"type": "string", "format": "date"}},
        "checkedAt": {"type": "string", "format": "date"},
        "notes": {"type": "array", "items": {"type": "string", "maxLength": 2000}},
    }
    schema = {
        "type": "object", "additionalProperties": False,
        "required": ["version", "checkedAt", "places"],
        "properties": {
            "version": {"const": 1}, "checkedAt": {"type": "string", "format": "date"},
            "places": {"type": "array", "minItems": 1, "maxItems": 500, "items": {
                "type": "object", "additionalProperties": False,
                "required": [key for key in fields if key not in {"existingPlaceId", "hoursSourceUrl", "menuStatus", "closedDates", "checkedAt"}],
                "properties": fields,
            }},
        }, "$defs": place_schema["$defs"],
    }
    errors = sorted(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value), key=lambda e: str(e.path))
    if errors:
        raise ImportPlacesError(f"네이버 명단 {errors[0].json_path}: {errors[0].message}")
    if date.fromisoformat(value["checkedAt"]) > today:
        raise ImportPlacesError("네이버 명단 checkedAt이 미래입니다.")
    ids, old_ids = set(), set()
    for row in value["places"]:
        if date.fromisoformat(row.get("checkedAt", value["checkedAt"])) > today:
            raise ImportPlacesError("네이버 식당 행 checkedAt이 미래입니다.")
        nid, old_id = row["naverPlaceId"], row.get("existingPlaceId")
        if nid in ids or (old_id and old_id in old_ids):
            raise ImportPlacesError("네이버 명단의 naverPlaceId 또는 existingPlaceId가 중복되었습니다.")
        ids.add(nid)
        if old_id:
            old_ids.add(old_id)
        if not row["name"].strip() or not row["address"].strip():
            raise ImportPlacesError("네이버 명단의 식당 이름·주소는 비어 있으면 안 됩니다.")
        if distance_m(row["lat"], row["lng"], *CAMPUS_CENTER) > MAX_CAMPUS_DISTANCE_M:
            raise ImportPlacesError(f"{row['name']}: 전남대 주변 2km 밖 좌표입니다. 주소·좌표를 재확인하세요.")
        hour_errors = _hours_errors(row["hours"])
        if hour_errors:
            raise ImportPlacesError(f"{row['name']}: " + "; ".join(hour_errors))
    return value


def _naver_registry(raw: bytes | None, path: Path) -> dict:
    if raw is None:
        return {"version": 1, "nextId": 101, "naverPlaceIds": {}, "reservedIds": []}
    value = _json(raw, path)
    if not isinstance(value, dict) or set(value) != {"version", "nextId", "naverPlaceIds", "reservedIds"}:
        raise ImportPlacesError("네이버 ID registry 형식이 잘못되었습니다.")
    # 기존 예약 검증기를 재사용하되 외부 ID 이름만 구분한다.
    checked = _registry(_encoded({"version": value["version"], "nextId": value["nextId"],
                                  "sourceIds": value["naverPlaceIds"], "reservedIds": value["reservedIds"]}), path)
    if any(not re.fullmatch(r"[1-9][0-9]*", nid) for nid in checked["sourceIds"]):
        raise ImportPlacesError("네이버 registry의 naverPlaceId는 숫자 문자열이어야 합니다.")
    return value


def _address_key(address: str) -> str:
    """행정구역 표기와 층수 차이를 제외한 도로명·건물번호를 비교한다."""
    address = unicodedata.normalize("NFKC", address)
    match = re.search(r"(?:^|\s)([\w·.-]+(?:대로|로|길))\s*(\d+(?:-\d+)?)(?=\s|$)", address)
    if match:
        return "".join(match.groups())
    return re.sub(r"\s+", "", address)


def _observations(row: dict, checked_at: str) -> tuple[dict, dict]:
    availability = {
        "status": "listed", "provider": "naver", "checkedAt": checked_at,
        "sourceUrl": row["sourceUrl"], "naverPlaceId": row["naverPlaceId"],
        "observedName": row["name"], "observedAddress": row["address"], "district": row["district"],
        "addressVerified": True, "nearby": True,
        "reason": "공개 네이버 지도에서 동일 주소의 전남대 " + row["district"] + " 식당으로 확인",
    }
    observation = {
        "sourceUrl": row["menuSourceUrl"], "checkedAt": checked_at,
        "priceContext": row["priceContext"], "menus": copy.deepcopy(row["menus"]),
        "note": "메뉴 상태: " + row.get("menuStatus", "confirmed" if row["menus"] else "empty")
                + ". 원본 관찰·좌표 출처는 적용 manifest에 보관합니다.",
    }
    return availability, observation


def _merge(row: dict, old: dict | None, pid: str, checked_at: str) -> dict:
    value = copy.deepcopy(old) if old else {
        "version": 1, "id": pid, "source": "team", "sourceId": "naver:" + row["naverPlaceId"],
    }
    value.update({key: row[key] for key in ("name", "address", "category", "lat", "lng")})
    value["sourceStatus"] = "active"
    value["availability"], value["menuObservation"] = _observations(row, checked_at)
    attrs = value.setdefault("attrs", {"status": "partial"})
    in_store = row["priceContext"] == "in_store"
    menu_confirmed = in_store and row.get("menuStatus", "confirmed") == "confirmed"
    menus = [{key: copy.deepcopy(menu[key]) for key in ("name", "price", "main", "orderWindow") if key in menu} for menu in row["menus"]
             if type(menu.get("price")) is int and menu["price"] > 0 and type(menu.get("main")) is bool
             and menu.get("priceContext", row["priceContext"]) == "in_store"]
    menu_confirmed = menu_confirmed and bool(menus)
    hours_confirmed = bool(row["hours"])
    value["menuObservation"]["status"] = "confirmed" if menu_confirmed else "unconfirmed"
    # 게시 가격 확인과 예산에 쓸 주메뉴 분류를 구분한다. 공유 메뉴도 게시 총액으로 사용할 수 있다.
    menu_confirmed = menu_confirmed and any(menu["main"] for menu in menus)
    value["hoursObservation"] = {
        "sourceUrl": row.get("hoursSourceUrl", row["sourceUrl"]), "checkedAt": checked_at,
        "status": "confirmed" if hours_confirmed else "unconfirmed", "hours": copy.deepcopy(row["hours"]),
        "note": "매장 영업시간 관찰. 기존 attrs/research를 덮어쓰지 않습니다.",
    }
    if row.get("closedDates"):
        value["hoursObservation"]["closedDates"] = list(row["closedDates"])
    # 과거 전체 명단 재생성이 더 최근의 추가 확인을 되돌리지 않게 한다.
    if old:
        for field in ("menuObservation", "hoursObservation"):
            previous = old.get(field, {})
            if previous.get("checkedAt", "") > checked_at:
                value[field] = copy.deepcopy(previous)
    for key, confirmed, observed in (("menus", menu_confirmed, menus), ("hours", hours_confirmed, row["hours"])):
        if key not in attrs and confirmed:
            attrs[key] = copy.deepcopy(observed)
    if "research" not in value:
        sources = [{"id": "naver-place", "url": row["sourceUrl"], "title": row["name"] + " 네이버 지도",
                    "type": "directory", "observedAt": checked_at, "addressMatch": True,
                    "note": "같은 주소의 지점 확인. 지도 등록 확인은 실시간 영업 중 보증이 아님"},
                   {"id": "naver-menu", "url": row["menuSourceUrl"], "title": row["name"] + " 메뉴 탭",
                    "type": "directory", "observedAt": checked_at, "addressMatch": True,
                    "note": "가격 맥락: " + row["priceContext"]},
                   {"id": "naver-hours", "url": row.get("hoursSourceUrl", row["sourceUrl"]), "title": row["name"] + " 영업시간",
                    "type": "directory", "observedAt": checked_at, "addressMatch": True,
                    "note": "게시된 요일별 영업시간. 미확인은 추정하지 않음"}]
        facts = {}
        for key, confirmed, observed, sid in (("menus", menu_confirmed, menus, "naver-menu"),
                                               ("hours", hours_confirmed, row["hours"], "naver-hours")):
            conflict = confirmed and key in attrs and attrs[key] != observed
            fact = {"status": "conflict" if conflict else "confirmed" if confirmed else "unconfirmed",
                    "sourceIds": [sid] if confirmed else [],
                    "note": "기존 attrs 값은 보존했으며 최신 관찰값과 다릅니다." if conflict else
                            "매장 게시값을 확인했습니다." if confirmed else "매장 기준 정보 미확인. 배달·가격 맥락 불명 자료는 확정하지 않습니다."}
            if confirmed:
                fact["value"] = copy.deepcopy(observed)
            facts[key] = fact
        unknown = [key for key in ("menus", "hours", "mealType", "takeout", "verifiedAt", "verifiedBy") if key not in attrs]
        unknown += [key for key in ("break", "lastOrder") if not attrs.get("hours") or not all(key in h for h in attrs["hours"])]
        value["research"] = {"checkedAt": checked_at, "method": "web", "identityStatus": "matched",
                             "queries": [row["name"] + " " + row["address"]], "sources": sources,
                             "facts": facts, "unknownFields": unknown,
                             "notes": list(row["notes"]) + ["공개 네이버 화면 관찰이며 현장 검증이 아닙니다."]}
    return value


def apply_naver_catalog(manifest_path: str | Path, *, places_dir: str | Path = PLACES_DIR,
                        registry_path: str | Path = DEFAULT_REGISTRY,
                        legacy_registry_path: str | Path = LEGACY_REGISTRY,
                        dry_run: bool = False, today: date | None = None) -> dict:
    """전체 명단 검증 후 예약→places 순서로 저장한다. dry_run은 파일을 만들지 않는다."""
    source, places, registry, legacy_registry = map(lambda p: Path(p).resolve(),
                                                   (manifest_path, places_dir, registry_path, legacy_registry_path))
    if len({source, registry, legacy_registry}) != 3 or any(path.is_relative_to(places) for path in (source, registry, legacy_registry)):
        raise ImportPlacesError("manifest와 두 ID registry는 places 밖의 서로 다른 파일이어야 합니다.")
    if places.name == "samples":
        raise ImportPlacesError("실제 식당은 samples 폴더에 반영할 수 없습니다.")
    today = today or datetime.now(timezone(timedelta(hours=9))).date()
    source_raw = source.read_bytes()
    manifest = _manifest(source_raw, source, today)
    registry_raw, legacy_raw = _read_optional(registry), _read_optional(legacy_registry)
    saved, legacy = _naver_registry(registry_raw, registry), _registry(legacy_raw, legacy_registry)
    snapshots = {source: source_raw, registry: registry_raw, legacy_registry: legacy_raw}
    existing_paths = set(places.glob("*.json"))
    existing, paths = {}, {}
    validator = _validator()
    for path in sorted(existing_paths):
        raw = path.read_bytes()
        snapshots[path] = raw
        record = _json(raw, path)
        _check_place(record, path, validator, today)
        if record["id"] in existing:
            raise ImportPlacesError("기존 식당 ID가 중복되었습니다.")
        existing[record["id"]], paths[record["id"]] = record, path
    mapping = dict(saved["naverPlaceIds"])
    reserved = set(saved["reservedIds"]) | set(legacy["reservedIds"]) | set(existing)
    legacy_mapping = dict(legacy["sourceIds"])
    legacy_owners = {pid: sid for sid, pid in legacy_mapping.items()}
    for pid, record in existing.items():
        if record["source"] == "semas":
            sid = record["sourceId"]
            if (sid in legacy_mapping and legacy_mapping[sid] != pid) or (pid in legacy_owners and legacy_owners[pid] != sid):
                raise ImportPlacesError("기존 공공 ID registry와 places 식별자가 충돌합니다.")
            legacy_mapping[sid], legacy_owners[pid] = pid, sid
        elif pid in legacy_owners:
            raise ImportPlacesError("기존 공공 registry가 수동 식당 ID를 가리킵니다.")
    next_id = max(101, saved["nextId"], legacy["nextId"], max((int(pid[2:]) for pid in reserved), default=0) + 1)
    rows = sorted(manifest["places"], key=lambda row: int(row["naverPlaceId"]))
    selected, mapped_rows = set(), []
    owners = {pid: nid for nid, pid in mapping.items()}
    output = copy.deepcopy(existing)
    for row in rows:
        nid, requested = row["naverPlaceId"], row.get("existingPlaceId")
        if requested and requested not in existing:
            raise ImportPlacesError(f"existingPlaceId {requested} 파일이 없습니다.")
        if requested and nid in mapping and mapping[nid] != requested:
            raise ImportPlacesError("existingPlaceId와 네이버 ID registry가 충돌합니다.")
        pid = mapping.get(nid) or requested
        if pid and pid in owners and owners[pid] != nid:
            raise ImportPlacesError("여러 네이버 ID를 같은 식당 ID로 병합할 수 없습니다.")
        if pid is None:
            if next_id > 9999:
                raise ImportPlacesError("식당 ID가 9999개를 초과했습니다.")
            pid = f"p_{next_id:04d}"
            next_id += 1
        old = existing.get(pid)
        if old is None and pid in legacy_owners:
            raise ImportPlacesError(f"{pid}는 공공 원천 ID가 예약한 식당입니다. 누락된 원본 파일부터 복구하세요.")
        if old:
            old_nid = old.get("availability", {}).get("naverPlaceId")
            if old_nid is not None and old_nid != nid:
                raise ImportPlacesError(f"{pid}는 다른 네이버 지점으로 기록되어 있습니다.")
            if _address_key(old.get("address", "")) != _address_key(row["address"]):
                raise ImportPlacesError(f"{pid}의 기존 주소와 네이버 확인 주소가 다릅니다. 자동 병합하지 않습니다.")
        if pid in selected:
            raise ImportPlacesError("같은 식당 ID를 여러 명단 행에 사용할 수 없습니다.")
        mapping[nid], owners[pid] = pid, nid
        reserved.add(pid)
        selected.add(pid)
        output[pid] = _merge(row, old, pid, row.get("checkedAt", manifest["checkedAt"]))
        mapped_rows.append({"naverPlaceId": nid, "placeId": pid, "created": old is None})
    for pid, record in output.items():
        if pid in selected or record["source"] == "sample":
            continue
        old_evidence = record.get("availability", {})
        record["availability"] = {
            "status": "excluded", "provider": "naver", "checkedAt": manifest["checkedAt"],
            "sourceUrl": EXCLUDED_URL, "naverPlaceId": old_evidence.get("naverPlaceId"),
            "observedName": "", "observedAddress": "", "district": None,
            "addressVerified": False, "nearby": False, "reason": EXCLUDED_REASON,
        }
    writes = {}
    for pid, value in sorted(output.items()):
        path = paths.get(pid, places / f"{pid}.json")
        _check_place(value, path, validator, today)
        snapshots.setdefault(path, None)
        if value != existing.get(pid):
            writes[path] = _encoded(value)
    reserve_list = sorted(reserved, key=lambda pid: (int(pid[2:]), pid))
    next_id = max(next_id, max((int(pid[2:]) for pid in reserved), default=0) + 1)
    new_registry = {"version": 1, "nextId": next_id, "naverPlaceIds": dict(sorted(mapping.items())), "reservedIds": reserve_list}
    new_legacy = {"version": 1, "nextId": next_id, "sourceIds": dict(sorted(legacy_mapping.items())), "reservedIds": reserve_list}
    registry_bytes = registry_raw if new_registry == saved else _encoded(new_registry)
    legacy_bytes = legacy_raw if new_legacy == legacy else _encoded(new_legacy)
    report = {"selectedCount": len(selected), "totalPlaces": len(output),
              "created": sum(row["created"] for row in mapped_rows),
              "updated": sum(path in existing_paths for path in writes),
              "unchanged": len(output) - len(writes), "excluded": sum(not candidate_active(p) for p in output.values()),
              "ready": sum(recommendation_ready(p) for p in output.values()),
              "registryChanged": registry_bytes != registry_raw, "legacyRegistryChanged": legacy_bytes != legacy_raw,
              "dryRun": dry_run, "places": mapped_rows}
    if dry_run:
        return report
    for directory in {places.parent, registry.parent, legacy_registry.parent}:
        directory.mkdir(parents=True, exist_ok=True)
    lock_paths = {places.parent / f".{places.name}.import.lock", registry.parent / f".{registry.name}.import.lock",
                  legacy_registry.parent / f".{legacy_registry.name}.import.lock"}
    with ExitStack() as stack:
        for lock_path in sorted(lock_paths):
            stack.enter_context(_lock(lock_path))
        if set(places.glob("*.json")) != existing_paths:
            raise ImportPlacesError("작업 중 places 파일 목록이 바뀌었습니다.")
        for path, expected in snapshots.items():
            _assert_unchanged(path, expected)
        places.mkdir(exist_ok=True)
        # 공공 generator도 예약을 보도록 먼저 저장한다. 두 예약이 끝나기 전 가게 파일은 쓰지 않는다.
        if legacy_bytes != legacy_raw:
            _assert_unchanged(legacy_registry, legacy_raw)
            _atomic_write(legacy_registry, legacy_bytes)
        if registry_bytes != registry_raw:
            _assert_unchanged(registry, registry_raw)
            _atomic_write(registry, registry_bytes)
        for path, raw in writes.items():
            _assert_unchanged(path, snapshots[path])
            _atomic_write(path, raw)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--places-dir", type=Path, default=PLACES_DIR)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--legacy-registry", type=Path, default=LEGACY_REGISTRY)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    report = apply_naver_catalog(args.manifest, places_dir=args.places_dir, registry_path=args.registry,
                                 legacy_registry_path=args.legacy_registry, dry_run=args.dry_run)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
