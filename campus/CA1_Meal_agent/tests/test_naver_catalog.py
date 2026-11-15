"""큐레이션 명단 적용: 예약·재실행·원본 보존·현재 후보 제외의 경계 검증."""
import copy
import csv
import json
from contextlib import contextmanager
from datetime import date

import pytest

from meal import naver_catalog as curator
from meal.import_places import ImportPlacesError, import_places
from meal.places import SAMPLES_DIR, check_dirs, recommendation_ready

TODAY = date(2026, 11, 11)


def row(nid="12345", **changes):
    return {"naverPlaceId": nid, "name": "확인한 밥집 " + nid, "address": "광주 북구 우치로 94 1층",
            "category": "한식", "district": "후문", "lat": 35.177, "lng": 126.914,
            "coordinateSource": {"file": "data/raw/semas_radius.json", "matchedAddress": "광주 북구 우치로 94"},
            "menus": [{"name": "정식", "price": 8500, "main": True}],
            "hours": [{"days": list("월화수목금토일"), "open": "10:00", "close": "20:00"}],
            "priceContext": "in_store", "sourceUrl": f"https://pcmap.place.naver.com/restaurant/{nid}/home",
            "menuSourceUrl": f"https://pcmap.place.naver.com/restaurant/{nid}/menu/list",
            "notes": ["공개 메뉴·영업시간 확인"], **changes}


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def snapshot(folder):
    return {p.relative_to(folder): (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.rglob("*") if p.is_file()}


@pytest.fixture
def paths(tmp_path):
    return {"manifest_path": tmp_path / "manifest.json", "places_dir": tmp_path / "places",
            "registry_path": tmp_path / "naver_place_ids.json", "legacy_registry_path": tmp_path / "place_ids.json"}


def manifest(paths, rows):
    dump(paths["manifest_path"], {"version": 1, "checkedAt": "2026-11-11", "places": rows})


def run(paths, **kwargs):
    return curator.apply_naver_catalog(**paths, today=TODAY, **kwargs)


def existing(paths, pid="p_0001", **changes):
    value = read(SAMPLES_DIR / "s_01.json")
    value.update(id=pid, source="semas", sourceId="OLD-" + pid, sourceStatus="active",
                 address="전남광주통합특별시 북구 우치로 94", **changes)
    dump(paths["places_dir"] / (pid + ".json"), value)
    return value


def test_new_places_are_ready_and_receive_reserved_stable_ids_without_touching_manifest(paths):
    manifest(paths, [row("900"), row("100")])
    original = paths["manifest_path"].read_bytes()
    report = run(paths)
    assert report["created"] == report["ready"] == report["selectedCount"] == 2
    assert read(paths["registry_path"])["naverPlaceIds"] == {"100": "p_0101", "900": "p_0102"}
    legacy = read(paths["legacy_registry_path"])
    assert legacy["reservedIds"] == ["p_0101", "p_0102"] and legacy["sourceIds"] == {}
    place = read(paths["places_dir"] / "p_0101.json")
    assert place["source"] == "team" and place["sourceId"] == "naver:100"
    assert place["availability"]["naverPlaceId"] == "100" and recommendation_ready(place)
    assert place["research"]["facts"]["menus"]["sourceIds"] == ["naver-menu"]
    assert not check_dirs([paths["places_dir"]], today=TODAY).errors
    assert paths["manifest_path"].read_bytes() == original


def test_timed_menu_keeps_its_condition_in_observation_and_attrs_on_reimport(paths):
    timed = {"name": "점심 정식", "price": 19900, "main": True,
             "orderWindow": {"start": "11:00", "end": "15:00"}}
    manifest(paths, [row(menus=[timed])])
    run(paths)
    before = snapshot(paths["places_dir"])
    p = read(paths["places_dir"] / "p_0101.json")
    assert p["attrs"]["menus"] == p["menuObservation"]["menus"] == [timed]
    run(paths)
    assert snapshot(paths["places_dir"]) == before


def test_mixed_research_dates_do_not_refresh_old_rows_or_erase_newer_prices(paths):
    manifest(paths, [row("100"), row("900")])
    run(paths)
    old = read(paths["places_dir"] / "p_0101.json")
    old["menuObservation"].update(checkedAt="2026-11-12", menus=[{"name": "정식", "price": 9000, "main": True}])
    dump(paths["places_dir"] / "p_0101.json", old)
    manifest(paths, [row("100"), row("900"), row("999", checkedAt="2026-11-12")])
    curator.apply_naver_catalog(**paths, today=date(2026, 11, 12))
    kept = read(paths["places_dir"] / "p_0101.json")
    added = read(paths["places_dir"] / "p_0103.json")
    assert kept["availability"]["checkedAt"] == "2026-11-11"
    assert kept["menuObservation"] == old["menuObservation"]
    assert added["availability"]["checkedAt"] == added["research"]["checkedAt"] == "2026-11-12"
    before = snapshot(paths["places_dir"])
    curator.apply_naver_catalog(**paths, today=date(2026, 11, 12))
    assert snapshot(paths["places_dir"]) == before


def test_future_row_research_date_is_rejected_before_writing(paths):
    manifest(paths, [row(checkedAt="2026-11-12")])
    with pytest.raises(ImportPlacesError, match="미래"):
        run(paths)
    assert not paths["places_dir"].exists() and not paths["registry_path"].exists()


def test_shared_store_menu_price_is_known_but_does_not_make_a_ready_meal(paths):
    manifest(paths, [row(menus=[{"name": "피자 한 판", "price": 20000, "main": False}])])
    report = run(paths)
    p = read(paths["places_dir"] / "p_0101.json")
    assert p["menuObservation"]["status"] == "confirmed"
    assert not recommendation_ready(p) and report["ready"] == 0


def test_announced_date_closure_is_preserved_and_validated(paths):
    manifest(paths, [row(closedDates=["2026-11-19"])])
    run(paths)
    place = read(paths["places_dir"] / "p_0101.json")
    assert place["hoursObservation"]["closedDates"] == ["2026-11-19"]
    assert not check_dirs([paths["places_dir"]], today=TODAY).errors
    manifest(paths, [row(closedDates=["2026-02-30"])])
    with pytest.raises(ImportPlacesError):
        run(paths)


def test_repeat_is_byte_and_mtime_identical_and_row_order_does_not_change_ids(paths):
    manifest(paths, [row("200"), row("100")])
    run(paths)
    before = snapshot(paths["places_dir"]), snapshot(paths["registry_path"].parent)
    report = run(paths)
    assert report["created"] == report["updated"] == 0 and report["unchanged"] == 2
    assert not report["registryChanged"] and not report["legacyRegistryChanged"]
    assert before == (snapshot(paths["places_dir"]), snapshot(paths["registry_path"].parent))
    manifest(paths, [row("100"), row("200")])
    assert run(paths)["places"] == report["places"]


def test_dry_run_leaves_files_directories_and_registries_untouched(paths):
    manifest(paths, [row()])
    before = snapshot(paths["manifest_path"].parent)
    report = run(paths, dry_run=True)
    assert report["created"] == 1 and report["dryRun"]
    assert not paths["places_dir"].exists()
    assert before == snapshot(paths["manifest_path"].parent)


def test_existing_explicit_match_preserves_id_source_attrs_and_research(paths):
    old = existing(paths)
    old["research"] = {
        "checkedAt": "2026-11-10", "method": "web", "identityStatus": "matched", "queries": ["과거 조사"],
        "sources": [], "facts": {k: {"status": "unconfirmed", "sourceIds": [], "note": "과거 미확인"} for k in ("menus", "hours")},
        "unknownFields": [], "notes": ["보존할 과거 조사"]}
    dump(paths["places_dir"] / "p_0001.json", old)
    manifest(paths, [row(existingPlaceId="p_0001")])
    report = run(paths)
    updated = read(paths["places_dir"] / "p_0001.json")
    for key in ["id", "source", "sourceId", "attrs", "research"]:
        assert updated[key] == old[key]
    assert report["created"] == 0
    assert updated["menuObservation"]["menus"][0]["price"] == 8500
    assert updated["name"] == "확인한 밥집 12345"


def test_absent_existing_attrs_fields_are_filled_but_existing_fields_stay(paths):
    old = existing(paths, attrs={"status": "partial", "menus": [{"name": "옛 메뉴", "price": 7000, "main": True}]})
    manifest(paths, [row(existingPlaceId="p_0001")])
    run(paths)
    updated = read(paths["places_dir"] / "p_0001.json")
    assert updated["attrs"]["menus"] == old["attrs"]["menus"]
    assert updated["attrs"]["hours"] == row()["hours"]
    assert updated["research"]["facts"]["menus"]["status"] == "conflict"
    assert recommendation_ready(updated)  # 별도 최신 관찰이 준비 판정을 뒷받침한다.


@pytest.mark.parametrize("context", ["delivery", "unknown"])
def test_non_store_prices_remain_observations_and_do_not_become_attrs(paths, context):
    manifest(paths, [row(priceContext=context)])
    run(paths)
    result = read(paths["places_dir"] / "p_0101.json")
    assert result["attrs"] == {"status": "partial", "hours": row()["hours"]}
    assert result["hoursObservation"]["status"] == "confirmed"
    assert result["menuObservation"]["priceContext"] == context
    assert not recommendation_ready(result)


def test_old_catalog_import_preserves_newer_menu_and_monthly_hours_observations(paths):
    manifest(paths, [row()])
    run(paths)
    path = paths["places_dir"] / "p_0101.json"
    p = read(path)
    p["menuObservation"]["checkedAt"] = "2026-11-12"
    p["menuObservation"]["menus"][0]["price"] = 11000
    p["hoursObservation"]["checkedAt"] = "2026-11-12"
    p["hoursObservation"]["monthlyClosed"] = [{"day": "월", "weeks": [2, 4]}]
    dump(path, p)
    curator.apply_naver_catalog(**paths, today=date(2026, 11, 12))
    current = read(path)
    for field in ("menuObservation", "hoursObservation", "attrs"):
        assert current[field] == p[field]


def test_unpriced_or_unconfirmed_menu_is_not_fabricated(paths):
    manifest(paths, [row(menus=[{"name": "시가", "price": None, "main": True}], menuStatus="unconfirmed")])
    run(paths)
    result = read(paths["places_dir"] / "p_0101.json")
    assert "menus" not in result["attrs"]
    assert result["menuObservation"]["menus"][0]["price"] is None
    assert not recommendation_ready(result)


def test_mixed_store_delivery_observations_only_promote_store_prices(paths):
    manifest(paths, [row(menus=[{"name": "식사 매장", "price": 20000, "main": True, "priceContext": "in_store"},
                               {"name": "식사 배달", "price": 25000, "main": True, "priceContext": "delivery"},
                               {"name": "기본 반찬", "price": 0, "main": False}])])
    run(paths)
    result = read(paths["places_dir"] / "p_0101.json")
    assert result["attrs"]["menus"] == [{"name": "식사 매장", "price": 20000, "main": True}]
    assert len(result["menuObservation"]["menus"]) == 3


def test_fresh_naver_menu_and_hours_resolve_old_research_conflict_without_rewriting_history(paths):
    old = existing(paths, attrs={"status": "partial", "menus": [{"name": "기존 식사", "price": 8000, "main": True}]})
    old["research"] = {
        "checkedAt": "2026-11-10", "method": "web", "identityStatus": "matched", "queries": ["옛 웹 조사"],
        "sources": [], "facts": {k: {"status": "conflict", "sourceIds": [], "note": "과거 자료가 서로 다름"} for k in ("menus", "hours")},
        "unknownFields": ["hours"], "notes": ["보존할 충돌 이력"]}
    dump(paths["places_dir"] / "p_0001.json", old)
    manifest(paths, [row(existingPlaceId="p_0001")])
    run(paths)
    current = read(paths["places_dir"] / "p_0001.json")
    assert current["research"] == old["research"]
    assert current["attrs"]["menus"] == old["attrs"]["menus"]
    assert current["attrs"]["hours"] == row()["hours"]
    assert current["hoursObservation"]["status"] == current["menuObservation"]["status"] == "confirmed"
    assert recommendation_ready(current)


@pytest.mark.parametrize("change", ["different_menu_place", "different_hours_place", "older", "delivery", "unconfirmed"])
def test_observations_cannot_bypass_conflicts_using_wrong_branch_old_or_delivery_information(paths, change):
    old = existing(paths, attrs={"status": "partial", "menus": [{"name": "기존 식사", "price": 8000, "main": True}]})
    old["research"] = {
        "checkedAt": "2026-11-11", "method": "web", "identityStatus": "matched", "queries": ["기존 조사"],
        "sources": [], "facts": {k: {"status": "conflict", "sourceIds": [], "note": "정보 상충"} for k in ("menus", "hours")},
        "unknownFields": ["hours"], "notes": []}
    dump(paths["places_dir"] / "p_0001.json", old)
    manifest(paths, [row(existingPlaceId="p_0001")])
    run(paths)
    current = read(paths["places_dir"] / "p_0001.json")
    if change == "different_menu_place": current["menuObservation"]["sourceUrl"] = "https://pcmap.place.naver.com/restaurant/999/menu/list"
    elif change == "different_hours_place": current["hoursObservation"]["sourceUrl"] = "https://map.naver.com/p/entry/place/999"
    elif change == "older": current["hoursObservation"]["checkedAt"] = "2026-11-10"
    elif change == "delivery": current["menuObservation"]["priceContext"] = "delivery"
    elif change == "unconfirmed": current["hoursObservation"]["status"] = "unconfirmed"
    assert not recommendation_ready(current)


def test_unselected_old_places_are_excluded_without_claiming_search_failure_or_closure(paths):
    old = existing(paths)
    manifest(paths, [row()])
    report = run(paths)
    archived = read(paths["places_dir"] / "p_0001.json")
    assert report["excluded"] == 1
    assert archived["availability"]["status"] == "excluded"
    assert archived["availability"]["reason"] == curator.EXCLUDED_REASON
    assert "폐업" not in archived["availability"]["reason"] and "검색 안" not in archived["availability"]["reason"]
    assert archived["attrs"] == old["attrs"] and archived["sourceStatus"] == "active"
    assert not recommendation_ready(archived)


@pytest.mark.parametrize("change", [
    "empty", "duplicate_naver", "duplicate_existing", "future", "wrong_address", "missing_existing",
    "far", "bad_hours", "bad_price", "unknown_field", "bad_url", "cafe",
])
def test_invalid_manifest_is_rejected_before_any_file_changes(paths, change):
    existing(paths)
    entries = [row()]
    if change == "empty": entries = []
    elif change == "duplicate_naver": entries.append(row())
    elif change == "duplicate_existing": entries = [row("1", existingPlaceId="p_0001"), row("2", existingPlaceId="p_0001")]
    elif change == "wrong_address": entries[0].update(existingPlaceId="p_0001", address="광주 북구 우치로 999")
    elif change == "missing_existing": entries[0]["existingPlaceId"] = "p_0999"
    elif change == "far": entries[0].update(lat=37.55, lng=126.98)
    elif change == "bad_hours": entries[0]["hours"][0].update(open="20:00", close="10:00")
    elif change == "bad_price": entries[0]["menus"][0]["price"] = -1
    elif change == "unknown_field": entries[0]["random"] = True
    elif change == "bad_url": entries[0]["sourceUrl"] = "https://evil.example/place/1"
    elif change == "cafe": entries[0]["category"] = "카페·베이커리"
    manifest(paths, entries)
    if change == "future":
        data = read(paths["manifest_path"]); data["checkedAt"] = "2026-11-12"; dump(paths["manifest_path"], data)
    before = snapshot(paths["manifest_path"].parent)
    with pytest.raises(ImportPlacesError): run(paths)
    assert before == snapshot(paths["manifest_path"].parent)


def test_new_id_skips_existing_and_both_registries_reservations(paths):
    existing(paths, "p_0140")
    dump(paths["legacy_registry_path"], {"version": 1, "nextId": 151, "sourceIds": {}, "reservedIds": ["p_0150"]})
    dump(paths["registry_path"], {"version": 1, "nextId": 161, "naverPlaceIds": {"999": "p_0160"}, "reservedIds": ["p_0160"]})
    manifest(paths, [row()])
    assert run(paths)["places"][0]["placeId"] == "p_0161"


def test_partial_place_write_failure_reuses_reserved_mapping_on_retry(paths, monkeypatch):
    manifest(paths, [row("1"), row("2")])
    write = curator._atomic_write
    def fail_second(path, raw):
        if path.name == "p_0102.json": raise OSError("simulated interrupted write")
        return write(path, raw)
    monkeypatch.setattr(curator, "_atomic_write", fail_second)
    with pytest.raises(OSError): run(paths)
    assigned = read(paths["registry_path"])["naverPlaceIds"]
    assert assigned == {"1": "p_0101", "2": "p_0102"}
    monkeypatch.setattr(curator, "_atomic_write", write)
    run(paths)
    assert read(paths["registry_path"])["naverPlaceIds"] == assigned
    assert len(list(paths["places_dir"].glob("*.json"))) == 2


def test_external_edit_after_planning_is_not_overwritten(paths, monkeypatch):
    old = existing(paths)
    manifest(paths, [row()])
    original_lock = curator._lock
    changed = False
    @contextmanager
    def edit_once(path):
        nonlocal changed
        with original_lock(path):
            if not changed:
                changed = True
                old["attrs"]["note"] = "외부에서 추가한 메모"
                dump(paths["places_dir"] / "p_0001.json", old)
            yield
    monkeypatch.setattr(curator, "_lock", edit_once)
    with pytest.raises(ImportPlacesError, match="바뀌었습니다"):
        run(paths)
    assert read(paths["places_dir"] / "p_0001.json")["attrs"]["note"] == "외부에서 추가한 메모"
    assert not paths["registry_path"].exists()


def test_public_generator_preserves_curated_exclusions_and_skips_naver_reserved_ids(paths):
    old = existing(paths)
    manifest(paths, [row()])
    run(paths)
    before = read(paths["places_dir"] / "p_0001.json")
    source = paths["manifest_path"].parent / "source.csv"
    with source.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["sourceId", "name", "category", "address", "lat", "lng"])
        writer.writeheader()
        writer.writerow({key: old[key] for key in writer.fieldnames})
        writer.writerow({"sourceId": "NEW", "name": "다른 공공 식당", "category": "한식", "address": "광주 북구 우치로 96", "lat": 35.178, "lng": 126.914})
    import_places(source, paths["places_dir"], paths["legacy_registry_path"], today=TODAY)
    after = read(paths["places_dir"] / "p_0001.json")
    assert after["availability"] == before["availability"] and after["attrs"] == before["attrs"]
    assert not recommendation_ready(after)
    assert read(paths["places_dir"] / "p_0101.json")["sourceId"] == "naver:12345"
    assert read(paths["legacy_registry_path"])["sourceIds"]["NEW"] == "p_0102"
