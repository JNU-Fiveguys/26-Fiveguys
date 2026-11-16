"""상가정보 CSV → 후보 · 조사 양식. 가짜 가게로만 시험한다(실제 원천 파일은 저장소에 넣지 않는다)."""

import csv
import json

import pytest

from meal.places import (
    Anchor,
    Campus,
    build_candidates,
    classify,
    main,
    read_rows,
    read_text,
    suspect_closed,
)
from meal.timing import MealType

CENTER = (35.1765, 126.9055)
# 위도 0.001도 ≈ 111m
NEAR = (35.1785, 126.9055)    # 약 222m
EDGE = (35.1855, 126.9055)    # 약 1000m
FAR = (35.2000, 126.9055)     # 약 2.6km

HEADER = ["상가업소번호", "상호명", "지점명", "상권업종대분류명", "상권업종중분류명", "상권업종소분류명", "지번주소", "도로명주소", "층정보", "경도", "위도"]


def row(id_, name, large, middle, small, at, branch="", floor="1"):
    return [id_, name, branch, large, middle, small, "광주 북구 용봉동 1", "광주광역시 북구 가상로 1", floor, str(at[1]), str(at[0])]


ROWS = [
    row("A1", "가상김밥", "음식", "기타 간이", "김밥/만두/분식", NEAR),
    row("A2", "가상고기", "음식", "한식", "돼지고기 구이/찜", EDGE),
    row("A3", "가상카페", "음식", "비알코올", "카페", NEAR, branch="전남대점"),
    row("A4", "가상포차", "음식", "주점", "요리 주점", NEAR),           # 주점 → 뺀다
    row("A5", "가상세탁", "수리·개인", "세탁", "세탁소", NEAR),         # 음식 아님 → 뺀다
    row("A6", "먼식당", "음식", "한식", "백반/한정식", FAR),            # 반경 밖 → 뺀다
    row("A7", "가상국밥", "음식", "한식", "국/탕/찌개류", EDGE),
    row("A8", "가상스시", "음식", "일식", "일식 회/초밥", NEAR),
    row("A9", "가상떡볶이", "음식", "기타 간이", "떡볶이", NEAR),
]


@pytest.fixture
def campus():
    return Campus("테스트 캠퍼스", (Anchor("중심", *CENTER),), 1200)


def to_csv(rows, header=HEADER, delim=","):
    lines = [delim.join(header)] + [delim.join(r) for r in rows]
    return "\n".join(lines) + "\n"


# --- 분류 ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "large, middle, small, expected",
    [
        ("음식", "기타 간이", "김밥/만두/분식", ("분식", MealType.QUICK)),
        ("음식", "기타 간이", "떡볶이", ("분식", MealType.QUICK)),          # '떡' 이 카페로 가면 안 된다
        ("음식", "한식", "돼지고기 구이/찜", ("고기", MealType.GROUP)),     # 소분류 우선
        ("음식", "한식", "국/탕/찌개류", ("국밥·찌개", MealType.REGULAR)),
        ("음식", "한식", "백반/한정식", ("한식", MealType.QUICK)),
        ("음식", "비알코올", "카페", ("카페·베이커리", MealType.CAFE)),
        ("음식", "기타 간이", "버거", ("패스트푸드", MealType.QUICK)),
        ("음식", "기타 간이", "치킨", ("패스트푸드", MealType.REGULAR)),
        ("음식", "중식", "중국집", ("중식", MealType.REGULAR)),
        ("음식", "서양식", "파스타/스테이크", ("양식", MealType.REGULAR)),
        ("음식", "동남아시아", "베트남식 전문", ("아시안", MealType.REGULAR)),
        # 2026-09 광주 실제 원천에서 잘못 붙던 업종들
        ("음식", "중식", "마라탕/훠궈", ("중식", MealType.REGULAR)),
        ("음식", "일식", "일식 카레/돈가스/덮밥", ("일식", MealType.QUICK)),
        ("음식", "한식", "족발/보쌈", ("고기", MealType.GROUP)),
        ("음식", "한식", "횟집", ("한식", MealType.GROUP)),
        ("음식", "기타 간이", "떡/한과", ("카페·베이커리", MealType.CAFE)),
        ("음식", "한식", "국수/칼국수", ("한식", MealType.QUICK)),
        ("음식", "기타", "무언가", ("미분류", MealType.REGULAR)),
        ("음식", "주점", "요리 주점", None),
        ("음식", "구내식당·뷔페", "구내식당", None),
        ("소매", "식료품", "슈퍼마켓", None),
    ],
)
def test_classify(large, middle, small, expected):
    assert classify(large, middle, small) == expected


# --- 후보 ---------------------------------------------------------------------

def test_build_candidates_filters_and_sorts(campus):
    places = build_candidates(read_rows(to_csv(ROWS)), campus)
    names = [p.name for p in places]
    # 주점 · 세탁소 · 반경 밖은 빠지고, 가까운 순(같으면 이름순)
    assert "가상포차" not in names and "가상세탁" not in names and "먼식당" not in names
    assert [p.distanceM <= 1200 for p in places] == [True] * len(places)
    assert [p.distanceM for p in places] == sorted(p.distanceM for p in places)
    cafe = next(p for p in places if p.sourceId == "A3")
    assert cafe.name == "가상카페 전남대점"
    assert cafe.id == "semas-A3"
    assert cafe.zone == "중심"
    assert cafe.address == "광주광역시 북구 가상로 1"
    assert cafe.rawCategory == "비알코올 > 카페"
    assert 215 <= cafe.distanceM <= 230
    assert len(places) == 6


def test_nearest_anchor_wins():
    c = Campus("c", (Anchor("정문", *CENTER), Anchor("후문", *EDGE)), 1200)
    places = build_candidates(read_rows(to_csv([row("B1", "후문식당", "음식", "한식", "백반/한정식", EDGE)])), c)
    assert (places[0].zone, places[0].distanceM) == ("후문", 0)


def test_duplicate_ids_kept_once(campus):
    places = build_candidates(read_rows(to_csv([ROWS[0], ROWS[0]])), campus)
    assert len(places) == 1


def test_bad_coordinates_skipped(campus):
    bad = row("C1", "좌표없음", "음식", "한식", "백반/한정식", ("", ""))
    assert build_candidates(read_rows(to_csv([bad])), campus) == []


def test_pipe_delimited_and_cp949(tmp_path, campus):
    p = tmp_path / "old.csv"
    p.write_bytes(to_csv(ROWS[:1], delim="|").encode("cp949"))
    places = build_candidates(read_rows(read_text(p)), campus)
    assert [x.name for x in places] == ["가상김밥"]


def test_missing_required_column_is_clear():
    with pytest.raises(ValueError, match="위도"):
        list(read_rows("상가업소번호,상호명,상권업종대분류명,경도\n1,a,음식,126.9\n"))


# --- 폐업 의심 ---------------------------------------------------------------

def test_suspect_closed(campus):
    now = build_candidates(read_rows(to_csv(ROWS)), campus)
    prev = [{"id": "semas-A1", "name": "가상김밥"}, {"id": "semas-Z9", "name": "사라진집"}]
    assert suspect_closed(prev, now) == [{"id": "semas-Z9", "name": "사라진집"}]


# --- CLI 전체 ----------------------------------------------------------------

def test_main_writes_bundle_survey_and_diff(tmp_path, capsys):
    src = tmp_path / "gwangju.csv"
    src.write_text(to_csv(ROWS), encoding="utf-8")
    campus_file = tmp_path / "campus.json"
    campus_file.write_text(
        json.dumps({"name": "테스트", "radiusM": 1200, "anchors": [{"name": "중심", "lat": CENTER[0], "lng": CENTER[1]}]}),
        encoding="utf-8",
    )
    prev = tmp_path / "prev.json"
    prev.write_text(json.dumps({"places": [{"id": "semas-Z9", "name": "사라진집"}]}), encoding="utf-8")
    out = tmp_path / "out"

    assert main([str(src), "--campus", str(campus_file), "--out", str(out), "--prev", str(prev), "--survey-within", "0", "--survey-cafe"]) == 0

    bundle = json.loads((out / "places.candidates.json").read_text(encoding="utf-8"))
    assert bundle["count"] == 6 and bundle["campus"] == "테스트"
    assert {"id", "name", "category", "mealTypeGuess", "lat", "lng", "zone", "distanceM", "address", "source", "sourceId"} <= set(bundle["places"][0])

    raw = (out / "survey.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # 엑셀이 한글을 깨뜨리지 않게 BOM
    rows = list(csv.reader(raw.decode("utf-8-sig").splitlines()))
    assert rows[0][:2] == ["id", "식당 이름"] and "가격대" in rows[0] and "혼밥(O/X)" in rows[0]
    assert len(rows) == 1 + 6
    assert rows[1][-1] == ""  # 채울 칸은 비어 있다

    gone = json.loads((out / "closed.suspect.json").read_text(encoding="utf-8"))
    assert [g["id"] for g in gone] == ["semas-Z9"]
    assert "후보 6곳" in capsys.readouterr().out


def test_survey_defaults_near_and_no_cafe(tmp_path):
    src = tmp_path / "gwangju.csv"
    src.write_text(to_csv(ROWS), encoding="utf-8")
    campus_file = tmp_path / "campus.json"
    campus_file.write_text(
        json.dumps({"name": "t", "radiusM": 1200, "anchors": [{"name": "중심", "lat": CENTER[0], "lng": CENTER[1]}]}),
        encoding="utf-8",
    )
    out = tmp_path / "out"
    assert main([str(src), "--campus", str(campus_file), "--out", str(out), "--survey-within", "500"]) == 0
    rows = list(csv.reader((out / "survey.csv").read_text(encoding="utf-8-sig").splitlines()))[1:]
    # 500m 안(NEAR 약 222m) · 카페 제외 → 김밥 · 스시 · 떡볶이. EDGE(약 1km) 의 고기·국밥은 빠진다
    assert sorted(r[1] for r in rows) == ["가상김밥", "가상떡볶이", "가상스시"]
    # 후보 목록은 그대로 전부
    assert json.loads((out / "places.candidates.json").read_text(encoding="utf-8"))["count"] == 6


def test_rerun_keeps_filled_rows(tmp_path):
    src = tmp_path / "gwangju.csv"
    src.write_text(to_csv(ROWS), encoding="utf-8")
    campus_file = tmp_path / "campus.json"
    campus_file.write_text(
        json.dumps({"name": "t", "radiusM": 1200, "anchors": [{"name": "중심", "lat": CENTER[0], "lng": CENTER[1]}]}),
        encoding="utf-8",
    )
    out = tmp_path / "out"
    args = [str(src), "--campus", str(campus_file), "--out", str(out), "--survey-within", "500"]
    assert main(args) == 0

    # 김밥집(범위 안)과 사라질 가게 줄을 채운다
    path = out / "survey.csv"
    rows = list(csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()))
    header = list(rows[0].keys())
    for r in rows:
        if r["id"] == "semas-A1":
            r["가격대"], r["메모"] = "A", "채움"
    rows.append({**{k: "" for k in header}, "id": "semas-GONE", "식당 이름": "사라진집", "가격대": "B"})
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(rows)

    # 범위를 좁혀(김밥집 222m 는 범위 밖) 다시 만들어도 채운 줄은 남는다
    assert main(args[:-1] + ["100"]) == 0
    again = {r["id"]: r for r in csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines())}
    assert again["semas-A1"]["가격대"] == "A" and again["semas-A1"]["메모"] == "채움"
    assert again["semas-GONE"]["식당 이름"] == "사라진집" and again["semas-GONE"]["가격대"] == "B"
    assert "semas-A8" not in again                  # 안 채운 범위 밖 줄은 빠진다


def test_default_campus_file_loads():
    from meal.places import DEFAULT_CAMPUS

    c = Campus.load(DEFAULT_CAMPUS)
    assert c.radius_m == 1200 and len(c.boundary) > 100
    # 캠퍼스 중심은 경계 안 · 서문은 경계 위
    assert c.locate(*c.center) == ("캠퍼스 안", 0.0)
    zone, d = c.locate(35.1722459, 126.8979984)
    assert d < 1


# --- 경계 기준 거리 (정의서 D1 '캠퍼스 경계에서') -------------------------------

# 한 변 약 222m 정사각형 캠퍼스 (위도 ±0.001 · 경도 ±0.00122)
SQ = ((35.1755, 126.90428), (35.1755, 126.90672), (35.1775, 126.90672), (35.1775, 126.90428))


def boxed(**kw):
    return Campus("네모", kw.pop("anchors", ()), kw.pop("radius", 1200), SQ, center_point=CENTER, **kw)


def test_boundary_inside_is_zero():
    assert boxed().locate(*CENTER) == ("캠퍼스 안", 0.0)


def test_boundary_distance_from_edge_not_center():
    zone, d = boxed().locate(35.1795, 126.9055)          # 북쪽 변(35.1775)에서 0.002도 ≈ 221m
    assert zone == "캠퍼스 북쪽" and d == pytest.approx(221, abs=2)
    zone, d = boxed().locate(35.1795, 126.9090)          # 북동쪽 모서리 바깥
    assert zone == "캠퍼스 북동쪽"
    assert boxed().locate(35.1765, 126.9000)[0] == "캠퍼스 서쪽"
    assert boxed().locate(35.1740, 126.9055)[0] == "캠퍼스 남쪽"


def test_boundary_zone_uses_gate_when_close():
    gate = Anchor("북문", 35.1775, 126.9055)
    c = boxed(anchors=(gate,))
    assert c.locate(35.1795, 126.9055)[0] == "북문 쪽"
    assert c.locate(35.1765, 126.8950)[0] == "캠퍼스 서쪽"   # 문에서 멀면 방향


def test_boundary_radius_filters_from_edge():
    # 중심에서 1.3km 지만 경계에서는 약 1.19km → 들어온다
    c = boxed()
    row_ok = row("D1", "경계 근처", "음식", "한식", "백반/한정식", (35.1765 + 0.0117, 126.9055))
    row_far = row("D2", "멀리", "음식", "한식", "백반/한정식", (35.1765 + 0.0130, 126.9055))
    names = [p.name for p in build_candidates(read_rows(to_csv([row_ok, row_far])), c)]
    assert names == ["경계 근처"]


def test_campus_file_needs_anchor_or_boundary(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"name": "x", "radiusM": 1}), encoding="utf-8")
    with pytest.raises(ValueError):
        Campus.load(p)
