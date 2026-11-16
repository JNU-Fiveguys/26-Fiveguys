"""미리보기용 깔끔한 바탕 지도 — OSM 의 길 · 건물 모양 · 녹지 · 물만 그린다(가게 아이콘·글자 없음).

    python -m meal.basemap fetch    # OSM Overpass 에서 건물·녹지·물 → data/osm/base.raw.json (커밋 안 됨)
    python -m meal.basemap build    # 길(walk.raw.json) + 위 파일 → data/osm/basemap.json (화면이 받는 작은 파일)

타일 지도(OSM)는 가게·카페 아이콘이 그려져 있어 추천 별표가 묻힌다 → 아이콘 없는 지도를 직접 그린다.
© OpenStreetMap contributors (ODbL 1.0) — 화면에 출처를 표기한다. 표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .walkgraph import AGENT_DIR, DEFAULT_WALK, WALK_BBOX, _overpass, walkable

DEFAULT_BASE_RAW = AGENT_DIR / "data" / "osm" / "base.raw.json"
DEFAULT_BASEMAP = AGENT_DIR / "data" / "osm" / "basemap.json"

MAJOR = {"primary", "primary_link", "secondary", "secondary_link", "trunk", "tertiary", "tertiary_link"}
MINOR = {"residential", "unclassified", "living_street", "service", "road"}
GREEN_LEISURE = {"park", "garden", "pitch", "playground", "recreation_ground"}
GREEN_LANDUSE = {"grass", "forest", "meadow", "recreation_ground", "village_green", "cemetery"}
GREEN_NATURAL = {"wood", "scrub", "grassland"}


def road_class(tags: dict) -> str:
    h = tags.get("highway")
    if h in MAJOR:
        return "major"
    if h in MINOR:
        return "minor"
    return "path"


def area_class(tags: dict) -> str | None:
    if tags.get("building"):
        return "buildings"
    if tags.get("natural") == "water" or tags.get("waterway") == "riverbank" or tags.get("landuse") == "reservoir":
        return "water"
    if tags.get("leisure") in GREEN_LEISURE or tags.get("landuse") in GREEN_LANDUSE or tags.get("natural") in GREEN_NATURAL:
        return "green"
    return None


def _line(geom: list[dict]) -> list[list[float]]:
    return [[round(g["lat"], 5), round(g["lon"], 5)] for g in geom]


def build(walk: dict, base: dict | None) -> dict:
    """길은 major/minor/path, 면은 buildings/green/water — 좌표는 [위도, 경도] 소수 5자리(약 1m)."""
    out: dict[str, list] = {"major": [], "minor": [], "path": [], "buildings": [], "green": [], "water": []}
    for el in walk.get("elements", []):
        tags = el.get("tags", {})
        if el.get("type") == "way" and walkable(tags) and len(el.get("geometry", [])) > 1:
            out[road_class(tags)].append(_line(el["geometry"]))
    for el in (base or {}).get("elements", []):
        kind = area_class(el.get("tags", {}))
        if not kind:
            continue
        if el.get("type") == "way" and len(el.get("geometry", [])) > 2:
            out[kind].append(_line(el["geometry"]))
        elif el.get("type") == "relation":       # 바깥 테두리만 그린다
            for m in el.get("members", []):
                if m.get("role") == "outer" and len(m.get("geometry", [])) > 2:
                    out[kind].append(_line(m["geometry"]))
    return {"source": "© OpenStreetMap contributors (ODbL 1.0)", **out}


def load_basemap(path: Path = DEFAULT_BASEMAP) -> bytes | None:
    """화면에 그대로 보낼 JSON 바이트 — 없으면 None(타일 지도만 쓴다)."""
    return Path(path).read_bytes() if Path(path).exists() else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m meal.basemap", description="아이콘 없는 바탕 지도 데이터")
    ap.add_argument("cmd", choices=["fetch", "build"])
    args = ap.parse_args(argv)
    if args.cmd == "fetch":
        s, w, n, e = WALK_BBOX
        b = f"({s},{w},{n},{e})"
        q = (f"[out:json][timeout:150];("
             f'way["building"]{b};relation["building"]{b};'
             f'way["leisure"~"^({"|".join(sorted(GREEN_LEISURE))})$"]{b};'
             f'way["landuse"~"^({"|".join(sorted(GREEN_LANDUSE | {"reservoir"}))})$"]{b};'
             f'relation["landuse"~"^({"|".join(sorted(GREEN_LANDUSE))})$"]{b};'
             f'way["natural"~"^(water|{"|".join(sorted(GREEN_NATURAL))})$"]{b};'
             f'relation["natural"="water"]{b};'
             f");out body geom qt;")
        DEFAULT_BASE_RAW.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_BASE_RAW.write_bytes(_overpass(q))
        print(f"받음: {DEFAULT_BASE_RAW} ({DEFAULT_BASE_RAW.stat().st_size // 1024} KB) — © OpenStreetMap contributors (ODbL)")
        return 0
    if not DEFAULT_WALK.exists():
        print("길 데이터가 없다 — python -m meal.walkgraph fetch", file=sys.stderr)
        return 1
    walk = json.loads(DEFAULT_WALK.read_text(encoding="utf-8"))
    base = json.loads(DEFAULT_BASE_RAW.read_text(encoding="utf-8")) if DEFAULT_BASE_RAW.exists() else None
    data = build(walk, base)
    DEFAULT_BASEMAP.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    counts = " · ".join(f"{k} {len(v)}" for k, v in data.items() if isinstance(v, list))
    print(f"바탕 지도: {counts} → {DEFAULT_BASEMAP} ({DEFAULT_BASEMAP.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
