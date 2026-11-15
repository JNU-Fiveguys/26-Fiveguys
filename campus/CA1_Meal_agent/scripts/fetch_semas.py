"""상가(상권)정보 API — 캠퍼스 반경 안 음식 업종 가게를 받아 data/raw/ 에 저장한다.

    python scripts/fetch_semas.py                 # 기본: 캠퍼스 중심 2,000m
    python scripts/fetch_semas.py --radius 1500

- 키는 .env 의 SEMAS_API_KEY (공공데이터포털 일반 인증키 Decoding). 키는 출력하거나 저장하지 않는다.
- 결과 원본은 data/raw/semas_radius.json (git 제외). 가공은 import_places.py 가 한다.
- 출처: 소상공인시장진흥공단 상가(상권)정보 (공공데이터포털)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
ENDPOINT = "https://apis.data.go.kr/B553077/api/open/sdsc2/storeListInRadius"

# OSM 전남대학교 영역(way 105125653)의 중심 ⏳ — 교문 기준 거리는 import 단계에서 다시 계산한다
CAMPUS_CENTER = (35.1769121, 126.904815)
FOOD = "I2"  # 업종 대분류: 음식


def load_key() -> str:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("SEMAS_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                if key:
                    return key
    sys.exit(".env 에 SEMAS_API_KEY 가 없습니다 (.env.example 참고)")


def call(key: str, page: int, rows: int, radius: int) -> dict:
    params = {
        "serviceKey": key,
        "pageNo": page,
        "numOfRows": rows,
        "radius": radius,
        "cx": CAMPUS_CENTER[1],  # 경도
        "cy": CAMPUS_CENTER[0],  # 위도
        "indsLclsCd": FOOD,
        "type": "json",
    }
    url = f"{ENDPOINT}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "campus-meal/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        # URL 에 키가 들어 있으므로 예외 메시지를 그대로 출력하지 않는다
        sys.exit(f"API HTTP 오류 {e.code} (page {page})")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 키 오류 등은 XML 로 온다 — 키가 섞이지 않은 앞부분만 보여 준다
        sys.exit("JSON 이 아닌 응답: " + text[:300].replace(key, "***"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--radius", type=int, default=2000, help="반경(m)")
    ap.add_argument("--rows", type=int, default=1000)
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    key = load_key()
    items: list[dict] = []
    page, total = 1, None
    while True:
        d = call(key, page, args.rows, args.radius)
        header = d.get("header", {})
        if header.get("resultCode") not in ("00", None):
            sys.exit(f"API 오류 {header.get('resultCode')}: {header.get('resultMsg')}")
        body = d.get("body", {})
        batch = body.get("items") or []
        total = int(body.get("totalCount", len(batch)))
        items.extend(batch)
        print(f"page {page}: {len(batch)}곳 (누적 {len(items)} / 전체 {total})")
        if not batch or len(items) >= total:
            break
        page += 1
        time.sleep(0.3)

    RAW.mkdir(parents=True, exist_ok=True)
    out = RAW / "semas_radius.json"
    out.write_text(json.dumps({
        "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "center": CAMPUS_CENTER,
        "radius": args.radius,
        "source": "소상공인시장진흥공단 상가(상권)정보 storeListInRadius",
        "items": items,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {out.relative_to(ROOT)} ({len(items)}곳)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
