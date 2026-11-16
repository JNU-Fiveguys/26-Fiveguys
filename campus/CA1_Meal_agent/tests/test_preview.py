"""미리보기 화면의 계산 API — 화면이 받는 값이 recommend() 결과 그대로인지."""

import csv
import io
import json

from meal.places import SURVEY_FILL, SURVEY_FIXED
from meal.preview import run_recommend

C = (35.1765, 126.9055)


def setup(tmp_path):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=SURVEY_FIXED + SURVEY_FILL)
    w.writeheader()
    w.writerow({"id": "semas-1", "식당 이름": "가상김밥", "종류(추정)": "분식", "식사 유형(추정)": "분식·덮밥·패스트푸드",
                "가격대": "A", "혼밥(O/X)": "O", "영업시간": "월-금 10:00-20:00",
                "대표 메뉴와 가격": "김밥 3500 / 라면 4000"})
    survey = tmp_path / "survey.csv"
    survey.write_text(buf.getvalue(), encoding="utf-8-sig")
    cands = tmp_path / "c.json"
    cands.write_text(json.dumps({"places": [{"id": "semas-1", "lat": C[0] + 0.0015, "lng": C[1]}]}), encoding="utf-8")
    campus = tmp_path / "campus.json"
    campus.write_text(json.dumps({"name": "t", "radiusM": 1200, "center": list(C),
                                  "anchors": [{"name": "정문", "lat": C[0] - 0.003, "lng": C[1]}]}), encoding="utf-8")
    return survey, cands, campus


def test_run_recommend_shapes_cards(tmp_path):
    d = run_recommend({"start": "11:30", "minutes": "90", "weekday": "2"}, *setup(tmp_path))
    assert d["slot"] == {"start": "11:30", "end": "13:00", "minutes": 90, "weekday": 2, "from": "캠퍼스 중심", "to": "캠퍼스 중심"}
    assert d["points"] == ["캠퍼스 중심", "정문"]
    c = d["top"][0]
    assert (c["name"], c["walkToMin"], c["walkBackMin"], c["needMin"], c["priceLabel"]) == ("가상김밥", 4, 4, 30, "7천 원 이하")
    assert "혼밥 OK" in c["reasons"]
    assert c["menu"] == [{"name": "김밥", "price": 3500, "note": None}, {"name": "라면", "price": 4000, "note": None}]
    assert d["data"] == {"ready": 1, "blank": 0, "problems": 0}
    # 미리보기 지도용 좌표 — 식당 위치와 출발·다음 건물 핀
    assert (c["lat"], c["lng"]) == (C[0] + 0.0015, C[1])
    assert d["pins"]["from"] == {"name": "캠퍼스 중심", "lat": C[0], "lng": C[1]} and d["pins"]["to"]["name"] == "캠퍼스 중심"


def test_run_recommend_from_gate_and_no_return(tmp_path):
    d = run_recommend({"start": "11:30", "minutes": "90", "weekday": "2", "from": "정문", "to": "없음"}, *setup(tmp_path))
    c = d["top"][0]
    assert c["walkToMin"] == 10 and c["walkBackMin"] is None   # 정문에서 약 500m → ⌈500 × 1.3 ÷ 70⌉
    assert d["pins"]["from"]["name"] == "정문" and d["pins"]["to"] is None


def test_run_recommend_closed_day_explains(tmp_path):
    d = run_recommend({"start": "11:30", "minutes": "90", "weekday": "6"}, *setup(tmp_path))
    assert d["top"] == [] and d["excluded"] == {"영업 아님": 1}


def test_parse_menu_formats():
    from meal.preview import parse_menu

    assert parse_menu("오리탕 9000 / 삼겹살 13000 (점심특선)") == [
        {"name": "오리탕", "price": 9000, "note": None}, {"name": "삼겹살", "price": 13000, "note": "점심특선"}]
    assert parse_menu("빅맥 세트 / 불고기 버거 세트 (가격 변동)")[1] == {"name": "불고기 버거 세트", "price": None, "note": "가격 변동"}
    assert parse_menu("마라탕 1인 11900")[0] == {"name": "마라탕 1인", "price": 11900, "note": None}
    assert parse_menu("치즈 피자(L) 15000")[0] == {"name": "치즈 피자(L)", "price": 15000, "note": None}


def test_run_recommend_with_buildings_and_paths(tmp_path):
    from meal.walkgraph import WalkGraph

    # 캠퍼스 중심 → 식당으로 남북 길 하나, 건물 '공대' 는 그 길 위
    graph = WalkGraph.from_overpass({"elements": [{"type": "way", "id": 1, "tags": {"highway": "footway"},
        "nodes": [1, 2, 3], "geometry": [{"lat": C[0] - 0.001, "lon": C[1]}, {"lat": C[0], "lon": C[1]},
                                         {"lat": C[0] + 0.0015, "lon": C[1]}]}]})
    d = run_recommend({"start": "11:30", "minutes": "90", "weekday": "2", "from": "공대", "to": "없음"},
                      *setup(tmp_path), graph=graph, buildings={"공대": (C[0] - 0.001, C[1])})
    assert d["buildings"] == ["공대"] and d["walk"] == "path" and d["pins"]["from"]["name"] == "공대"
    c = d["top"][0]
    assert c["walkToMin"] == 4                      # 길 약 278m ÷ 70 → 4분(우회 계수 없음)
    assert c["pathTo"][0] == [C[0] - 0.001, C[1]] and c["pathTo"][-1] == [C[0] + 0.0015, C[1]]
    assert c["pathBack"] is None                    # 다음 수업 없음



def test_page_has_only_clean_map():
    from meal.preview import PAGE

    assert "const CLEAN={clean};" in PAGE and "tileLayer" not in PAGE and "control.layers" not in PAGE


def test_categories_filter(tmp_path):
    survey, cands, campus = setup(tmp_path)
    d = run_recommend({"start": "11:30", "minutes": "90", "weekday": "2"}, survey, cands, campus)
    assert d["categories"] == ["분식"]
    d = run_recommend({"start": "11:30", "minutes": "90", "weekday": "2", "cats": "한식,일식"}, survey, cands, campus)
    assert d["top"] == []                                   # 고른 종류가 아니면 빠진다
    d = run_recommend({"start": "11:30", "minutes": "90", "weekday": "2", "cats": "한식,분식"}, survey, cands, campus)
    assert d["top"][0]["name"] == "가상김밥"                 # 여러 개 중 하나만 맞아도 된다


def test_share_mode_does_not_read_timetable(tmp_path, monkeypatch):
    import meal.preview as P

    monkeypatch.setattr(P, "f3_classes", lambda day: [{"name": "비밀 수업", "start": "09:00", "end": "10:00", "room": "", "state": "scheduled"}])
    survey, cands, campus = setup(tmp_path)
    assert P.MealApp(survey, cands, campus).schedule({})["source"] == "f3"
    shared = P.MealApp(survey, cands, campus, timetable=False).schedule({})
    assert shared["source"] == "none" and shared["classes"] == []      # --share: 내 시간표를 내보내지 않는다


def test_browse_places_and_detail(tmp_path):
    from meal.preview import MealApp

    survey, cands, campus = setup(tmp_path)
    app = MealApp(survey, cands, campus)
    d = app.places({"q": "김밥"})
    assert d["total"] == 1 and d["categories"] == ["분식"]
    item = d["items"][0]
    assert (item["name"], item["priceLabel"], item["hours"]) == ("가상김밥", "7천 원 이하", "월-금 10:00-20:00")
    assert len(item["menu"]) == 2 and app.places({"q": "없는가게"})["total"] == 0
    assert app.places({"cat": "한식"})["total"] == 0
    detail, code = app.place("semas-1")
    assert code == 200 and detail["menu"][0] == {"name": "김밥", "price": 3500, "note": None} and "lat" in detail
    assert app.place("nope")[1] == 404
