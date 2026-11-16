"""조사 후보 목록 만들기 — 상가정보 + 북구 음식점현황 + 북구 착한가격업소 + OSM 캠퍼스 경계.

    python scripts/fetch_semas.py          # 먼저 상가정보 받기
    python scripts/build_candidates.py     # → data/survey/candidates.csv + 요약

입력 (data/raw/, git 제외)
  semas_radius.json                         상가(상권)정보 — 이름 · 소분류 · 좌표 · 주소
  *_북구_음식점현황_*.csv                    인허가 — 실제 영업 신고가 있는지 확인
  *_북구_착한가격업소_*.csv                  대표 메뉴 · 가격
  osm_jnu.json                              OSM 전남대학교 경계 (ODbL)

출력
  data/survey/candidates.csv                현장 조사 후보 (경계 거리순). 엑셀·구글 시트로 열어 조사 계획에 쓴다
"""

from __future__ import annotations

import csv
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "survey" / "candidates.csv"

MAX_BOUNDARY_M = 1200  # 캠퍼스 경계에서 ⏳1.2km (정의서 CA1-D1)

# 상가정보 소분류 → (우리 종류, 식사 유형 추정). None = 식사 추천 대상 아님(주점 · 카페 등)
SUBCAT: dict[str, tuple[str, str] | None] = {
    "백반/한정식": ("한식", "일반"),
    "국/탕/찌개류": ("국밥·찌개", "일반"),
    "국수/칼국수": ("한식", "일반"),
    "냉면/밀면": ("한식", "일반"),
    "전/부침개": ("한식", "일반"),
    "기타 한식 음식점": ("한식", "일반"),
    "돼지고기 구이/찜": ("고기", "고기·단체"),
    "소고기 구이/찜": ("고기", "고기·단체"),
    "닭/오리고기 구이/찜": ("고기", "고기·단체"),
    "곱창 전골/구이": ("고기", "고기·단체"),
    "족발/보쌈": ("고기", "고기·단체"),
    "해산물 구이/찜": ("한식", "고기·단체"),
    "김밥/만두/분식": ("분식", "간단식"),
    "버거": ("패스트푸드", "간단식"),
    "토스트/샌드위치/샐러드": ("패스트푸드", "간단식"),
    "치킨": ("패스트푸드", "일반"),
    "피자": ("양식", "일반"),
    "그 외 기타 간이 음식점": ("분식", "간단식"),
    "중국집": ("중식", "일반"),
    "마라탕/훠궈": ("중식", "일반"),
    "일식 회/초밥": ("일식", "일반"),
    "횟집": ("일식", "고기·단체"),
    "일식 카레/돈가스/덮밥": ("일식", "일반"),
    "일식 면 요리": ("일식", "일반"),
    "경양식": ("양식", "일반"),
    "파스타/스테이크": ("양식", "일반"),
    "기타 서양식 음식점": ("양식", "일반"),
    "베트남식 전문": ("아시안", "일반"),
    "기타 동남아식 전문": ("아시안", "일반"),
    "구내식당": ("학식", "학식"),
    "뷔페": ("한식", "일반"),
    # 식사 추천에서 뺀다 (정의서 CA1-Q5: 카페는 ⏳ — 정하면 여기만 바꾼다)
    "카페": None, "빵/도넛": None, "떡/한과": None, "아이스크림/빙수": None,
    "요리 주점": None, "생맥주 전문": None, "일반 유흥 주점": None, "무도 유흥 주점": None,
}


# ── 거리 ────────────────────────────────────────────────────

def _xy(lat: float, lng: float, lat0: float) -> tuple[float, float]:
    """작은 범위용 평면 근사(m)."""
    return (lng * 111_320 * math.cos(math.radians(lat0)), lat * 111_195)


def boundary_distance(lat: float, lng: float, poly: list[tuple[float, float]]) -> float:
    """다각형 경계까지 거리(m). 안쪽이면 0."""
    lat0 = poly[0][0]
    px, py = _xy(lat, lng, lat0)
    pts = [_xy(a, b, lat0) for a, b in poly]
    inside = False
    best = float("inf")
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        if (y1 > py) != (y2 > py) and px < (x2 - x1) * (py - y1) / (y2 - y1) + x1:
            inside = not inside
        dx, dy = x2 - x1, y2 - y1
        t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy or 1)))
        best = min(best, math.hypot(px - (x1 + t * dx), py - (y1 + t * dy)))
    return 0.0 if inside else best


# ── 이름 · 주소 맞추기 ──────────────────────────────────────

def norm_name(s: str) -> str:
    s = re.sub(r"\(.*?\)|\[.*?\]", "", s or "")
    return re.sub(r"[\s·.,'\"&\-_/]", "", s).lower()


def road_key(addr: str) -> str | None:
    """'광주광역시 북구 설죽로214번길 109 (용봉동)' → '설죽로214번길 109'."""
    m = re.search(r"([가-힣0-9]+(?:로|길)(?:\s?[0-9]+번?길)?)\s*([0-9]+(?:-[0-9]+)?)", addr or "")
    return f"{m.group(1).replace(' ', '')} {m.group(2)}" if m else None


def read_csv_any(path: Path) -> list[dict]:
    for enc in ("utf-8-sig", "cp949"):
        try:
            with path.open(encoding=enc, newline="") as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("csv", b"", 0, 1, f"인코딩을 알 수 없음: {path}")


def one(pattern: str) -> Path | None:
    files = sorted(RAW.glob(pattern))
    return files[-1] if files else None


# ── 본체 ────────────────────────────────────────────────────

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    semas = json.loads((RAW / "semas_radius.json").read_text(encoding="utf-8"))["items"]
    osm = json.loads((RAW / "osm_jnu.json").read_text(encoding="utf-8"))
    poly = [(p["lat"], p["lon"]) for p in osm["boundary"]["elements"][0]["geometry"]]

    lic_path, good_path = one("*음식점현황*.csv"), one("*착한가격*.csv")
    licensed_by_road: dict[str, set[str]] = {}
    licensed_names: Counter = Counter()
    if lic_path:
        for r in read_csv_any(lic_path):
            k = road_key(r.get("소재지(도로명)", ""))
            n = norm_name(r.get("업소명", ""))
            licensed_names[n] += 1
            if k:
                licensed_by_road.setdefault(k, set()).add(n)

    good: list[tuple[str, str | None, dict]] = []  # (이름, 도로명 키, 행)
    if good_path:
        for r in read_csv_any(good_path):
            if r.get("업종구분") in ("한식", "중식", "일식", "양식", "분식", "경양식", "기타외식업"):
                good.append((norm_name(r["업소명"]), road_key(r.get("주소", "")), r))

    def find_good(n: str, rk: str | None) -> dict | None:
        """착한가격업소 연결: 도로명 주소가 같고 이름이 같거나 한쪽이 다른 쪽을 포함할 때만.
        이름만 같은 곳은 체인점일 수 있어 연결하지 않는다."""
        for gn, grk, r in good:
            if rk and grk == rk and (gn == n or (min(len(gn), len(n)) >= 2 and (gn in n or n in gn))):
                return r
        return None

    def match_name(n: str, names: set[str]) -> bool:
        return any(n and m and (n == m or n in m or m in n) for m in names)

    rows = []
    skipped = Counter()
    for it in semas:
        mapping = SUBCAT.get(it["indsSclsNm"], ("한식", "일반"))
        if mapping is None:
            skipped[it["indsSclsNm"]] += 1
            continue
        lat, lng = float(it["lat"]), float(it["lon"])
        d = boundary_distance(lat, lng, poly)
        if d > MAX_BOUNDARY_M:
            continue
        name = it["bizesNm"] + (f" {it['brchNm']}" if it.get("brchNm") else "")
        n = norm_name(it["bizesNm"])
        rk = road_key(it.get("rdnmAdr", ""))
        if rk and rk in licensed_by_road and match_name(n, licensed_by_road[rk]):
            licensed = "주소+이름"
        elif licensed_names.get(n) == 1:
            licensed = "이름만"
        else:
            licensed = ""
        g = find_good(n, rk)
        cat, meal = mapping
        rows.append({
            "sourceId": it["bizesId"],
            "name": name,
            "category": cat,
            "mealTypeHint": meal,
            "subCategory": it["indsSclsNm"],
            "boundaryM": round(d),
            "walkMinHint": math.ceil(d * 1.3 / 70),
            "licensed": licensed,
            "goodPriceMenu": g["대표메뉴"] if g else "",
            "goodPrice": g["대표메뉴가격"] if g else "",
            "floor": it.get("flrNo", ""),
            "dong": it.get("ldongNm", ""),
            "address": it.get("rdnmAdr") or it.get("lnoAdr", ""),
            "lat": round(lat, 6),
            "lng": round(lng, 6),
        })

    rows.sort(key=lambda r: (r["boundaryM"], r["name"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:  # 엑셀에서 바로 열리도록 BOM
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # ── 요약 ──
    print(f"상가정보 {len(semas)}곳 → 식사 업종 · 경계 {MAX_BOUNDARY_M}m 안 {len(rows)}곳 → {OUT.relative_to(ROOT)}")
    print(f"제외(주점·카페 등): {sum(skipped.values())}곳")
    print("\n경계 거리별 (도보 추정 = 거리 × 1.3 ÷ 70)")
    for m, label in [(0, "캠퍼스 안"), (270, "~5분"), (540, "~10분"), (810, "~15분"), (1200, "~22분")]:
        print(f"  {label:<8} ≤{m:>5}m : {sum(1 for r in rows if r['boundaryM'] <= m):>4}곳")
    near = [r for r in rows if r["boundaryM"] <= 540]
    print("\n도보 10분 안 종류별:", Counter(r["category"] for r in near).most_common())
    print("도보 10분 안 인허가 확인:", Counter(r["licensed"] or "못 찾음" for r in near).most_common())
    gp = [r for r in rows if r["goodPrice"]]
    print(f"착한가격업소 연결: {len(gp)}곳 (도보 10분 안 {sum(1 for r in gp if r['boundaryM'] <= 540)}곳)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
