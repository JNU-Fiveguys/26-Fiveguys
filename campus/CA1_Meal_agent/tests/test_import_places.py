import copy
import csv
import json
from datetime import date
from pathlib import Path
import subprocess
import sys

import pytest

from meal import import_places as importer
from meal.places import SAMPLES_DIR, check_dirs

TODAY = date(2026, 11, 11)
HEADERS = ["sourceId", "name", "category", "address", "lat", "lng", "mealTypeHint", "goodPriceMenu", "goodPrice"]


def row(sid, **updates):
    return {"sourceId": sid, "name": f"식당 {sid}", "category": "한식", "address": "광주 북구 용봉로 77",
            "lat": "35.1765", "lng": "126.9095", "mealTypeHint": "일반", "goodPriceMenu": "백반",
            "goodPrice": "7000", **updates}


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def snapshot(path):
    return {p.relative_to(path): (p.read_bytes(), p.stat().st_mtime_ns) for p in path.rglob("*") if p.is_file()}


@pytest.fixture
def paths(tmp_path):
    return tmp_path / "selected.csv", tmp_path / "places", tmp_path / "place_ids.json"


def run(paths, **kwargs):
    return importer.import_places(*paths, today=TODAY, **kwargs)


def add_attrs(places, pid):
    value = read(places / f"{pid}.json")
    attrs = json.loads((SAMPLES_DIR / "s_01.json").read_text(encoding="utf-8"))["attrs"]
    attrs["note"] = "현장 조사 메모 — 자동 재생성으로 바뀌면 안 됨"
    value["attrs"] = attrs
    dump(places / f"{pid}.json", value)
    return copy.deepcopy(attrs)


def test_deterministic_ids_ignore_row_order_and_repeat_is_byte_identical(paths):
    source, places, registry = paths
    write_csv(source, [row("B"), row("A")])
    report = run(paths)
    assert (report.created, report.candidates) == (2, 2)
    assert read(registry)["sourceIds"] == {"A": "p_0001", "B": "p_0002"}
    assert not any("attrs" in read(p) for p in places.glob("*.json"))
    assert check_dirs([places], today=TODAY).errors == []
    before = snapshot(places), (registry.read_bytes(), registry.stat().st_mtime_ns)
    write_csv(source, [row("A"), row("B")])
    report = run(paths)
    assert report.created == report.updated == 0 and report.unchanged == 2
    assert not report.registry_changed
    assert before == (snapshot(places), (registry.read_bytes(), registry.stat().st_mtime_ns))


def test_same_name_and_coordinates_are_distinct_sources(paths):
    source, places, _ = paths
    write_csv(source, [row("A", name="같은 가게"), row("B", name="같은 가게")])
    run(paths)
    assert len(list(places.glob("*.json"))) == 2


def test_regeneration_updates_only_public_fields_and_preserves_all_attrs(paths):
    source, places, registry = paths
    write_csv(source, [row("A")])
    run(paths)
    attrs = add_attrs(places, "p_0001")
    write_csv(source, [row("A", name="새 간판", category="중식", lat="35.177", address="새 주소", goodPrice="1"), row("B")])
    report = run(paths)
    after = read(places / "p_0001.json")
    assert after["attrs"] == attrs
    assert after["name"] == "새 간판" and after["lat"] == 35.177 and after["category"] == "중식"
    assert (report.created, report.updated, report.attrs_preserved) == (1, 1, 1)
    assert read(registry)["sourceIds"]["A"] == "p_0001"


def test_missing_and_reappearance_keep_file_id_and_attrs(paths):
    source, places, registry = paths
    write_csv(source, [row("A"), row("B")])
    run(paths)
    attrs = add_attrs(places, "p_0001")
    write_csv(source, [row("B")])
    missing = run(paths)
    assert missing.missing == missing.marked_missing == 1
    assert read(places / "p_0001.json")["sourceStatus"] == "missing"
    assert read(places / "p_0001.json")["attrs"] == attrs
    second = run(paths)
    assert second.missing == 1 and second.marked_missing == second.updated == 0
    write_csv(source, [row("A"), row("B"), row("C")])
    restored = run(paths)
    assert restored.restored == 1 and restored.missing == 0
    assert read(places / "p_0001.json")["attrs"] == attrs
    assert read(registry)["sourceIds"] == {"A": "p_0001", "B": "p_0002", "C": "p_0003"}


def test_deleted_file_never_releases_id_and_reappearance_uses_reserved_id(paths):
    source, places, registry = paths
    write_csv(source, [row("A"), row("B")])
    run(paths)
    (places / "p_0001.json").unlink()
    write_csv(source, [row("B"), row("C")])
    run(paths)
    assert read(registry)["sourceIds"]["C"] == "p_0003"
    write_csv(source, [row("A"), row("B"), row("C")])
    run(paths)
    assert read(places / "p_0001.json")["sourceId"] == "A"


def test_missing_registry_recovers_legacy_and_manual_ids_without_changing_team_file(paths):
    source, places, registry = paths
    places.mkdir()
    base = {"version": 1, "id": "p_001", "name": "기존", "category": "한식", "lat": 35.1765,
            "lng": 126.9095, "source": "semas", "sourceId": "A"}
    dump(places / "p_001.json", base)
    team = {**base, "id": "p_0007", "source": "team"}
    team.pop("sourceId")
    dump(places / "p_0007.json", team)
    original = (places / "p_0007.json").read_bytes()
    write_csv(source, [row("A"), row("B")])
    report = run(paths)
    assert read(registry)["sourceIds"] == {"A": "p_001", "B": "p_0008"}
    assert report.team_preserved == 1
    assert (places / "p_0007.json").read_bytes() == original
    assert "p_0007" in read(registry)["reservedIds"]


def test_dry_run_creates_no_places_registry_or_lock(paths):
    source, places, registry = paths
    write_csv(source, [row("A")])
    before = snapshot(source.parent)
    report = run(paths, dry_run=True)
    assert report.dry_run and report.created == 1
    assert snapshot(source.parent) == before
    assert not places.exists() and not registry.exists()


@pytest.mark.parametrize("bad_rows", [[], [row("")], [row("A"), row("A")],
                                         [row("A", lat="nan")], [row("A", lng="Infinity")],
                                         [row("A", lat="35x")], [row("A", lat="99")],
                                         [row("A", category="술집")], [row("A", name="")]])
def test_invalid_csv_is_rejected_before_any_write(paths, bad_rows):
    source, _, _ = paths
    write_csv(source, [row("OLD")])
    run(paths)
    write_csv(source, bad_rows)
    before = snapshot(source.parent)
    with pytest.raises(importer.ImportPlacesError):
        run(paths)
    assert snapshot(source.parent) == before


def test_invalid_later_row_does_not_partially_import_valid_first_row(paths):
    source, _, _ = paths
    write_csv(source, [row("A"), row("Z", lat="nan")])
    before = snapshot(source.parent)
    with pytest.raises(importer.ImportPlacesError):
        run(paths)
    assert snapshot(source.parent) == before


def test_malformed_quoted_header_is_reported_without_writes(paths):
    source, _, _ = paths
    source.write_text('"sourceId,name,category,address,lat,lng\n', encoding="utf-8")
    before = snapshot(source.parent)
    with pytest.raises(importer.ImportPlacesError, match="헤더"):
        run(paths)
    assert snapshot(source.parent) == before


def test_registry_conflict_stops_without_touching_attrs(paths):
    source, places, registry = paths
    write_csv(source, [row("A"), row("B")])
    run(paths)
    add_attrs(places, "p_0001")
    mapping = read(registry)
    mapping["sourceIds"] = {"A": "p_0002", "B": "p_0001"}
    dump(registry, mapping)
    before = snapshot(source.parent)
    with pytest.raises(importer.ImportPlacesError, match="충돌"):
        run(paths)
    assert snapshot(source.parent) == before


def test_duplicate_existing_source_ids_are_rejected(paths):
    source, places, registry = paths
    write_csv(source, [row("A")])
    run(paths)
    existing = read(places / "p_0001.json")
    existing["id"] = "p_0002"
    dump(places / "p_0002.json", existing)
    before = snapshot(source.parent)
    with pytest.raises(importer.ImportPlacesError, match="sourceId.*중복"):
        run(paths)
    assert snapshot(source.parent) == before


def test_invalid_existing_attrs_are_not_replaced(paths):
    source, places, _ = paths
    write_csv(source, [row("A")])
    run(paths)
    value = read(places / "p_0001.json")
    value["attrs"] = {"note": "조사 중"}
    dump(places / "p_0001.json", value)
    before = snapshot(source.parent)
    with pytest.raises(importer.ImportPlacesError, match="attrs"):
        run(paths)
    assert snapshot(source.parent) == before


def test_concurrent_import_lock_is_not_removed_by_failed_second_import(paths):
    source, places, _ = paths
    write_csv(source, [row("A")])
    lock = places.parent / ".places.import.lock"
    lock.write_text("another importer", encoding="utf-8")
    before = snapshot(source.parent)
    with pytest.raises(importer.ImportPlacesError, match="잠금"):
        run(paths)
    assert snapshot(source.parent) == before


def test_shared_registry_lock_prevents_competing_id_allocations(paths):
    source, places, registry = paths
    write_csv(source, [row("A")])
    lock = registry.parent / f".{registry.name}.import.lock"
    lock.write_text("another destination uses this registry", encoding="utf-8")
    before = snapshot(source.parent)
    with pytest.raises(importer.ImportPlacesError, match="잠금"):
        run(paths)
    assert snapshot(source.parent) == before


def test_changed_survey_file_before_commit_is_not_overwritten(paths, monkeypatch):
    source, places, _ = paths
    write_csv(source, [row("A")])
    run(paths)
    attrs = add_attrs(places, "p_0001")
    write_csv(source, [row("A", name="새 이름")])
    verify = importer._verify_snapshot

    def edit_then_verify(plan):
        value = read(places / "p_0001.json")
        value["attrs"]["note"] = "동시에 새로 조사함"
        dump(places / "p_0001.json", value)
        verify(plan)

    monkeypatch.setattr(importer, "_verify_snapshot", edit_then_verify)
    with pytest.raises(importer.ImportPlacesError, match="바뀌었습니다"):
        run(paths)
    after = read(places / "p_0001.json")
    assert after["attrs"]["note"] == "동시에 새로 조사함"
    assert after["attrs"]["menus"] == attrs["menus"] and after["name"] == "식당 A"


def test_partial_write_failure_reserves_ids_and_retries_without_loss(paths, monkeypatch):
    source, places, registry = paths
    write_csv(source, [row("A"), row("B")])
    writer = importer._atomic_write

    def fail_second_place(path, raw):
        if path.name == "p_0002.json":
            raise OSError("disk full simulation")
        writer(path, raw)

    monkeypatch.setattr(importer, "_atomic_write", fail_second_place)
    with pytest.raises(OSError):
        run(paths)
    assigned = read(registry)["sourceIds"]
    assert assigned == {"A": "p_0001", "B": "p_0002"}
    assert (places / "p_0001.json").exists() and not (places / "p_0002.json").exists()
    attrs = add_attrs(places, "p_0001")
    monkeypatch.setattr(importer, "_atomic_write", writer)
    write_csv(source, [row("B"), row("A")])
    run(paths)
    assert read(registry)["sourceIds"] == assigned
    assert read(places / "p_0001.json")["attrs"] == attrs


def test_duplicate_json_keys_are_not_silently_accepted(paths):
    source, places, registry = paths
    write_csv(source, [row("A")])
    run(paths)
    raw = (places / "p_0001.json").read_text(encoding="utf-8")
    (places / "p_0001.json").write_text(raw.replace('"sourceId": "A"', '"sourceId": "B", "sourceId": "A"'), encoding="utf-8")
    with pytest.raises(importer.ImportPlacesError, match="중복"):
        run(paths)


def test_exhausted_id_space_fails_before_writing(paths):
    source, _, registry = paths
    write_csv(source, [row("NEW")])
    dump(registry, {"version": 1, "nextId": 10000, "sourceIds": {"OLD": "p_9999"}, "reservedIds": ["p_9999"]})
    before = snapshot(source.parent)
    with pytest.raises(importer.ImportPlacesError, match="9999"):
        run(paths)
    assert snapshot(source.parent) == before


def test_cli_dry_run_from_another_working_directory(paths):
    source, places, registry = paths
    write_csv(source, [row("A")])
    script = Path(__file__).resolve().parents[1] / "scripts" / "import_places.py"
    result = subprocess.run([sys.executable, str(script), "--source", str(source), "--places-dir", str(places),
                             "--registry", str(registry), "--dry-run"], cwd=source.parent,
                            capture_output=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr
    assert '"created": 1' in result.stdout and not places.exists()
