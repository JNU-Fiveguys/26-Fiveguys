"""채운 조사 양식 읽기 — 사람이 적는 여러 표기를 받아들이고, 잘못 적은 줄은 줄 번호와 함께 알려 준다."""

import csv
import io
import json

import pytest

from meal.places import SURVEY_FILL, SURVEY_FIXED
from meal.ranking import MealRequest, recommend
from meal.survey import (
    SurveyError,
    apply_breaks,
    load_survey,
    main,
    parse_days,
    parse_meal_type,
    parse_schedule,
    parse_yes_no,
)
from meal.timing import MealType

H = lambda h, m=0: h * 60 + m
O = (35.1765, 126.9055)
COORDS = {"semas-1": (35.1780, 126.9055), "semas-2": (35.1790, 126.9055), "semas-3": (35.1800, 126.9055)}


def survey_text(*rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=SURVEY_FIXED + SURVEY_FILL)
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def filled(pid="semas-1", name="가상김밥", **kw):
    r = {"id": pid, "식당 이름": name, "종류(추정)": "분식", "식사 유형(추정)": "분식·덮밥·패스트푸드",
         "가격대": "A", "혼밥(O/X)": "O", "좌석(S/M/L)": "S", "영업시간": "월-금 10:00-20:00 / 토 11:00-15:00"}
    r.update(kw)
    return r


# --- 요일 · 시각 --------------------------------------------------------------

@pytest.mark.parametrize(
    "text, days",
    [
        ("월-금", {0, 1, 2, 3, 4}), ("월~금", {0, 1, 2, 3, 4}), ("월요일-금요일", {0, 1, 2, 3, 4}),
        ("월,수,금", {0, 2, 4}), ("토", {5}), ("평일", {0, 1, 2, 3, 4}), ("주말", {5, 6}),
        ("매일", set(range(7))), ("", set(range(7))), ("금-월", {4, 5, 6, 0}),
    ],
)
def test_parse_days(text, days):
    assert parse_days(text) == days


def test_parse_days_rejects():
    with pytest.raises(SurveyError):
        parse_days("월화")


def test_parse_schedule_formats():
    s = parse_schedule("월-금 11:00-21:00 / 토 11:00~15:00")
    assert s[0] == [(H(11), H(21))] and s[5] == [(H(11), H(15))] and 6 not in s
    assert parse_schedule("1030-2200") == {d: [(H(10, 30), H(22))] for d in range(7)}
    assert parse_schedule("매일 17:00-02:00")[3] == [(H(17), 1440)]      # 새벽 영업은 자정에서 끊는다
    assert parse_schedule("평일 11:00-14:00 17:00-21:00")[1] == [(H(11), H(14)), (H(17), H(21))]


def test_parse_schedule_rejects():
    with pytest.raises(SurveyError):
        parse_schedule("점심만")


def test_parse_last_order():
    from meal.survey import parse_last_order

    assert parse_last_order("13:50") == {d: [H(13, 50)] for d in range(7)}
    lo = parse_last_order("월-금 14:30 20:00 / 토 22:40")
    assert lo[0] == [H(14, 30), H(20)] and lo[5] == [H(22, 40)] and 6 not in lo
    assert parse_last_order("00:40")[0] == [40]
    with pytest.raises(SurveyError):
        parse_last_order("마감 전")


def test_last_order_column_reaches_place():
    places, problems, _ = load_survey(survey_text(filled(라스트오더="월-금 19:30")), COORDS)
    assert not problems and places[0].last_order[2] == [H(19, 30)]


def test_hours_unknown_row():
    text = survey_text(filled(영업시간="영업시간 확인 필요", 휴무="일"), filled("semas-2", "빈 시간", 영업시간=""))
    places, problems, _ = load_survey(text, COORDS)
    assert [p.name for p in places] == ["가상김밥"] and places[0].hours_unknown
    assert places[0].hours[0] == [(0, 1440)] and 6 not in places[0].hours      # 휴무는 그대로 뺀다
    assert [pr.name for pr in problems] == ["빈 시간"] and "영업시간 확인 필요" in problems[0].message


def test_breaks_cut_hours():
    hours = parse_schedule("매일 11:00-21:00")
    out = apply_breaks(hours, parse_schedule("15:00-17:00"))
    assert out[0] == [(H(11), H(15)), (H(17), H(21))]
    out = apply_breaks(hours, parse_schedule("토 14:00-21:00"))
    assert out[5] == [(H(11), H(14))] and out[0] == [(H(11), H(21))]


@pytest.mark.parametrize("text, v", [("O", True), ("o", True), ("가능", True), ("X", False), ("불가", False), ("", None)])
def test_yes_no(text, v):
    assert parse_yes_no(text) is v


def test_meal_type_ko_and_en():
    assert parse_meal_type("일반 식당") is MealType.REGULAR
    assert parse_meal_type("고기") is MealType.GROUP
    assert parse_meal_type("quick") is MealType.QUICK
    assert parse_meal_type("") is None


# --- 줄 단위 ------------------------------------------------------------------

def test_load_survey_mixed_rows():
    text = survey_text(
        filled(),
        filled("semas-2", "가상고기", **{"종류(확인)": "고기", "식사 유형(확인)": "고기·단체", "가격대": "c",
                                     "영업시간": "매일 11:30-22:00", "브레이크": "15:00-17:00", "휴무": "일"}),
        {"id": "semas-3", "식당 이름": "안 간 곳"},                                   # 아직 조사 안 함
        filled("semas-3", "닫은 곳", **{"영업 상태": "폐업"}),                          # 폐업 → 조용히 뺀다
        filled("semas-9", "목록에 없음"),
        filled("semas-1", "가격 틀림", 가격대="7천"),
        filled("semas-1", "시각 틀림", 영업시간="점심만"),
    )
    places, problems, blank = load_survey(text, COORDS)

    assert [p.name for p in places] == ["가상김밥", "가상고기"]
    assert blank == 2
    assert [(pr.line, pr.name) for pr in problems] == [(6, "목록에 없음"), (7, "가격 틀림"), (8, "시각 틀림")]

    kim, meat = places
    assert (kim.category, kim.meal_type, kim.price_band, kim.solo_ok, kim.seats) == ("분식", MealType.QUICK, "A", True, "S")
    assert kim.lat == COORDS["semas-1"][0]
    assert 6 not in kim.hours                                  # 일요일 영업 안 적음
    assert (meat.category, meat.meal_type, meat.price_band) == ("고기", MealType.GROUP, "C")
    assert meat.hours[0] == [(H(11, 30), H(15)), (H(17), H(22))]
    assert 6 not in meat.hours                                 # 휴무 일


def test_count_closed_separately():
    from meal.survey import count_closed

    text = survey_text(filled(), filled("semas-2", "닫음", **{"영업 상태": "폐업"}),
                       {"id": "semas-3", "식당 이름": "못찾", "영업 상태": "못 찾음"}, {"id": "semas-3", "식당 이름": "빈칸"},
                       filled("semas-1", "뺌", **{"영업 상태": "제외"}))
    assert count_closed(text) == 3
    places, problems, _ = load_survey(text, COORDS)
    assert "뺌" not in [p.name for p in places] and not problems


def test_cp949_saved_by_excel(tmp_path):
    from meal.places import read_text

    p = tmp_path / "survey.csv"
    p.write_bytes(survey_text(filled()).encode("cp949"))
    places, problems, _ = load_survey(read_text(p), COORDS)
    assert [x.name for x in places] == ["가상김밥"] and not problems


def test_loaded_places_feed_recommend():
    places, _, _ = load_survey(survey_text(filled(), filled("semas-2", "브레이크집", 영업시간="매일 11:00-15:00 / 매일 17:00-21:00")), COORDS)
    r = recommend(places, MealRequest(slot_start=H(15, 30), slot_min=90, weekday=2, origin=O, dest=O))
    assert [c.name for c in r.top] == ["가상김밥"]               # 브레이크집은 15:30 에 빠진다
    assert r.excluded == {"영업 아님": 1}


# --- CLI ----------------------------------------------------------------------

def test_main_check_and_try(tmp_path, capsys):
    survey = tmp_path / "survey.csv"
    survey.write_text(survey_text(filled(), filled("semas-1", "틀린 줄", 가격대="Z")), encoding="utf-8-sig")
    cands = tmp_path / "c.json"
    cands.write_text(json.dumps({"places": [{"id": k, "lat": v[0], "lng": v[1]} for k, v in COORDS.items()]}), encoding="utf-8")
    campus = tmp_path / "campus.json"
    campus.write_text(json.dumps({"name": "t", "radiusM": 1200, "anchors": [{"name": "중심", "lat": O[0], "lng": O[1]}]}), encoding="utf-8")

    code = main([str(survey), "--candidates", str(cands), "--campus", str(campus), "--try", "11:30", "90", "--weekday", "2"])
    out = capsys.readouterr().out
    assert code == 1                                            # 고칠 줄이 있으면 1
    assert "추천 가능 1곳 · 폐업·못 찾음·제외 0곳 · 아직 조사 안 함 0곳" in out and "3번째 줄 틀린 줄" in out
    assert "[추천] 가상김밥 (분식)" in out and "왕복 포함" in out
