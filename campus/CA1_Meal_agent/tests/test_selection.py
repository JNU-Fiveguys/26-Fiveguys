import csv
import io
import json

import pytest

from meal.selection import exclusion_reason, main, select_candidates, write_selection


def row(source_id="source-1", name="맛있는밥집", distance=10, **changes):
    data = {"sourceId": source_id, "name": name, "category": "한식", "subCategory": "백반/한정식",
            "boundaryM": str(distance), "lat": "35.1765", "lng": "126.9095",
            "address": "광주 북구 용봉로 77", "floor": "1", "licensed": "", "goodPriceMenu": "제육백반",
            "goodPrice": "7000", "mealTypeHint": "일반", "walkMinHint": "1"}
    data.update(changes)
    return data


def save_csv(path, rows):
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    path.write_bytes(text.getvalue().encode("utf-8-sig"))
    return path


@pytest.mark.parametrize("name,reason", [
    ("전대카페", "name_cafe"), ("고마다커피", "name_cafe"), ("복지센터비원cafe", "name_cafe"),
    ("COFFEE BEAN 전남", "name_cafe"), ("Café 대학", "name_cafe"),
    ("CU전남대점", "name_convenience"), ("GS25 전대점", "name_convenience"),
    ("세븐일레븐 전대", "name_convenience"), ("이마트24 용봉", "name_convenience"),
    ("씨유전남", "name_convenience"), ("미니스톱 전대", "name_convenience"),
    ("별빛호프", "name_bar"), ("낭만포차", "name_bar"), ("청춘술집", "name_bar"),
    ("용봉민속주점", "name_bar"), ("주점 용봉", "name_bar"),
    ("이자카야하루", "name_bar"), ("맥주창고", "name_bar"), ("골목와인바", "name_bar"),
    ("막걸리마당", "name_bar"), ("동네 PUB", "name_bar"),
])
def test_misclassified_cafes_convenience_stores_and_bars_are_excluded(name, reason):
    assert exclusion_reason(row(name=name), {}) == reason


@pytest.mark.parametrize("name", ["뉴요커피자", "디바농장광주점", "김밥천국문흥대주점", "바로김밥",
                                  "술돼지", "CUCUMBER 샐러드", "대학 Cafeteria", "바른밥상", "청년다방 전대점"])
def test_meal_names_are_not_rejected_by_incidental_substrings(name):
    assert exclusion_reason(row(name=name), {}) is None


def test_unrecognized_subcategory_is_excluded_instead_of_defaulting_to_korean():
    assert exclusion_reason(row(subCategory="처음보는업종"), {}) == "subcategory"
    assert exclusion_reason(row(subCategory="카페"), {}) == "subcategory"
    assert exclusion_reason(row(category="카페·베이커리"), {}) == "subcategory"


def test_default_selection_has_100_unique_sources_and_excludes_reviewed_places():
    from meal.selection import SOURCE_ID_EXCLUSIONS

    selection = select_candidates()
    ids = {r["sourceId"] for r in selection.rows}
    assert len(selection.rows) == len(ids) == 100
    assert ids.isdisjoint(SOURCE_ID_EXCLUSIONS)
    assert all(exclusion_reason(r) is None for r in selection.rows)


def test_review_exclusion_uses_exact_sourceid_not_all_similarly_named_stores():
    assert exclusion_reason(row(), {"source-1": "현장 확인 전 보류"}) == "review_exclusion"
    assert exclusion_reason(row("source-2"), {"source-1": "현장 확인 전 보류"}) is None


def test_tied_distances_are_deterministic_and_limit_is_exact(tmp_path):
    rows = [row(f"source-{i}", name=f"밥집{i}", distance=i % 2) for i in range(8)]
    first = select_candidates(save_csv(tmp_path / "one.csv", rows), limit=4)
    second = select_candidates(save_csv(tmp_path / "two.csv", list(reversed(rows))), limit=4)
    assert [r["sourceId"] for r in first.rows] == ["source-0", "source-2", "source-4", "source-6"]
    assert first.rows == second.rows
    assert first.report["excludedCounts"] == {"outside_limit": 4}


def test_exact_store_dedup_prefers_license_then_sourceid_and_keeps_floors(tmp_path):
    rows = [row("source-1", name="맛 집", licensed=""),
            row("source-2", name="맛집", licensed="주소+이름"),
            row("source-3", name="맛집", licensed="주소+이름"),
            row("source-4", name="맛집", floor="2"),
            row("source-5", name="다른밥집")]
    result = select_candidates(save_csv(tmp_path / "source.csv", rows))
    assert [r["sourceId"] for r in result.rows] == ["source-2", "source-4", "source-5"]
    assert result.report["excludedCounts"]["duplicate_store"] == 2
    assert all(r["keptSourceId"] == "source-2" for r in result.report["excluded"])


def test_missing_address_is_not_deduplicated_by_name_or_coordinate_only(tmp_path):
    result = select_candidates(save_csv(tmp_path / "source.csv", [row("source-1", address=""),
                                                                 row("source-2", address="")]))
    assert len(result.rows) == 2


def test_output_keeps_source_fields_and_hints_without_creating_attrs(tmp_path):
    source = save_csv(tmp_path / "source.csv", [row(extraField="조사 후보 메모")])
    original = source.read_bytes()
    selected = select_candidates(source)
    output, report = tmp_path / "selected.csv", tmp_path / "report.json"
    write_selection(selected, source_path=source, output_path=output, report_path=report)
    with output.open(encoding="utf-8-sig", newline="") as stream:
        written = list(csv.DictReader(stream))
    assert written == [row(extraField="조사 후보 메모")]
    assert "attrs" not in written[0]
    assert source.read_bytes() == original
    assert output.read_bytes().startswith(b"\xef\xbb\xbf")
    assert json.loads(report.read_text(encoding="utf-8"))["selectedCount"] == 1
    before = (output.read_bytes(), report.read_bytes(), output.stat().st_mtime_ns, report.stat().st_mtime_ns)
    write_selection(selected, source_path=source, output_path=output, report_path=report)
    assert before == (output.read_bytes(), report.read_bytes(), output.stat().st_mtime_ns, report.stat().st_mtime_ns)


def test_dry_run_does_not_create_output_or_report(tmp_path, capsys):
    source = save_csv(tmp_path / "source.csv", [row()])
    output, report = tmp_path / "not-created" / "out.csv", tmp_path / "not-created" / "report.json"
    assert main(["--source", str(source), "--output", str(output), "--report", str(report), "--dry-run"]) == 0
    assert not output.parent.exists()
    assert "미리보기" in capsys.readouterr().out


@pytest.mark.parametrize("changes", [{"lat": "nan"}, {"lng": "inf"}, {"lat": "0"},
                                      {"boundaryM": "-1"}, {"sourceId": " "}, {"sourceId": " id"},
                                      {"name": " "}, {"category": "없는분류"}])
def test_invalid_source_records_fail_before_output(tmp_path, changes):
    source = save_csv(tmp_path / "source.csv", [row(**changes)])
    with pytest.raises(ValueError):
        select_candidates(source)
    assert list(tmp_path.iterdir()) == [source]


def test_duplicate_sourceids_are_not_silently_merged(tmp_path):
    source = save_csv(tmp_path / "source.csv", [row(), row(name="다른가게")])
    with pytest.raises(ValueError, match="sourceId"):
        select_candidates(source)


def test_source_overwrite_and_source_changes_are_rejected(tmp_path):
    source = save_csv(tmp_path / "source.csv", [row()])
    selected = select_candidates(source)
    with pytest.raises(ValueError, match="서로 달라야"):
        write_selection(selected, source_path=source, output_path=source, report_path=tmp_path / "report.json")
    save_csv(source, [row(name="변경된가게")])
    with pytest.raises(ValueError, match="바뀌었습니다"):
        write_selection(selected, source_path=source, output_path=tmp_path / "selected.csv", report_path=tmp_path / "report.json")
    assert not (tmp_path / "selected.csv").exists()


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_limit_must_be_positive_integer(tmp_path, limit):
    with pytest.raises(ValueError, match="limit"):
        select_candidates(tmp_path / "not-read.csv", limit=limit)
