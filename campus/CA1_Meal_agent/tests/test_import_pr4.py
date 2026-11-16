"""김종연 CA1 식당 합치기 — 내 조사값이 먼저 · 빈 줄만 채움 · 없는 가게는 새 줄."""

import csv
import io
import json

from meal.import_pr4 import fill, hours_text, main, plan, price_band, same_place
from meal.places import SURVEY_FILL, SURVEY_FIXED
from meal.survey import load_coords, load_survey

XY = (35.1784, 126.9123)


def pr4(pid, name, lat=XY[0], lng=XY[1], **kw):
    return {"id": pid, "name": name, "category": "분식", "lat": lat, "lng": lng, "address": "광주 북구",
            "menus": [{"name": "김밥", "price": 4000, "main": True}, {"name": "콜라", "price": 2000, "main": False}],
            "repPrice": 4000, "hours": [{"days": ["월", "화", "수", "목", "금"], "open": "10:00", "close": "20:00",
                                         "break": {"start": "15:00", "end": "16:00"}, "lastOrder": "19:30"}],
            "closedDates": [], "monthlyClosed": [], "menuSource": {"url": "https://example/m"}, "hoursSource": {"url": None}, **kw}


def test_same_place_rules():
    assert same_place("광주전남대써브웨이 후문점", XY, pr4("p_1", "써브웨이 광주전남대후문점"))
    assert same_place("백련옥", XY, pr4("p_2", "백년옥"))                      # 거의 같은 자리 · 한 글자 차이
    assert same_place("나름", XY, pr4("p_3", "엄마손맛나름"))
    assert not same_place("오모야", XY, pr4("p_4", "그여자의 밥상"))           # 같은 건물 다른 가게
    assert not same_place("백련옥", XY, pr4("p_5", "백년옥", lat=XY[0] + 0.01))  # 1km 떨어지면 다른 가게


def test_hours_and_band_text():
    assert hours_text(pr4("p", "x")["hours"]) == ("월-금 10:00-20:00", "월-금 15:00-16:00", "월-금 19:30", "토,일")
    from meal.import_pr4 import _days
    assert [_days(d) for d in (list("월화수목금토일"), ["월", "수", "금"], ["토", "일"], ["월", "화", "수", "금"])] == [
        "매일", "월,수,금", "토,일", "월-수,금"]
    assert [price_band(p) for p in (7000, 7001, 10000, 15000, 15001, None)] == ["A", "B", "B", "C", "D", ""]
    f = fill(pr4("p", "x", closedDates=["2026-11-19"], monthlyClosed=[{"weeks": [2, 4], "day": "월"}]))
    assert f["대표 메뉴와 가격"] == "김밥 4000" and f["확인자"] == "김종연(pr4)"
    assert "날짜 휴무 2026-11-19" in f["메모"] and "매달 2·4번째 월요일 휴무" in f["메모"]


def test_import_keeps_mine_fills_blank_adds_new(tmp_path):
    fields = SURVEY_FIXED + SURVEY_FILL
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    w.writerow({"id": "s-1", "식당 이름": "가상김밥", "가격대": "B", "영업시간": "매일 09:00-21:00"})      # 내가 채움 → 유지
    w.writerow({"id": "s-2", "식당 이름": "빈칸분식"})                                                # 비어 있음 → 채움
    w.writerow({"id": "s-3", "식당 이름": "닫은집", "영업 상태": "제외"})                             # 내가 뺌 → 유지
    survey = tmp_path / "survey.csv"
    survey.write_text(buf.getvalue(), encoding="utf-8-sig")
    cands = tmp_path / "places.candidates.json"
    cands.write_text(json.dumps({"places": [{"id": f"s-{i}", "lat": XY[0] + i * 0.002, "lng": XY[1]} for i in (1, 2, 3)]}), encoding="utf-8")
    export = tmp_path / "pr4.json"
    export.write_text(json.dumps({"places": [pr4("p_1", "가상김밥", lat=XY[0] + 0.002), pr4("p_2", "빈칸분식", lat=XY[0] + 0.004),
                                             pr4("p_3", "닫은집", lat=XY[0] + 0.006), pr4("p_9", "새분식", lat=XY[0] + 0.02)]},
                                 ensure_ascii=False), encoding="utf-8")
    assert main([str(export), "--survey", str(survey), "--candidates", str(cands), "--apply"]) == 0
    rows = {r["id"]: r for r in csv.DictReader(survey.read_text(encoding="utf-8-sig").splitlines())}
    assert rows["s-1"]["가격대"] == "B" and rows["s-1"]["확인자"] == ""          # 내 값 그대로
    assert rows["s-2"]["가격대"] == "A" and rows["s-2"]["확인자"] == "김종연(pr4)"
    assert rows["s-3"]["영업 상태"] == "제외"
    assert rows["pr4-p_9"]["식당 이름"] == "새분식"
    coords = load_coords(cands)                                                   # 새 가게 좌표는 places.extra.json
    assert "pr4-p_9" in coords
    places, problems, _ = load_survey(survey.read_text(encoding="utf-8-sig"), coords)
    assert not problems and {p.id for p in places} == {"s-1", "s-2", "pr4-p_9"}
    assert (tmp_path / "survey.before_pr4.csv").exists()
    assert plan(list(rows.values()), coords, [pr4("p_9", "새분식", lat=XY[0] + 0.02)])["keep"]   # 다시 돌려도 중복 추가 안 함
