import copy
import json
from datetime import date

import pytest

from meal.places import (
    DATA_DIR,
    SAMPLES_DIR,
    check_dirs,
    price_band,
    price_label,
    rep_price,
    recommendation_ready,
    load_places,
)

TODAY = date(2026, 11, 11)


def _load(pid: str) -> dict:
    return json.loads((SAMPLES_DIR / f"{pid}.json").read_text(encoding="utf-8"))


def _check_one(tmp_path, place: dict, folder: str = "samples"):
    d = tmp_path / folder
    d.mkdir(exist_ok=True)
    (d / f"{place['id']}.json").write_text(json.dumps(place, ensure_ascii=False), encoding="utf-8")
    return check_dirs([d], today=TODAY)


# ── 샘플 데이터 자체 ─────────────────────────────────────────

def test_samples_have_no_errors():
    result = check_dirs([SAMPLES_DIR], today=TODAY)
    assert result.errors == []
    assert len(result.places) == 8


def test_sample_without_attrs_is_warned_not_error():
    result = check_dirs([SAMPLES_DIR], today=TODAY)
    s06 = [i for i in result.issues if i.file.endswith("s_06.json")]
    assert [i.level for i in s06] == ["warn"]


def test_far_place_is_warned(tmp_path):
    p = _load("s_07")  # 원래 약 1.3km — 경고 기준(1.5km) 안쪽
    assert not any("캠퍼스 중심" in i.message for i in _check_one(tmp_path, p).issues)
    p["lat"] = 35.15   # 약 3km
    result = _check_one(tmp_path, p)
    assert result.errors == []
    assert any("캠퍼스 중심" in i.message for i in result.issues)


# ── 가격대 ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "price, band, label",
    [(3500, "A", "3천 원대"), (7000, "A", "7천 원대"), (7250, "B", "7천 원대"),
     (10000, "B", "1만 원대"), (14000, "C", "1만 원대"), (15001, "D", "1만 원대"), (None, None, None)],
)
def test_price_band_and_label(price, band, label):
    assert price_band(price) == band
    assert price_label(price) == label


def test_rep_price_is_median_of_main_menus():
    assert rep_price(_load("s_01")) == 7250   # 7000 · 7500 (계란말이 6000 은 main 아님)
    assert rep_price(_load("s_05")) == 4500   # 3500 · 4500 · 4500
    assert rep_price(_load("s_06")) is None   # 정보 없음


def baseline_place(pid):
    baseline = json.loads((DATA_DIR / "survey" / "naver_2026-11-11" / "baseline_catalog.json").read_text(encoding="utf-8"))
    return next(p for p in baseline["places"] if p["id"] == pid)


def test_legacy_confirmed_menu_and_hours_remain_supported():
    baseline = json.loads((DATA_DIR / "survey" / "naver_2026-11-11" / "baseline_catalog.json").read_text(encoding="utf-8"))
    ready = {p["id"] for p in baseline["places"] if recommendation_ready(p)}
    assert ready == {"p_0001", "p_0011", "p_0040", "p_0068", "p_0071", "p_0076", "p_0078", "p_0089", "p_0092"}


@pytest.mark.parametrize("field", ["menus", "hours"])
@pytest.mark.parametrize("status", ["unconfirmed", "partial", "conflict"])
def test_incomplete_web_evidence_stays_out_of_recommendations(field, status):
    p = baseline_place("p_0040")
    assert recommendation_ready(p)
    p["research"]["facts"][field]["status"] = status
    assert not recommendation_ready(p)


def test_samples_no_longer_store_seating_or_solo_attributes():
    for p in check_dirs([SAMPLES_DIR], today=TODAY).places:
        assert "soloOk" not in p.get("attrs", {})
        assert "seats" not in p.get("attrs", {})


@pytest.mark.parametrize("field, value", [("soloOk", True), ("seats", "M")])
def test_removed_attributes_are_rejected_by_schema(tmp_path, field, value):
    p = _load("s_01")
    p["attrs"][field] = value
    assert _check_one(tmp_path, p).errors


# ── 규칙 위반을 잡는지 ──────────────────────────────────────

def test_schema_error_bad_category(tmp_path):
    p = _load("s_01")
    p["category"] = "맛집"
    assert any("형식 category" in i.message for i in _check_one(tmp_path, p).errors)


def test_break_outside_hours(tmp_path):
    p = _load("s_02")
    p["attrs"]["hours"][0]["break"] = {"start": "10:00", "end": "11:30"}
    assert any("브레이크" in i.message for i in _check_one(tmp_path, p).errors)


@pytest.mark.parametrize("last_order", ["14:30", "15:00", "16:00", "17:00"])
def test_last_order_before_or_during_break_is_rejected(tmp_path, last_order):
    p = _load("s_02")
    p["attrs"]["hours"][0]["lastOrder"] = last_order
    errors = _check_one(tmp_path, p).errors
    assert any("lastOrder" in i.message and "hours 행을 나눠" in i.message for i in errors)


@pytest.mark.parametrize("last_order", ["17:01", "20:30", "21:00"])
def test_last_order_in_final_segment_is_valid(tmp_path, last_order):
    p = _load("s_02")
    p["attrs"]["hours"][0]["lastOrder"] = last_order
    assert _check_one(tmp_path, p).errors == []


def test_separate_lunch_and_dinner_last_orders_are_valid(tmp_path):
    p = _load("s_02")
    days = p["attrs"]["hours"][0]["days"]
    p["attrs"]["hours"] = [
        {"days": days, "open": "11:00", "close": "15:00", "lastOrder": "14:30"},
        {"days": days, "open": "17:00", "close": "21:00", "lastOrder": "20:30"},
    ]
    assert _check_one(tmp_path, p).errors == []


def test_load_places_rejects_ambiguous_last_order(tmp_path, monkeypatch):
    from meal import places

    p = _load("s_02")
    p["attrs"]["hours"][0]["lastOrder"] = "14:30"
    _check_one(tmp_path, p)
    monkeypatch.setattr(places, "PLACES_DIR", tmp_path / "places")
    monkeypatch.setattr(places, "SAMPLES_DIR", tmp_path / "samples")
    with pytest.raises(ValueError, match="hours 행을 나눠"):
        places.load_places(include_samples=True)


def test_overlapping_hours_same_day(tmp_path):
    p = _load("s_08")
    p["attrs"]["hours"][1]["open"] = "13:00"
    assert any("겹칩니다" in i.message for i in _check_one(tmp_path, p).errors)


def test_no_main_menu(tmp_path):
    p = _load("s_01")
    for m in p["attrs"]["menus"]:
        m["main"] = False
    assert any("대표 메뉴" in i.message for i in _check_one(tmp_path, p).errors)


def test_future_verified_at(tmp_path):
    p = _load("s_01")
    p["attrs"]["verifiedAt"] = "2027-01-01"
    assert any("미래" in i.message for i in _check_one(tmp_path, p).errors)


def test_stale_verified_at_is_warning(tmp_path):
    p = _load("s_01")
    p["attrs"]["verifiedAt"] = "2026-06-01"
    result = _check_one(tmp_path, p)
    assert result.errors == []
    assert any("다시 확인" in i.message for i in result.issues)


def test_sample_in_places_dir_is_error(tmp_path):
    assert any("data/samples" in i.message for i in _check_one(tmp_path, _load("s_01"), "places").errors)


def test_real_place_needs_sourceid_for_semas(tmp_path):
    p = copy.deepcopy(_load("s_01"))
    p.update(id="p_001", source="semas")
    assert any("sourceId" in i.message for i in _check_one(tmp_path, p, "places").errors)


@pytest.mark.parametrize("source_id", ["   ", " source-1", "source-1 "])
def test_semas_sourceid_must_not_be_blank_or_padded(tmp_path, source_id):
    p = _load("s_01")
    p.update(id="p_001", source="semas", sourceId=source_id)
    assert any("sourceId" in i.message for i in _check_one(tmp_path, p, "places").errors)


@pytest.mark.parametrize("key,value", [("lat", float("nan")), ("lng", float("nan")),
                                       ("lat", float("inf")), ("lng", float("-inf"))])
def test_nonfinite_coordinates_rejected(tmp_path, key, value):
    p = _load("s_01")
    p[key] = value
    assert _check_one(tmp_path, p).errors


def test_filename_must_match_id(tmp_path):
    d = tmp_path / "samples"
    d.mkdir()
    (d / "wrong.json").write_text(json.dumps(_load("s_01"), ensure_ascii=False), encoding="utf-8")
    assert any("파일 이름" in i.message for i in check_dirs([d], today=TODAY).errors)
