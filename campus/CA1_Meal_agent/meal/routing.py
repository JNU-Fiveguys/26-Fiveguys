"""Local shortest walking routes over an attributed OpenStreetMap snapshot.

No request-time network calls or personal-location logging. Missing/disconnected
coverage returns a labelled straight-line estimate rather than inventing edges.
Endpoints are projected onto the nearest traversable segment; the unverified
straight connectors are included in both distance and metadata.
"""
from __future__ import annotations

import heapq
import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

LatLng = tuple[float, float]
DEFAULT_NETWORK_PATH = Path(__file__).resolve().parent.parent / "data" / "routing" / "walk_network.json"
WALKABLE = frozenset({"footway", "pedestrian", "path", "steps", "living_street", "residential",
                      "service", "unclassified", "track", "road", "tertiary", "tertiary_link",
                      "secondary", "secondary_link", "primary", "primary_link", "cycleway"})
FOOT_ALLOWED = frozenset({"yes", "designated", "permissive"})
ACCESS_ALLOWED = frozenset({"yes", "designated", "permissive"})
PASSABLE_BARRIERS = frozenset({"gate", "lift_gate", "swing_gate", "kissing_gate", "stile", "entrance",
                              "bollard", "cycle_barrier", "kerb", "toll_booth", "cattle_grid"})


def _distance(a: LatLng, b: LatLng) -> float:
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    v = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    return 2 * 6371000.0 * math.asin(math.sqrt(min(1.0, v)))


def _coordinate(value: LatLng) -> LatLng:
    if len(value) != 2 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value):
        raise ValueError("경로 좌표는 유한한 위도·경도 두 값이어야 합니다")
    lat, lng = value
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError("경로 좌표가 위도·경도 범위를 벗어났습니다")
    return float(lat), float(lng)


def _positive(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name}은 유한한 양수여야 합니다")


@dataclass(frozen=True)
class WalkRoute:
    distance_m: float
    duration_min: int
    source: str
    fallback_reason: str | None = None
    connector_m: float = 0.0
    path: tuple[LatLng, ...] = ()

    def to_dict(self) -> dict:
        return {"distanceM": round(self.distance_m, 1), "durationMin": self.duration_min,
                "source": self.source, "fallbackReason": self.fallback_reason,
                "connectorM": round(self.connector_m, 1), "path": [list(p) for p in self.path]}


class EstimateRouter:
    """Explicit baseline, also used for labelled fallback when OSM is unavailable."""
    def __init__(self, reason: str = "estimate_requested"):
        self.reason = reason

    def route(self, origin: LatLng, dest: LatLng, *, meters_per_min: float = 70,
              detour_factor: float = 1.3) -> WalkRoute:
        origin, dest = _coordinate(origin), _coordinate(dest)
        _positive(meters_per_min, "meters_per_min")
        _positive(detour_factor, "detour_factor")
        direct = _distance(origin, dest)
        distance = 0.0 if direct < 1 else direct * detour_factor
        return WalkRoute(distance, math.ceil(distance / meters_per_min), "estimate", self.reason,
                         path=(origin, dest))


def _access(tags: dict) -> bool:
    foot = tags.get("foot")
    # Conditional access is not interpreted without a date/time/access calendar.
    if tags.get("foot:conditional") or (not foot and tags.get("access:conditional")):
        return False
    if foot is not None:
        # A mode-specific permission overrides general access. Unknown values,
        # permits and destination/customers-only access are not unrestricted
        # pedestrian transit, so they are conservatively excluded.
        return foot in FOOT_ALLOWED
    access = tags.get("access")
    return access is None or access in ACCESS_ALLOWED


def _way_walkable(tags: dict) -> bool:
    highway = tags.get("highway")
    if highway in {"construction", "proposed", "motorway", "motorway_link", "raceway"}:
        return False
    if highway not in WALKABLE and not (highway in {"trunk", "trunk_link"} and tags.get("foot") in FOOT_ALLOWED):
        return False
    if highway == "cycleway" and tags.get("foot") not in FOOT_ALLOWED:
        return False
    if tags.get("area") == "yes" or tags.get("indoor") == "yes":
        return False
    return _access(tags)


def _node_passable(tags: dict) -> bool:
    if not _access(tags):
        return False
    barrier = tags.get("barrier")
    if not barrier:
        return True
    if tags.get("locked") == "yes":
        return False
    return barrier in PASSABLE_BARRIERS or tags.get("foot") in FOOT_ALLOWED


@dataclass(frozen=True)
class _Segment:
    u: int
    v: int
    length: float
    forward: bool
    backward: bool


@dataclass(frozen=True)
class _Snap:
    segment: int
    fraction: float
    point: LatLng
    distance: float


def _project(point: LatLng, a: LatLng, b: LatLng) -> tuple[float, LatLng]:
    scale = math.cos(math.radians(point[0]))
    dx, dy = (b[1] - a[1]) * scale, b[0] - a[0]
    px, py = (point[1] - a[1]) * scale, point[0] - a[0]
    t = max(0.0, min(1.0, (px * dx + py * dy) / (dx * dx + dy * dy or 1)))
    return t, (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))


def _crosses(a: LatLng, b: LatLng, c: LatLng, d: LatLng) -> bool:
    """Strict interior crossing: touching a gate/line endpoint is not crossing it."""
    def orientation(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return orientation(a, b, c) * orientation(a, b, d) < -1e-24 and orientation(c, d, a) * orientation(c, d, b) < -1e-24


class PedestrianRouter:
    def __init__(self, data: dict | None = None, *, max_snap_m: float = 150,
                 unavailable_reason: str = "network_missing"):
        _positive(max_snap_m, "max_snap_m")
        self.max_snap_m = max_snap_m
        self.unavailable_reason = unavailable_reason
        self.metadata = (data or {}).get("metadata", {})
        self.nodes: dict[int, LatLng] = {}
        self.adjacency: dict[int, dict[int, float]] = {}
        self.segments: list[_Segment] = []
        self.barriers: list[tuple[LatLng, LatLng]] = []
        elements = (data or {}).get("elements", [])
        if not isinstance(elements, list) or not all(isinstance(e, dict) for e in elements):
            raise ValueError("OSM elements must be a list of objects")
        node_data = {e["id"]: e for e in elements if e.get("type") == "node" and "lat" in e and "lon" in e}
        self.nodes = {nid: _coordinate((n["lat"], n["lon"])) for nid, n in node_data.items()}
        blocked = {nid for nid, n in node_data.items() if not _node_passable(n.get("tags", {}))}
        ways = [e for e in elements if e.get("type") == "way"]
        for way in ways:
            tags, ids = way.get("tags", {}), way.get("nodes", [])
            if tags.get("barrier") and not _node_passable(tags):
                self.barriers.extend((self.nodes[a], self.nodes[b]) for a, b in zip(ids, ids[1:])
                                     if a in self.nodes and b in self.nodes)
                # A shared wall/fence vertex is not an entrance unless its own
                # tags explicitly describe a passable gate or foot permission.
                for nid in ids:
                    node_tags = node_data.get(nid, {}).get("tags", {})
                    if not ((node_tags.get("barrier") in PASSABLE_BARRIERS or
                             node_tags.get("foot") in FOOT_ALLOWED) and _node_passable(node_tags)):
                        blocked.add(nid)
        for way in ways:
            tags, ids = way.get("tags", {}), way.get("nodes", [])
            if not _way_walkable(tags):
                continue
            oneway = tags.get("oneway:foot", "no")
            forward, backward = oneway != "-1", oneway not in {"yes", "1", "true"}
            for u, v in zip(ids, ids[1:]):
                if u == v or u in blocked or v in blocked or u not in self.nodes or v not in self.nodes:
                    continue
                if any(_crosses(self.nodes[u], self.nodes[v], a, b) for a, b in self.barriers):
                    continue
                length = _distance(self.nodes[u], self.nodes[v])
                if length <= 0:
                    continue
                self.segments.append(_Segment(u, v, length, forward, backward))
                for a, b, allowed in ((u, v, forward), (v, u, backward)):
                    if allowed:
                        neighbors = self.adjacency.setdefault(a, {})
                        neighbors[b] = min(neighbors.get(b, math.inf), length)
                self.adjacency.setdefault(u, {})
                self.adjacency.setdefault(v, {})

    @classmethod
    def from_file(cls, path: Path | str = DEFAULT_NETWORK_PATH, *, max_snap_m: float = 150) -> "PedestrianRouter":
        path = Path(path)
        try:
            stat = path.stat()
        except FileNotFoundError:
            return cls(max_snap_m=max_snap_m)
        return _load_router(str(path.resolve()), stat.st_mtime_ns, stat.st_size, max_snap_m)

    # ── 가까운 보행길 찾기 가속 ──────────────────────────────────
    # 전체 구간(수만 개)을 매번 훑지 않도록, 구간을 위경도 격자 칸에 미리 넣어 두고
    # 점 주변 max_snap_m 안의 칸에 걸친 구간만 기존과 같은 규칙·같은 순서로 비교한다.
    _GRID_DEG = 0.002  # 약 200m 칸

    def _segment_grid(self) -> dict[tuple[int, int], list[int]]:
        grid = getattr(self, "_grid", None)
        if grid is None:
            grid = {}
            g = self._GRID_DEG
            for index, segment in enumerate(self.segments):
                (la, lo), (lb, lob) = self.nodes[segment.u], self.nodes[segment.v]
                for i in range(math.floor(min(la, lb) / g), math.floor(max(la, lb) / g) + 1):
                    for j in range(math.floor(min(lo, lob) / g), math.floor(max(lo, lob) / g) + 1):
                        grid.setdefault((i, j), []).append(index)
            self._grid = grid
            self._snap_cache: dict[LatLng, _Snap | None] = {}
        return grid

    def _nearby_segments(self, point: LatLng) -> list[int]:
        grid, g = self._segment_grid(), self._GRID_DEG
        # max_snap_m 를 위경도로 바꿀 때 넉넉하게(5%) 잡아 경계 근처 구간을 놓치지 않는다
        dlat = self.max_snap_m / 111_000 * 1.05
        dlng = self.max_snap_m / (111_000 * max(math.cos(math.radians(point[0])), 1e-6)) * 1.05
        found: set[int] = set()
        for i in range(math.floor((point[0] - dlat) / g), math.floor((point[0] + dlat) / g) + 1):
            for j in range(math.floor((point[1] - dlng) / g), math.floor((point[1] + dlng) / g) + 1):
                found.update(grid.get((i, j), ()))
        return sorted(found)  # 기존 전체 순회와 같은 순서 → 같은 동점 처리

    def _snap(self, point: LatLng) -> _Snap | None:
        self._segment_grid()
        if point in self._snap_cache:
            return self._snap_cache[point]
        best = self._snap_scan(point, self._nearby_segments(point))
        if len(self._snap_cache) > 4096:
            self._snap_cache.clear()
        self._snap_cache[point] = best
        return best

    def _snap_scan(self, point: LatLng, indices) -> _Snap | None:
        best = None
        for index in indices:
            segment = self.segments[index]
            fraction, projection = _project(point, self.nodes[segment.u], self.nodes[segment.v])
            distance = _distance(point, projection)
            if distance > self.max_snap_m or (best and distance >= best.distance - 1e-7):
                continue
            if any(_crosses(point, projection, a, b) for a, b in self.barriers):
                continue
            best = _Snap(index, fraction, projection, distance)
        return best

    def route(self, origin: LatLng, dest: LatLng, *, meters_per_min: float = 70,
              detour_factor: float = 1.3) -> WalkRoute:
        origin, dest = _coordinate(origin), _coordinate(dest)
        _positive(meters_per_min, "meters_per_min")
        _positive(detour_factor, "detour_factor")

        def fallback(reason: str) -> WalkRoute:
            return EstimateRouter(reason).route(origin, dest, meters_per_min=meters_per_min, detour_factor=detour_factor)

        if not self.segments:
            return fallback(self.unavailable_reason)
        bbox = self.metadata.get("bbox")
        if bbox and any(not (bbox[0] <= p[0] <= bbox[2] and bbox[1] <= p[1] <= bbox[3]) for p in (origin, dest)):
            return fallback("outside_network_bounds")
        start, finish = self._snap(origin), self._snap(dest)
        if start is None or finish is None:
            return fallback("no_walkable_segment_nearby")
        if _distance(origin, dest) < 1:
            return WalkRoute(0.0, 0, "osm", path=(origin, dest))
        a, b = self.segments[start.segment], self.segments[finish.segment]
        starts, goals = {}, {}
        if a.backward:
            starts[a.u] = a.length * start.fraction
        if a.forward:
            starts[a.v] = a.length * (1 - start.fraction)
        if b.forward:
            goals[b.u] = b.length * finish.fraction
        if b.backward:
            goals[b.v] = b.length * (1 - finish.fraction)
        # Exact graph vertices are accessible even on the restricted side of a
        # pedestrian one-way segment: no travel along that segment is needed.
        if start.fraction < 1e-9:
            starts[a.u] = 0.0
        if start.fraction > 1 - 1e-9:
            starts[a.v] = 0.0
        if finish.fraction < 1e-9:
            goals[b.u] = 0.0
        if finish.fraction > 1 - 1e-9:
            goals[b.v] = 0.0

        best_distance, best_node = math.inf, None
        if start.segment == finish.segment:
            delta = finish.fraction - start.fraction
            if (delta >= 0 and a.forward) or (delta <= 0 and a.backward):
                best_distance = abs(delta) * a.length
        queue = [(distance, node) for node, distance in starts.items()]
        heapq.heapify(queue)
        distances, parents = dict(starts), {}
        while queue:
            distance, node = heapq.heappop(queue)
            if distance != distances[node]:
                continue
            if distance >= best_distance:
                break
            if node in goals and distance + goals[node] < best_distance:
                best_distance, best_node = distance + goals[node], node
            for neighbor, length in self.adjacency.get(node, {}).items():
                candidate = distance + length
                if candidate < distances.get(neighbor, math.inf):
                    distances[neighbor] = candidate
                    parents[neighbor] = node
                    heapq.heappush(queue, (candidate, neighbor))
        if not math.isfinite(best_distance):
            return fallback("disconnected_walk_network")
        between = []
        node = best_node
        while node is not None:
            between.append(self.nodes[node])
            node = parents.get(node)
        between.reverse()
        connector = start.distance + finish.distance
        distance = best_distance + connector
        points = (origin, start.point, *between, finish.point, dest)
        path = tuple(p for i, p in enumerate(points) if i == 0 or p != points[i - 1])
        return WalkRoute(distance, math.ceil(distance / meters_per_min), "osm", connector_m=connector, path=path)


@lru_cache(maxsize=4)
def _load_router(path: str, mtime_ns: int, size: int, max_snap_m: float) -> PedestrianRouter:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("elements"), list):
            raise ValueError("Invalid OSM snapshot")
        return PedestrianRouter(data, max_snap_m=max_snap_m, unavailable_reason="network_empty")
    except (OSError, ValueError, KeyError, TypeError):
        return PedestrianRouter(max_snap_m=max_snap_m, unavailable_reason="network_invalid")
