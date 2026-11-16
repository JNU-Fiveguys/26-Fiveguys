"""입력함은 부분 정보와 수정 이력을 보존하고, 추천 원본을 직접 바꾸지 않는다."""
import copy
import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from meal import survey
from meal.api import app
from meal.places import DATA_DIR, recommendation_ready
from meal.timetable import SEOUL


@pytest.fixture
def setup_survey(tmp_path, monkeypatch):
    places = [json.loads((DATA_DIR / "places" / f"{pid}.json").read_text(encoding="utf-8"))
              for pid in ("p_0013", "p_0017", "p_0158", "p_0040")]
    folder = tmp_path / "places"
    folder.mkdir()
    for p in places:
        (folder / (p["id"] + ".json")).write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(survey, "PLACES_DIR", folder)
    monkeypatch.setattr(survey, "ENTRIES_DIR", tmp_path / "user_entries")
    monkeypatch.setattr(survey, "load_places", lambda: copy.deepcopy(places))
    return places, folder


@pytest.fixture
def client(setup_survey):
    with TestClient(app) as c:
        yield c


def payload(**changes):
    return {"expectedRevision": 0, "checkedAt": datetime.now(SEOUL).date().isoformat(),
            "method": "map", "priceContext": "in_store", "menusConfirmed": True,
            "menus": [{"name": "직접 확인한 정식", "price": 8000, "main": True}],
            "sources": ["https://pcmap.place.naver.com/restaurant/13376550/menu/list"], **changes}


def test_queue_only_includes_active_unready_places_and_precise_missing_fields(client):
    data = client.get("/v1/meal/survey").json()
    assert data["counts"] == {"total": 2, "menus": 1, "hours": 1, "saved": 0}
    rows = {p["id"]: p for p in data["places"]}
    assert set(rows) == {"p_0013", "p_0017"}
    assert [m["key"] for m in rows["p_0013"]["missing"]] == ["menus"]
    assert [m["key"] for m in rows["p_0017"]["missing"]] == ["hours"]


def test_save_and_reload_keep_prices_and_sources_without_modifying_recommendation_files(client, setup_survey):
    places, folder = setup_survey
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.glob("*.json")}
    response = client.post("/v1/meal/survey/p_0013", json=payload())
    assert response.status_code == 200
    saved = response.json()
    assert saved["status"] == "draft" and saved["revision"] == 1
    assert saved["entry"]["menus"][0]["price"] == 8000
    assert saved["entry"]["sources"] == payload()["sources"]
    assert len(saved["baseSnapshotHash"]) == 64
    assert client.get("/v1/meal/survey/p_0013").json()["draft"] == saved
    disk = json.loads((survey.ENTRIES_DIR / "p_0013.json").read_text(encoding="utf-8"))
    assert disk == saved
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.glob("*.json")} == before
    assert not recommendation_ready(next(p for p in places if p["id"] == "p_0013"))
    row = next(p for p in client.get("/v1/meal/survey").json()["places"] if p["id"] == "p_0013")
    assert row["entryStatus"] == "review"  # 사용자 확인이 끝나도 추천 준비와는 구분한다.


def test_unknown_fields_can_be_saved_partially_and_are_never_filled_in(client):
    response = client.post("/v1/meal/survey/p_0013", json={"expectedRevision": 0,
        "menus": [{"name": "가격 재확인 필요", "price": None}], "notes": "메뉴판을 다시 확인할 예정"})
    assert response.status_code == 200
    e = response.json()["entry"]
    assert e["menus"][0]["price"] is None and not e["menusConfirmed"]
    assert not e["hours"] and not e["hoursConfirmed"] and e["priceContext"] == "unknown"


def test_stale_revision_cannot_overwrite_a_newer_save(client):
    assert client.post("/v1/meal/survey/p_0013", json=payload()).status_code == 200
    assert client.post("/v1/meal/survey/p_0013", json=payload(notes="다른 창의 오래된 값")).status_code == 409
    assert client.get("/v1/meal/survey/p_0013").json()["draft"]["entry"]["notes"] == ""
    updated = client.post("/v1/meal/survey/p_0013", json=payload(expectedRevision=1, notes="추가 확인"))
    assert updated.status_code == 200 and updated.json()["revision"] == 2


@pytest.mark.parametrize("changes", [
    {"priceContext": "delivery"}, {"menus": [{"name": "정식", "price": None, "main": True}]},
    {"menus": [{"name": "음료", "price": 2000, "main": False}]},
    {"menus": [{"name": "정식", "price": True, "main": True}]},
    {"menus": [{"name": "정식", "price": 0, "main": True}]},
    {"sources": ["javascript:alert(1)"]}, {"sources": ["https://user:secret@example.com/menu"]},
    {"checkedAt": (datetime.now(SEOUL).date()+timedelta(days=1)).isoformat()},
    {"method": "unknown"}, {"method": "phone", "sources": []},
    {"sources": []}, {"unexpected": "ignored?"},
])
def test_invalid_or_unsupported_confirmations_do_not_create_any_entry(client, changes):
    assert client.post("/v1/meal/survey/p_0013", json=payload(**changes)).status_code == 422
    assert not survey.ENTRIES_DIR.exists()


def test_full_week_requires_explicit_closed_days_but_partial_hours_can_be_saved(client):
    value = {"expectedRevision": 0, "checkedAt": payload()["checkedAt"], "method": "phone",
             "notes": "전화로 평일 영업과 주말 휴무 확인", "hoursConfirmed": True,
             "hours": [{"days": list("월화수목금"), "open": "11:00", "close": "21:00"}]}
    assert client.post("/v1/meal/survey/p_0017", json=value).status_code == 422
    value["closedDays"] = ["토", "일"]
    response = client.post("/v1/meal/survey/p_0017", json=value)
    assert response.status_code == 200 and response.json()["entry"]["closedDays"] == ["토", "일"]
    value.update(expectedRevision=1, closedDays=["금", "토", "일"])
    assert client.post("/v1/meal/survey/p_0017", json=value).status_code == 422


def test_overnight_input_is_preserved_for_review_without_guessing_next_day_hours(client):
    value = {"expectedRevision": 0, "hours": [{"days": ["금"], "open": "17:00", "close": "02:00"}],
             "hoursNote": "새벽 마감은 다음 날임"}
    response = client.post("/v1/meal/survey/p_0017", json=value)
    assert response.status_code == 200
    assert response.json()["entry"]["hours"] == [{**value["hours"][0], "lastOrder": None, "breakStart": None, "breakEnd": None}]


def test_write_failure_keeps_previous_saved_content_and_returns_accurate_error(client, monkeypatch):
    assert client.post("/v1/meal/survey/p_0013", json=payload()).status_code == 200
    original = (survey.ENTRIES_DIR / "p_0013.json").read_bytes()
    def fail(*args): raise OSError("disk full")
    monkeypatch.setattr(survey, "_atomic_write", fail)
    result = client.post("/v1/meal/survey/p_0013", json=payload(expectedRevision=1))
    assert result.status_code == 503 and "저장하지 못했습니다" in result.json()["detail"]
    assert (survey.ENTRIES_DIR / "p_0013.json").read_bytes() == original
    assert not (survey.ENTRIES_DIR / ".save.lock").exists()


def test_unknown_archived_ids_and_cross_origin_writes_are_rejected(client):
    for pid in ("p_9999", "p_0040", "s_01", "p_0013.json"):
        assert client.post("/v1/meal/survey/"+pid, json=payload()).status_code == 404
    response = client.post("/v1/meal/survey/p_0013", json=payload(), headers={"Origin": "https://example.com"})
    assert response.status_code == 403
    assert not survey.ENTRIES_DIR.exists()


def test_variable_prices_stay_specific_and_whole_price_meals_leave_the_missing_queue():
    p = json.loads((DATA_DIR / "places/p_0100.json").read_text(encoding="utf-8"))
    assert survey.missing_info(p)[0]["label"] == "식사 메뉴의 실제 가격"
    pizza = json.loads((DATA_DIR / "places/p_0137.json").read_text(encoding="utf-8"))
    assert survey.missing_info(pizza) == []
