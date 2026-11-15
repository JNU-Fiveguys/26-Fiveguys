import copy
import json
from datetime import date

import pytest

from meal import check_data, places
from meal.places import SAMPLES_DIR, check_dirs
from meal.ranking import MealContext, rank
from meal.routing import EstimateRouter

TODAY = date(2026, 11, 11)
CONTEXT = MealContext(2, 710, 810, (35.1765, 126.9095), (35.1765, 126.9095))


def surveyed(pid="p_0001", source_id="source-1", status="active"):
    place = json.loads((SAMPLES_DIR / "s_01.json").read_text(encoding="utf-8"))
    place.update(id=pid, source="semas", sourceId=source_id, sourceStatus=status)
    return place


def save(folder, place):
    folder.mkdir(exist_ok=True)
    path = folder / f"{place['id']}.json"
    path.write_text(json.dumps(place, ensure_ascii=False), encoding="utf-8")
    return path


def test_missing_surveyed_place_remains_loadable_but_not_recommended(tmp_path, monkeypatch):
    folder = tmp_path / "places"
    place = surveyed(status="missing")
    save(folder, place)
    monkeypatch.setattr(places, "PLACES_DIR", folder)
    loaded = places.load_places()
    assert loaded == [place]
    result = rank(loaded, CONTEXT, router=EstimateRouter())
    assert not result.top and not result.more and not result.tight and not result.cafeteria
    assert result.excluded["unavailable"] == 1 and result.excluded["noInfo"] == 0


def test_missing_status_precedes_missing_attrs():
    place = surveyed(status="missing")
    del place["attrs"]
    result = rank([place], CONTEXT, router=EstimateRouter())
    assert result.excluded["unavailable"] == 1
    assert result.excluded["noInfo"] == 0


def test_active_without_attrs_is_not_recommended():
    place = surveyed()
    del place["attrs"]
    result = rank([place], CONTEXT, router=EstimateRouter())
    assert result.excluded["noInfo"] == 1
    assert result.excluded["unavailable"] == 0


def test_reappearing_place_keeps_its_survey_and_becomes_eligible(tmp_path, monkeypatch):
    folder = tmp_path / "places"
    place = surveyed(status="missing")
    original_attrs = copy.deepcopy(place["attrs"])
    save(folder, place)
    monkeypatch.setattr(places, "PLACES_DIR", folder)
    assert not rank(places.load_places(), CONTEXT, router=EstimateRouter()).top
    place["sourceStatus"] = "active"
    save(folder, place)
    candidate = rank(places.load_places(), CONTEXT, router=EstimateRouter()).top[0]
    assert candidate.place_id == "p_0001"
    assert candidate.place["attrs"] == original_attrs


def test_legacy_place_without_status_defaults_to_active():
    place = surveyed()
    del place["sourceStatus"]
    assert [c.place_id for c in rank([place], CONTEXT, router=EstimateRouter()).top] == ["p_0001"]


@pytest.mark.parametrize("second_status", ["active", "missing"])
def test_duplicate_sourceid_rejected_even_for_different_place_ids(tmp_path, monkeypatch, second_status):
    folder = tmp_path / "places"
    save(folder, surveyed())
    save(folder, surveyed("p_0002", status=second_status))
    checked = check_dirs([folder], today=TODAY)
    assert any("sourceId source-1" in issue.message for issue in checked.errors)
    monkeypatch.setattr(places, "PLACES_DIR", folder)
    with pytest.raises(ValueError, match="sourceId"):
        places.load_places()


def test_summary_groups_warnings_without_listing_every_place(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "places"
    for i in range(1, 4):
        place = surveyed(f"p_{i:04}", f"source-{i}", "missing" if i == 3 else "active")
        if i != 1:
            del place["attrs"]
        save(folder, place)
    checked = check_dirs([folder], today=TODAY)
    assert not checked.errors
    monkeypatch.setattr(check_data, "check_dirs", lambda: checked)
    assert check_data.main(["--summary"]) == 0
    output = capsys.readouterr().out
    assert "attrs 미조사: 2건" in output
    assert "후보 목록 누락·재확인 필요: 1건" in output
    assert "가게 3곳 · 추천 가능 1곳 · 오류 0 · 경고 3" in output
    assert "실제 가게 3곳" in output and "샘플 0곳" in output
    assert "p_0001" not in output and "p_0002" not in output


def test_summary_does_not_hide_errors(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "places"
    save(folder, surveyed())
    save(folder, surveyed("p_0002"))
    checked = check_dirs([folder], today=TODAY)
    monkeypatch.setattr(check_data, "check_dirs", lambda: checked)
    assert check_data.main(["--summary"]) == 1
    output = capsys.readouterr().out
    assert "[오류]" in output and "sourceId" in output
    assert "오류 1" in output
