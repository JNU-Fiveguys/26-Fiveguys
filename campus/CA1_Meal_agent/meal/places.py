"""상가(상권)정보 → 캠퍼스 주변 식당 후보 목록 + 현장 조사 양식 (정의서 CA1 D2 · CA1-R10 · 7절).

원천: 공공데이터포털 '소상공인시장진흥공단_상가(상권)정보' 시도별 CSV (광주광역시 파일).
사람이 내려받은 파일을 읽기만 한다 — 이 스크립트는 네트워크에 접속하지 않는다.

    python -m meal.places <상가정보.csv> [--campus directory/campus.json] [--out data] [--prev data/places.candidates.json]

만드는 것 (--out 폴더)
- places.candidates.json : 기준점 반경 안 음식점 → `Place` 후보(종류·식사 유형은 업종에서 추정, 조사 때 확인)
- survey.csv             : 현장 조사 양식(가까운 순). 엑셀에서 바로 열린다(UTF-8 BOM)
- closed.suspect.json    : --prev 를 주면, 지난 목록에 있었는데 이번에 사라진 곳 = 폐업 의심 (CA1-R10)
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

from .timing import MealType, distance_m

AGENT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CAMPUS = AGENT_DIR / "directory" / "campus.json"


# ---------------------------------------------------------------------------
# 캠퍼스 기준점
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Anchor:
    name: str
    lat: float
    lng: float


_DIRECTIONS = ("동쪽", "북동쪽", "북쪽", "북서쪽", "서쪽", "남서쪽", "남쪽", "남동쪽")


def _xy(lat: float, lng: float, lat0: float, lng0: float) -> tuple[float, float]:
    """(lat0, lng0) 기준 평면 좌표(m). 캠퍼스 몇 km 범위에서는 오차가 1% 안쪽이다."""
    return (lng - lng0) * 111_320 * math.cos(math.radians(lat0)), (lat - lat0) * 110_574


def _seg_dist(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def _inside(p: tuple[float, float], poly: Sequence[tuple[float, float]]) -> bool:
    x, y = p
    hit = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


@dataclass(frozen=True)
class Campus:
    """후보를 고르는 기준. `boundary`(캠퍼스 경계 다각형)가 있으면 **경계에서** 잰다(정의서 D1).

    없으면 `anchors`(기준점) 중 가장 가까운 점에서 잰다.
    구역 이름은 `gate_zone_m` 안에 있는 가장 가까운 문(anchors), 없으면 캠퍼스 중심에서 본 방향.
    """

    name: str
    anchors: tuple[Anchor, ...]
    radius_m: float
    boundary: tuple[tuple[float, float], ...] = ()   # (lat, lng)
    gate_zone_m: float = 400
    center_point: tuple[float, float] | None = None   # 방향 이름 · --try 출발점. 없으면 경계 꼭짓점 평균

    @classmethod
    def load(cls, path: Path) -> "Campus":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        anchors = tuple(Anchor(a["name"], float(a["lat"]), float(a["lng"])) for a in raw.get("anchors", []))
        boundary = tuple((float(a), float(b)) for a, b in raw.get("boundary", []))
        if not anchors and len(boundary) < 3:
            raise ValueError("기준점(anchors) 또는 경계(boundary)가 있어야 한다")
        center = tuple(map(float, raw["center"])) if raw.get("center") else None
        return cls(raw["name"], anchors, float(raw["radiusM"]), boundary, float(raw.get("gateZoneM", 400)), center)

    def nearest(self, lat: float, lng: float) -> tuple[Anchor, float]:
        return min(((a, distance_m(a.lat, a.lng, lat, lng)) for a in self.anchors), key=lambda x: x[1])

    @property
    def center(self) -> tuple[float, float]:
        if self.center_point:
            return self.center_point
        if self.boundary:
            pts = self.boundary
            return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        return self.anchors[0].lat, self.anchors[0].lng

    def locate(self, lat: float, lng: float) -> tuple[str, float]:
        """(구역 이름, 캠퍼스에서의 거리 m). 경계가 있으면 경계 안은 0."""
        if not self.boundary:
            a, d = self.nearest(lat, lng)
            return a.name, d
        c_lat, c_lng = self.center
        poly = [_xy(a, b, c_lat, c_lng) for a, b in self.boundary]
        if poly[0] == poly[-1]:
            poly = poly[:-1]
        p = _xy(lat, lng, c_lat, c_lng)
        dist = 0.0 if _inside(p, poly) else min(_seg_dist(p, a, b) for a, b in zip(poly, poly[1:] + poly[:1]))
        if dist == 0.0:
            return "캠퍼스 안", 0.0
        if self.anchors:
            gate, gd = self.nearest(lat, lng)
            if gd <= self.gate_zone_m:
                return f"{gate.name} 쪽", dist
        angle = math.degrees(math.atan2(p[1], p[0])) % 360
        return f"캠퍼스 {_DIRECTIONS[int((angle + 22.5) // 45) % 8]}", dist


# ---------------------------------------------------------------------------
# CSV 읽기 — 연도마다 인코딩·열 이름이 조금씩 달라서 너그럽게 읽는다
# ---------------------------------------------------------------------------

# 우리 이름 → 원천에서 쓰였던 열 이름들(앞쪽 우선)
COLUMNS: dict[str, tuple[str, ...]] = {
    "id": ("상가업소번호",),
    "name": ("상호명",),
    "branch": ("지점명",),
    "large": ("상권업종대분류명",),
    "middle": ("상권업종중분류명",),
    "small": ("상권업종소분류명",),
    "road_addr": ("도로명주소",),
    "jibun_addr": ("지번주소",),
    "floor": ("층정보",),
    "lng": ("경도",),
    "lat": ("위도",),
}
REQUIRED = ("id", "name", "large", "lng", "lat")


def read_text(path: Path) -> str:
    data = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"{path}: UTF-8 도 CP949 도 아니다")


def _resolve_columns(header: Sequence[str]) -> dict[str, str]:
    cleaned = {h.strip(): h for h in header}
    found: dict[str, str] = {}
    for key, names in COLUMNS.items():
        for n in names:
            if n in cleaned:
                found[key] = cleaned[n]
                break
    missing = [k for k in REQUIRED if k not in found]
    if missing:
        raise ValueError(f"상가정보 CSV 열을 찾지 못했다: {', '.join(COLUMNS[k][0] for k in missing)}")
    return found


def read_rows(text: str) -> Iterator[dict[str, str]]:
    """CSV 텍스트 → 우리 열 이름으로 바꾼 행. 쉼표·파이프(|) 구분 둘 다 받는다."""
    first = text.split("\n", 1)[0]
    delim = "|" if first.count("|") > first.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    cols = _resolve_columns(reader.fieldnames or [])
    for row in reader:
        yield {k: (row.get(src) or "").strip() for k, src in cols.items()}


# ---------------------------------------------------------------------------
# 업종 → 카테고리 사전 · 식사 유형 (정의서 7절 사전 · 5절 유형). 추정값이고 조사 때 고친다.
# ---------------------------------------------------------------------------

# 아예 후보에서 빼는 업종 (밥집이 아님 · 학식은 따로 다룸)
EXCLUDE_KEYWORDS = ("주점", "호프", "맥주", "소주", "포차", "요리주점", "바(BAR)", "유흥", "나이트", "단란", "구내식당", "급식", "출장", "케이터링")

# (키워드들, 카테고리, 식사 유형) — 위에서부터 먼저 맞는 것
RULES: tuple[tuple[tuple[str, ...], str, MealType], ...] = (
    (("카페", "커피", "제과", "제빵", "빵", "베이커리", "디저트", "아이스크림", "빙수", "도넛", "떡집", "떡 전문", "떡/한과", "차 전문", "주스"), "카페·베이커리", MealType.CAFE),
    (("김밥", "분식", "떡볶이", "만두", "라면"), "분식", MealType.QUICK),
    (("버거", "햄버거", "샌드위치", "토스트", "핫도그"), "패스트푸드", MealType.QUICK),
    (("치킨", "닭강정", "피자"), "패스트푸드", MealType.REGULAR),
    (("돈가스", "돈까스", "카레"), "일식", MealType.QUICK),          # 원천 '일식 카레/돈가스/덮밥' — 덮밥 규칙보다 먼저
    (("마라", "훠궈"), "중식", MealType.REGULAR),                    # '마라탕' 이 '탕' 에 걸리기 전에
    (("돼지고기", "소고기", "고기", "구이", "갈비", "삼겹", "곱창", "막창", "양꼬치", "오리", "족발", "보쌈"), "고기", MealType.GROUP),
    (("횟집",), "한식", MealType.GROUP),
    (("국밥", "해장", "국/탕", "찌개", "탕"), "국밥·찌개", MealType.REGULAR),
    (("덮밥", "도시락", "백반", "국수"), "한식", MealType.QUICK),
    (("쌀국수", "베트남", "태국", "인도", "동남아", "아시아", "아시안", "마라", "훠궈"), "아시안", MealType.REGULAR),
    (("중식", "중국", "짜장", "짬뽕"), "중식", MealType.REGULAR),
    (("일식", "초밥", "스시", "돈가스", "돈까스", "라멘", "우동", "회", "일본"), "일식", MealType.REGULAR),
    (("양식", "서양식", "파스타", "스테이크", "레스토랑", "브런치", "멕시코", "기타 외국식"), "양식", MealType.REGULAR),
    (("한식", "한정식"), "한식", MealType.REGULAR),
)


def classify(large: str, middle: str, small: str) -> tuple[str, MealType] | None:
    """(카테고리, 식사 유형). 밥집이 아니면 None. 음식 업종인데 못 맞추면 ('미분류', 일반)."""
    if "음식" not in large:
        return None
    text = f"{middle} {small}"
    if any(k in text for k in EXCLUDE_KEYWORDS):
        return None
    # 소분류가 더 구체적이라 먼저 본다 (예: 중분류 '한식' · 소분류 '돼지고기 구이' → 고기)
    for part in (small, middle):
        for keywords, category, meal_type in RULES:
            if any(k in part for k in keywords):
                return category, meal_type
    return "미분류", MealType.REGULAR


# ---------------------------------------------------------------------------
# 후보 만들기
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PlaceCandidate:
    """정의서 7절 `Place` + 조사에 도움이 되는 값. 조사 전이라 추천에는 쓰지 않는다(CA1-R11)."""

    id: str
    name: str
    category: str          # 추정 — 조사 때 확인
    mealTypeGuess: str     # 추정 — 조사 때 확인
    lat: float
    lng: float
    zone: str              # 구역: '서문 쪽' · '캠퍼스 북동쪽' · '캠퍼스 안' (경계가 없으면 가장 가까운 기준점)
    distanceM: int         # 캠퍼스 경계에서 직선거리 (경계 안 0, 경계가 없으면 기준점에서)
    address: str
    floor: str
    rawCategory: str       # 원천 업종(중분류 > 소분류)
    source: str = "semas"
    sourceId: str = ""


def build_candidates(rows: Iterable[Mapping[str, str]], campus: Campus) -> list[PlaceCandidate]:
    out: dict[str, PlaceCandidate] = {}
    for r in rows:
        cls = classify(r.get("large", ""), r.get("middle", ""), r.get("small", ""))
        if cls is None:
            continue
        try:
            lat, lng = float(r["lat"]), float(r["lng"])
        except (KeyError, ValueError):
            continue
        zone, dist = campus.locate(lat, lng)
        if dist > campus.radius_m:
            continue
        name = r["name"] + (f" {r['branch']}" if r.get("branch") else "")
        category, meal_type = cls
        pid = f"semas-{r['id']}"
        out[pid] = PlaceCandidate(
            id=pid,
            name=name,
            category=category,
            mealTypeGuess=meal_type.value,
            lat=round(lat, 6),
            lng=round(lng, 6),
            zone=zone,
            distanceM=round(dist),
            address=r.get("road_addr") or r.get("jibun_addr", ""),
            floor=r.get("floor", ""),
            rawCategory=" > ".join(x for x in (r.get("middle", ""), r.get("small", "")) if x),
            sourceId=r["id"],
        )
    return sorted(out.values(), key=lambda p: (p.distanceM, p.name))


def suspect_closed(prev: Sequence[Mapping], current: Sequence[PlaceCandidate]) -> list[dict]:
    """지난 목록에 있었는데 이번 원천에서 사라진 곳 (CA1-R10 '폐업 의심')."""
    now_ids = {p.id for p in current}
    return [dict(p) for p in prev if p.get("id") not in now_ids]


# ---------------------------------------------------------------------------
# 현장 조사 양식 (정의서 D3 · 7절 PlaceAttr)
# ---------------------------------------------------------------------------

SURVEY_FIXED = ["id", "식당 이름", "주소", "층", "구역", "거리(m)", "원천 업종", "종류(추정)", "식사 유형(추정)"]
SURVEY_FILL = [
    "종류(확인)",            # 한식·중식·일식·양식·분식·고기·국밥·찌개·패스트푸드·아시안·카페·베이커리
    "식사 유형(확인)",        # cafeteria·quick·regular·group·cafe
    "가격대",                # PRICE_BANDS_NOTE
    "혼밥(O/X)",
    "좌석(S/M/L)",
    "영업시간",              # 예: 월-금 11:00-21:00 / 토 11:00-15:00
    "브레이크",              # 예: 15:00-17:00
    "라스트오더",            # 예: 14:30 20:00 / 토 14:00 (구간마다 하나, 없으면 비움)
    "휴무",                  # 예: 일
    "대표 메뉴와 가격",       # 예: 참치김밥 4500 / 라볶이 6000
    "영업 상태",             # 영업 / 폐업 / 못 찾음
    "확인일",
    "확인자",
    "메모",
]
PRICE_BANDS_NOTE = "A ≤7천 · B 7천~1만 · C 1만~1.5만 · D >1.5만"
MEAL_TYPE_KO = {
    MealType.CAFETERIA.value: "학식",
    MealType.QUICK.value: "분식·덮밥·패스트푸드",
    MealType.REGULAR.value: "일반 식당",
    MealType.GROUP.value: "고기·단체",
    MealType.CAFE.value: "카페·베이커리",
}


def survey_targets(places: Sequence[PlaceCandidate], within_m: float | None, include_cafe: bool) -> list[PlaceCandidate]:
    """조사할 곳만 — 가까운 곳부터 (정의서 목표 11주차 60곳 · 기말 100곳). 카페는 Q5 미결이라 기본 제외."""
    return [
        p for p in places
        if (within_m is None or p.distanceM <= within_m) and (include_cafe or p.category != "카페·베이커리")
    ]


def _filled_rows(path: Path) -> dict[str, dict[str, str]]:
    """이미 있는 조사 양식에서 한 칸이라도 채운 줄 (id → 줄)."""
    if not path.exists():
        return {}
    rows = csv.DictReader(io.StringIO(read_text(path)))
    return {
        r["id"].strip(): r for r in rows
        if (r.get("id") or "").strip() and any((r.get(k) or "").strip() for k in SURVEY_FILL)
    }


def write_survey(
    path: Path,
    places: Sequence[PlaceCandidate],
    keep: Mapping[str, Mapping[str, str]] | None = None,
    all_places: Sequence[PlaceCandidate] = (),
) -> int:
    """조사 양식을 쓴다. `keep`(이미 채운 줄)의 채운 칸은 그대로 옮기고, 이번 범위 밖이어도 지우지 않는다.

    돌려주는 값: 옮겨 온 줄 수.
    """
    keep = dict(keep or {})
    by_id = {p.id: p for p in all_places}
    order = list(places) + [by_id[i] for i in keep if i in by_id and i not in {p.id for p in places}]
    kept = 0
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(SURVEY_FIXED + SURVEY_FILL)
        for p in order:
            old = keep.pop(p.id, None)
            kept += old is not None
            w.writerow(
                [p.id, p.name, p.address, p.floor, p.zone, p.distanceM, p.rawCategory, p.category, MEAL_TYPE_KO[p.mealTypeGuess]]
                + [(old or {}).get(k, "") for k in SURVEY_FILL]
            )
        for old in keep.values():   # 이번 원천에서 사라진 곳(폐업 의심) — 채운 값을 잃지 않게 그대로 둔다
            kept += 1
            w.writerow([old.get(k, "") for k in SURVEY_FIXED + SURVEY_FILL])
    return kept


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m meal.places", description="상가(상권)정보 CSV → 캠퍼스 주변 식당 후보 + 조사 양식")
    ap.add_argument("csv", type=Path, help="상가(상권)정보 시도별 CSV (광주광역시)")
    ap.add_argument("--campus", type=Path, default=DEFAULT_CAMPUS, help="기준점·반경 JSON")
    ap.add_argument("--out", type=Path, default=AGENT_DIR / "data", help="결과 폴더")
    ap.add_argument("--prev", type=Path, help="지난번 places.candidates.json (폐업 의심 비교)")
    ap.add_argument("--survey-within", type=float, default=50, help="조사 양식에 넣을 캠퍼스 경계에서의 최대 거리(m), 0 이면 전부 (기본 50)")
    ap.add_argument("--survey-cafe", action="store_true", help="조사 양식에 카페·베이커리도 넣기")
    args = ap.parse_args(argv)

    campus = Campus.load(args.campus)
    places = build_candidates(read_rows(read_text(args.csv)), campus)

    args.out.mkdir(parents=True, exist_ok=True)
    bundle = {
        "source": "소상공인시장진흥공단 상가(상권)정보",
        "sourceFile": args.csv.name,
        "builtAt": date.today().isoformat(),
        "campus": campus.name,
        "radiusM": campus.radius_m,
        "count": len(places),
        "places": [asdict(p) for p in places],
    }
    (args.out / "places.candidates.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    targets = survey_targets(places, args.survey_within or None, args.survey_cafe)
    survey_path = args.out / "survey.csv"
    kept = write_survey(survey_path, targets, _filled_rows(survey_path), places)

    print(f"후보 {len(places)}곳 → {args.out / 'places.candidates.json'}")
    scope = f"경계에서 {args.survey_within:.0f}m 안" if args.survey_within else "전체"
    print(f"조사 양식 {len(targets)}곳({scope}{'' if args.survey_cafe else ' · 카페 제외'}) → {survey_path}")
    if kept:
        print(f"  이미 채운 {kept}곳은 채운 값을 그대로 옮겼다")
    by_cat: dict[str, int] = {}
    for p in places:
        by_cat[p.category] = by_cat.get(p.category, 0) + 1
    print("종류별: " + " · ".join(f"{k} {v}" for k, v in sorted(by_cat.items(), key=lambda x: -x[1])))

    if args.prev:
        prev = json.loads(args.prev.read_text(encoding="utf-8")).get("places", [])
        gone = suspect_closed(prev, places)
        (args.out / "closed.suspect.json").write_text(json.dumps(gone, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"폐업 의심 {len(gone)}곳 → {args.out / 'closed.suspect.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
