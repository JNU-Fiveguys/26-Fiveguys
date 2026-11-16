import copy
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from meal import service
from meal.api import app
from meal.hours import check_open
from meal.places import SAMPLES_DIR, _check_rules, _validator, check_dirs, recommendation_ready, rep_price
from meal.ranking import MealContext, rank
from meal.research import apply_research, merge_record
from meal.routing import EstimateRouter
from meal.timing import compute_for_place


@pytest.fixture
def place():
    p = json.loads((SAMPLES_DIR / "s_01.json").read_text(encoding="utf-8"))
    p.pop("attrs")
    p.update(id="p_0001", source="semas", sourceId="TEST-ONE")
    return p


@pytest.fixture
def record(place):
    return {"placeId": place["id"], "sourceId": place["sourceId"], "name": place["name"],
            "identityStatus": "matched", "queries": ["식당 이름 주소 메뉴 영업시간"],
            "sources": [{"id": "s1", "url": "https://example.com/store/1", "title": "공식 매장", "type": "official",
                         "observedAt": "2026-11-11", "addressMatch": True, "note": "동일 주소"}],
            "facts": {"menus": {"status": "confirmed", "value": [{"name": "정식", "price": 8000, "main": True}],
                                  "sourceIds": ["s1"], "note": "메뉴 표에 표시된 가격"},
                      "hours": {"status": "unconfirmed", "sourceIds": [], "note": "요일별 시간 미확인"}},
            "notes": ["공개 자료 확인이며 현장 확인 아님"]}


def errors(p):
    schema = list(_validator().iter_errors(p))
    if schema:
        return [e.message for e in schema]
    return [i.message for i in _check_rules(p, p["id"] + ".json", False, date(2026, 11, 11)) if i.level == "error"]


def test_partial_attrs_store_only_known_values_and_do_not_recommend(place, record):
    p = merge_record(place, record, "2026-11-11")
    assert errors(p) == []
    assert set(p["attrs"]) == {"status", "menus"}
    assert rep_price(p) == 8000 and not recommendation_ready(p)
    assert "hours" in p["research"]["unknownFields"]
    assert check_open(p, 2, arrive=720, order_at=725, done=750).reason == "no_info"
    assert compute_for_place(p, start=720, end=840, origin=(35.1765, 126.9095), dest=None) is None
    ctx = MealContext(weekday=2, start=720, end=840, origin=(35.1765, 126.9095), dest=None)
    assert rank([p], ctx, router=EstimateRouter()).to_dict()["excluded"]["noInfo"] == 1


def test_confirmed_menu_and_hours_enable_web_place_without_other_attrs(place, record):
    record["facts"]["hours"] = {"status": "confirmed", "sourceIds": ["s1"], "note": "매일 영업시간 게시",
                                "value": [{"days": list("월화수목금토일"), "open": "09:00", "close": "21:00"}]}
    p = merge_record(place, record, "2026-11-11")
    assert errors(p) == [] and recommendation_ready(p)
    assert set(p["attrs"]) == {"status", "menus", "hours"}
    assert not {"soloOk", "seats"} & set(p["research"]["unknownFields"])
    ctx = MealContext(weekday=2, start=720, end=840, origin=(p["lat"], p["lng"]), dest=None)
    result = rank([p], ctx, router=EstimateRouter()).to_dict()
    assert len(result["candidates"]) == 1
    card = result["candidates"][0]
    assert card["mealTimeEstimated"] is True and card["breakdown"]["eat"] == 30
    assert card.get("takeout") is None
    assert "soloOk" not in card and "seats" not in card
    # 계산용 기본값을 조사된 속성으로 저장하지 않는다.
    assert "mealType" not in p["attrs"] and "verifiedBy" not in p["attrs"]


@pytest.mark.parametrize("status", ["partial", "unconfirmed", "conflict"])
def test_unconfirmed_observations_are_not_promoted_to_attrs(place, record, status):
    record["facts"]["menus"]["status"] = status
    p = merge_record(place, record, "2026-11-11")
    assert errors(p) == []
    assert p["attrs"] == {"status": "partial"}
    assert p["research"]["facts"]["menus"]["value"][0]["price"] == 8000
    assert rep_price(p) is None


@pytest.mark.parametrize("change", ["no_source", "bad_source", "no_value", "wrong_address", "uncertain_identity"])
def test_confirmed_fact_requires_traceable_same_store_evidence(place, record, change):
    if change == "no_source":
        record["facts"]["menus"]["sourceIds"] = []
    elif change == "bad_source":
        record["facts"]["menus"]["sourceIds"] = ["missing"]
    elif change == "no_value":
        record["facts"]["menus"].pop("value")
    elif change == "wrong_address":
        record["sources"][0]["addressMatch"] = False
    else:
        record["identityStatus"] = "uncertain"
    assert errors(merge_record(place, record, "2026-11-11"))


def test_existing_human_attrs_are_preserved(place, record):
    place["attrs"] = json.loads((SAMPLES_DIR / "s_01.json").read_text(encoding="utf-8"))["attrs"]
    before = copy.deepcopy(place["attrs"])
    merged = merge_record(place, record, "2026-11-11")
    assert merged["attrs"] == before
    assert recommendation_ready(merged)
    assert any("기존 attrs.menus" in n for n in merged["research"]["notes"])


def test_legacy_incomplete_attrs_remain_invalid(place):
    place["attrs"] = {"menus": [{"name": "정식", "price": 8000, "main": True}]}
    assert errors(place)
    place["attrs"]["status"] = "complete"
    assert errors(place)


def test_confirmed_research_hours_are_validated_even_when_existing_attrs_are_kept(place, record):
    place["attrs"] = json.loads((SAMPLES_DIR / "s_01.json").read_text(encoding="utf-8"))["attrs"]
    record["facts"]["hours"] = {"status": "confirmed", "sourceIds": ["s1"], "note": "잘못된 시간",
                                "value": [{"days": ["월"], "open": "20:00", "close": "10:00"}]}
    assert any("research.hours" in e for e in errors(merge_record(place, record, "2026-11-11")))


def test_closed_research_blocks_even_previously_complete_attrs(place, record):
    place["attrs"] = json.loads((SAMPLES_DIR / "s_01.json").read_text(encoding="utf-8"))["attrs"]
    record["identityStatus"] = "closed"
    record["facts"]["menus"]["status"] = "unconfirmed"
    merged = merge_record(place, record, "2026-11-11")
    assert errors(merged) == [] and not recommendation_ready(merged)


def test_apply_validates_entire_batch_and_reruns_without_writes(tmp_path, place, record):
    folder = tmp_path / "places"
    folder.mkdir()
    target = folder / "p_0001.json"
    target.write_text(json.dumps(place), encoding="utf-8")
    shard = tmp_path / "part.json"
    shard.write_text(json.dumps({"version": 1, "checkedAt": "2026-11-11", "places": [record]}), encoding="utf-8")
    before = target.read_bytes()
    assert apply_research([shard], places_dir=folder, dry_run=True)["changedFiles"] == 1
    assert target.read_bytes() == before
    result = apply_research([shard], places_dir=folder)
    assert result["menusConfirmed"] == 1 and result["ready"] == 0
    snapshot = target.read_bytes(), target.stat().st_mtime_ns
    assert apply_research([shard], places_dir=folder)["changedFiles"] == 0
    assert snapshot == (target.read_bytes(), target.stat().st_mtime_ns)
    assert check_dirs([folder], today=date(2026, 11, 11)).errors == []


def test_duplicate_or_missing_research_aborts_before_writing(tmp_path, place, record):
    folder = tmp_path / "places"
    folder.mkdir()
    target = folder / "p_0001.json"
    target.write_text(json.dumps(place), encoding="utf-8")
    before = target.read_bytes()
    shard = tmp_path / "part.json"
    for rows in ([], [record, record]):
        shard.write_text(json.dumps({"version": 1, "checkedAt": "2026-11-11", "places": rows}), encoding="utf-8")
        with pytest.raises(ValueError):
            apply_research([shard], places_dir=folder)
        assert target.read_bytes() == before


def test_catalog_exposes_research_separately_from_readiness(monkeypatch, place, record):
    p = merge_record(place, record, "2026-11-11")
    monkeypatch.setattr(service, "load_places", lambda **kw: [p])
    with TestClient(app) as client:
        catalog = client.get("/v1/meal/places").json()
        assert catalog["counts"]["ready"] == 0
        assert catalog["researchCounts"] == {"checked": 1, "menus": 1, "hours": 0}
        assert catalog["items"][0]["research"]["hours"] == "unconfirmed"
        detail = client.get("/v1/meal/places/p_0001").json()
        assert detail["research"]["sources"][0]["url"].startswith("https://")
        assert detail["attrs"]["menus"][0]["price"] == 8000 and not detail["ready"]
        assert client.get("/v1/meal/places/s_01").status_code == 404
