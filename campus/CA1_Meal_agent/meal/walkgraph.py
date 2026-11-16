"""길 기준 도보 거리 — OpenStreetMap 의 길(도로·인도·캠퍼스 안 길)을 따라 가장 짧은 거리.

    python -m meal.walkgraph fetch        # OSM Overpass 에서 길을 받아 data/osm/walk.raw.json 에 둔다(커밋 안 됨)
    python -m meal.walkgraph buildings    # OSM 건물(이름 있는 대학 건물) → directory/buildings.json
    python -m meal.walkgraph check        # 길 데이터 요약 · 캠퍼스 중심에서 문까지 길 거리

길 데이터가 없으면 추천은 지금처럼 직선거리 × 우회 계수(1.3)로 계산한다.
© OpenStreetMap contributors (ODbL 1.0) — 화면에 쓸 때 출처를 표기한다.
표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .timing import distance_m

AGENT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_WALK = AGENT_DIR / "data" / "osm" / "walk.raw.json"
DEFAULT_BUILDINGS_RAW = AGENT_DIR / "data" / "osm" / "buildings.raw.json"
DEFAULT_BUILDINGS = AGENT_DIR / "directory" / "buildings.json"
OVERPASS = "https://overpass-api.de/api/interpreter"
USER_AGENT = "CA1-meal-preview/0.1"

# 식당 후보 범위(캠퍼스 경계에서 1.2km)를 덮는 상자 — 남·서·북·동
WALK_BBOX = (35.158, 126.881, 35.194, 126.929)
CAMPUS_BBOX = (35.168, 126.893, 35.186, 126.918)
SKIP_HIGHWAY = {"motorway", "motorway_link", "trunk_link", "construction", "proposed", "raceway", "bus_guideway"}
NO_ACCESS = {"private", "no"}

LatLng = tuple[float, float]


def walkable(tags: dict) -> bool:
    """사람이 걸을 수 있는 길인지 — 고속도로·공사 중·출입 금지 길은 뺀다."""
    if tags.get("highway") in SKIP_HIGHWAY or "highway" not in tags:
        return False
    if tags.get("foot") == "no":
        return False
    if tags.get("access") in NO_ACCESS and tags.get("foot") not in ("yes", "designated", "permissive"):
        return False
    return True


@dataclass
class WalkGraph:
    nodes: dict[int, LatLng]
    adj: dict[int, list[tuple[int, float]]]
    _grid: dict[tuple[int, int], list[int]] = field(default_factory=dict, repr=False)
    _trees: dict[int, tuple[dict[int, float], dict[int, int]]] = field(default_factory=dict, repr=False)

    CELL = 0.002   # 위도·경도 칸(약 200m) — 가까운 점 찾기용

    # --- 만들기 ---------------------------------------------------------------
    @classmethod
    def from_overpass(cls, data: dict) -> "WalkGraph":
        """Overpass `out body geom` 결과(way 에 nodes · geometry 가 같이 있음) → 가장 큰 연결 덩어리만 남긴 그래프."""
        nodes: dict[int, LatLng] = {}
        adj: dict[int, list[tuple[int, float]]] = {}
        for el in data.get("elements", []):
            if el.get("type") != "way" or not walkable(el.get("tags", {})):
                continue
            ids, geom = el.get("nodes", []), el.get("geometry", [])
            if len(ids) != len(geom):
                continue
            for nid, g in zip(ids, geom):
                nodes[nid] = (g["lat"], g["lon"])
            for a, b in zip(ids, ids[1:]):
                d = distance_m(*nodes[a], *nodes[b])
                adj.setdefault(a, []).append((b, d))
                adj.setdefault(b, []).append((a, d))
        keep = _largest_component(adj)
        nodes = {n: nodes[n] for n in keep}
        adj = {n: [(m, d) for m, d in adj[n] if m in keep] for n in keep}
        return cls(nodes, adj)

    @classmethod
    def load(cls, path: Path = DEFAULT_WALK) -> "WalkGraph | None":
        """길 데이터 파일이 없으면 None — 부르는 쪽은 직선거리로 돌아간다."""
        if not Path(path).exists():
            return None
        return cls.from_overpass(json.loads(Path(path).read_text(encoding="utf-8")))

    # --- 가까운 길 점 ---------------------------------------------------------
    def _cell(self, lat: float, lng: float) -> tuple[int, int]:
        return int(lat // self.CELL), int(lng // self.CELL)

    def snap(self, p: LatLng) -> int:
        """p 에서 가장 가까운 길 위의 점."""
        if not self._grid:
            for n, (la, ln) in self.nodes.items():
                self._grid.setdefault(self._cell(la, ln), []).append(n)
        ci, cj = self._cell(*p)
        for r in range(1, 30):
            near = [n for i in range(ci - r, ci + r + 1) for j in range(cj - r, cj + r + 1) for n in self._grid.get((i, j), ())]
            if near:
                return min(near, key=lambda n: (distance_m(*p, *self.nodes[n]), n))
        raise ValueError("근처에 길이 없다")

    # --- 가장 짧은 길 ---------------------------------------------------------
    def _tree(self, src: int) -> tuple[dict[int, float], dict[int, int]]:
        if src not in self._trees:
            dist, prev = {src: 0.0}, {}
            heap = [(0.0, src)]
            while heap:
                d, n = heapq.heappop(heap)
                if d > dist[n]:
                    continue
                for m, w in self.adj.get(n, ()):
                    nd = d + w
                    if nd < dist.get(m, math.inf):
                        dist[m], prev[m] = nd, n
                        heapq.heappush(heap, (nd, m))
            if len(self._trees) > 64:        # 출발점이 아주 많아지면 오래된 것부터 버린다
                self._trees.pop(next(iter(self._trees)))
            self._trees[src] = (dist, prev)
        return self._trees[src]

    def route(self, a: LatLng, b: LatLng) -> tuple[float, list[LatLng]]:
        """a → b 길 거리(m)와 지나는 점들. 양 끝은 가장 가까운 길 점까지 직선으로 잇는다."""
        sa, sb = self.snap(a), self.snap(b)
        dist, prev = self._tree(sa)
        if sb not in dist:
            raise ValueError("길이 이어지지 않는다")
        path = [sb]
        while path[-1] != sa:
            path.append(prev[path[-1]])
        pts = [a] + [self.nodes[n] for n in reversed(path)] + [b]
        meters = distance_m(*a, *self.nodes[sa]) + dist[sb] + distance_m(*self.nodes[sb], *b)
        return meters, pts

    def meters(self, a: LatLng, b: LatLng) -> float | None:
        """`recommend(router=...)` 에 넘기는 함수 — 길이 없으면 None(직선거리로 계산)."""
        try:
            return self.route(a, b)[0]
        except ValueError:
            return None


def _largest_component(adj: dict[int, list[tuple[int, float]]]) -> set[int]:
    seen: set[int] = set()
    best: set[int] = set()
    for start in adj:
        if start in seen:
            continue
        comp, stack = {start}, [start]
        while stack:
            n = stack.pop()
            for m, _ in adj[n]:
                if m not in comp:
                    comp.add(m)
                    stack.append(m)
        seen |= comp
        if len(comp) > len(best):
            best = comp
    return best


# ---------------------------------------------------------------------------
# 건물 목록 (정의서 '건물 마스터' 가 나오기 전까지 CA1 안에서 쓰는 임시본)
# ---------------------------------------------------------------------------

def buildings_from_overpass(data: dict) -> list[dict]:
    """대학 건물(building=university · dormitory)만 이름 · 중심 좌표로. 이름 순."""
    out = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        if tags.get("building") not in ("university", "dormitory") or not tags.get("name"):
            continue
        c = el.get("center") or {}
        if "lat" not in c:
            continue
        out.append({"name": tags["name"].strip(), "lat": round(c["lat"], 6), "lng": round(c["lon"], 6),
                    "osm": f"{el['type']}/{el['id']}"})
    out.sort(key=lambda b: b["name"])
    names = [b["name"] for b in out]
    return [b for i, b in enumerate(out) if b["name"] not in names[:i]]     # 같은 이름은 처음 것만


def load_buildings(path: Path = DEFAULT_BUILDINGS) -> dict[str, LatLng]:
    if not Path(path).exists():
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {b["name"]: (b["lat"], b["lng"]) for b in data.get("buildings", [])}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _overpass(query: str) -> bytes:
    body = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(OVERPASS, data=body, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m meal.walkgraph", description="길 기준 도보 거리 데이터")
    ap.add_argument("cmd", choices=["fetch", "buildings", "check"])
    args = ap.parse_args(argv)
    s, w, n, e = WALK_BBOX
    if args.cmd == "fetch":
        q = (f'[out:json][timeout:120];way["highway"]["highway"!~"^({"|".join(sorted(SKIP_HIGHWAY))})$"]'
             f"({s},{w},{n},{e});out body geom qt;")
        DEFAULT_WALK.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_WALK.write_bytes(_overpass(q))
        print(f"받음: {DEFAULT_WALK} ({DEFAULT_WALK.stat().st_size // 1024} KB) — © OpenStreetMap contributors (ODbL)")
        return 0
    if args.cmd == "buildings":
        if not DEFAULT_BUILDINGS_RAW.exists():
            s2, w2, n2, e2 = CAMPUS_BBOX
            q = (f'[out:json][timeout:90];(way["building"]["name"]({s2},{w2},{n2},{e2});'
                 f'relation["building"]["name"]({s2},{w2},{n2},{e2}););out tags center;')
            DEFAULT_BUILDINGS_RAW.parent.mkdir(parents=True, exist_ok=True)
            DEFAULT_BUILDINGS_RAW.write_bytes(_overpass(q))
        bl = buildings_from_overpass(json.loads(DEFAULT_BUILDINGS_RAW.read_text(encoding="utf-8")))
        DEFAULT_BUILDINGS.write_text(json.dumps({
            "_설명": "출발·다음 수업 건물 고르기용 임시 건물 목록(정의서 '건물 마스터'가 나오면 바꾼다). building=university·dormitory 중 이름 있는 것, 좌표는 건물 중심.",
            "source": "OpenStreetMap Overpass API — © OpenStreetMap contributors, ODbL 1.0 — 화면에 쓸 때 출처 표기 필요",
            "buildings": bl,
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"건물 {len(bl)}곳 → {DEFAULT_BUILDINGS}")
        return 0
    g = WalkGraph.load()
    if g is None:
        print("길 데이터가 없다 — python -m meal.walkgraph fetch", file=sys.stderr)
        return 1
    print(f"길 점 {len(g.nodes)}개 · 연결 {sum(len(v) for v in g.adj.values()) // 2}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
