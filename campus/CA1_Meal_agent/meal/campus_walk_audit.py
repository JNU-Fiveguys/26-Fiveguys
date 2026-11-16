"""Audit curated restaurants against a fifteen-minute OSM walk from campus gates.

This is an offline selection report, not a places/manifest mutator. Gate
coordinates are derived from referenced OSM boundary/road geometry; a guessed
coordinate or an unconfirmed gate name cannot make a restaurant eligible.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .availability import DISTRICTS
from .routing import DEFAULT_NETWORK_PATH, PedestrianRouter, _node_passable, _way_walkable
from .settings import load_settings

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BOUNDARY_PATH = ROOT / "data" / "raw" / "osm_jnu.json"
DEFAULT_ENTRANCES_PATH = ROOT / "data" / "routing" / "campus_entrances.json"
MAX_WALK_MIN = 15
USER_EXCLUDED_IDS = frozenset({"2097663323"})


def _cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def segment_intersection(a, b, c, d):
    """Return an actual intersecting point, including shared endpoints."""
    r, s = (b[0] - a[0], b[1] - a[1]), (d[0] - c[0], d[1] - c[1])
    divisor = _cross(r, s)
    if abs(divisor) < 1e-18:
        return None  # collinear overlap does not identify a unique gate
    offset = c[0] - a[0], c[1] - a[1]
    t, u = _cross(offset, s) / divisor, _cross(offset, r) / divisor
    if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
        return a[0] + t * r[0], a[1] + t * r[1]
    return None


def _segment(node_ids, pair, label):
    if (not isinstance(pair, list) or len(pair) != 2 or
            not any([u, v] == pair or [v, u] == pair for u, v in zip(node_ids, node_ids[1:]))):
        raise ValueError(f"{label}: referenced nodes must be a real adjacent segment")


def resolve_entrances(config: dict, network: dict, boundary_data: dict) -> list[dict]:
    """Resolve only geometry-backed entrances, retaining separate map verification."""
    if config.get("version") != 1 or not isinstance(config.get("entrances"), list):
        raise ValueError("Entrance configuration must have version 1 and entrances")
    nodes = {e["id"]: e for e in network["elements"] if e.get("type") == "node"}
    ways = {e["id"]: e for e in network["elements"] if e.get("type") == "way"}
    boundaries = {e["id"]: e for e in boundary_data["boundary"]["elements"] if e.get("type") == "way"}
    resolved, ids = [], set()
    for entry in config["entrances"]:
        if (not entry.get("id") or entry["id"] in ids or entry.get("district") not in DISTRICTS or
                not entry.get("name")):
            raise ValueError("Each entrance needs a unique id, district and name")
        ids.add(entry["id"])
        ref = entry["osm"]
        boundary, way = boundaries[ref["boundaryWayId"]], ways[ref["walkWayId"]]
        if boundary.get("tags", {}).get("amenity") != "university" or not _way_walkable(way.get("tags", {})):
            raise ValueError("Entrance must join a university boundary and a walkable way")
        geometry = boundary["geometry"]
        if len(geometry) != len(boundary["nodes"]):
            raise ValueError("Incomplete campus boundary geometry")
        bcoords = {nid: (g["lat"], g["lon"]) for nid, g in zip(boundary["nodes"], geometry)}
        if "nodeId" in ref:
            nid = ref["nodeId"]
            if nid not in way["nodes"] or nid not in bcoords:
                raise ValueError("Entrance node must belong to both the road and university boundary")
            if not _node_passable(nodes[nid].get("tags", {})):
                raise ValueError("Entrance node is not passable in the OSM snapshot")
            coordinate = nodes[nid]["lat"], nodes[nid]["lon"]
            if coordinate != bcoords[nid]:
                raise ValueError("OSM snapshots disagree on entrance node coordinates")
            kind = "shared_boundary_walk_node"
        else:
            wp, bp = ref["walkNodeIds"], ref["boundaryNodeIds"]
            _segment(way["nodes"], wp, "walk way")
            _segment(boundary["nodes"], bp, "campus boundary")
            if not all(_node_passable(nodes[nid].get("tags", {})) for nid in wp):
                raise ValueError("Entrance segment has a blocked OSM endpoint")
            road = [(nodes[nid]["lat"], nodes[nid]["lon"]) for nid in wp]
            coordinate = segment_intersection(*road, *(bcoords[nid] for nid in bp))
            if coordinate is None:
                raise ValueError("Referenced OSM segments do not intersect at a unique entrance")
            kind = "boundary_walk_intersection"
        verification = entry.get("verification", {})
        if verification.get("status") not in {"confirmed", "pending"}:
            raise ValueError("Entrance verification must be confirmed or pending")
        if verification["status"] == "confirmed" and not all(
                verification.get(k) for k in ("sourceUrl", "checkedAt", "note")):
            raise ValueError("A confirmed district entrance needs map observation evidence")
        resolved.append({"id": entry["id"], "district": entry["district"], "name": entry["name"],
                         "lat": coordinate[0], "lng": coordinate[1],
                         "verification": dict(verification),
                         "provenance": {"kind": kind, **ref,
                                        "boundaryUrl": f"https://www.openstreetmap.org/way/{boundary['id']}",
                                        "walkWayUrl": f"https://www.openstreetmap.org/way/{way['id']}",
                                        "walkWayTags": way.get("tags", {}),
                                        "boundaryName": boundary.get("tags", {}).get("name")}})
    return resolved


def read_manifests(paths: list[Path]) -> tuple[list[dict], list[dict]]:
    places, sources = {}, []
    for path in paths:
        raw = path.read_bytes()
        data = json.loads(raw)
        if data.get("version") != 1 or not isinstance(data.get("places"), list):
            raise ValueError(f"Not a version 1 normalized manifest: {path}")
        sources.append({"path": str(path.resolve()), "sha256": hashlib.sha256(raw).hexdigest(),
                        "checkedAt": data.get("checkedAt"), "placeCount": len(data["places"])})
        for place in data["places"]:
            nid = place.get("naverPlaceId")
            if not isinstance(nid, str) or not nid.isdigit() or place.get("district") not in DISTRICTS:
                raise ValueError(f"Invalid Naver ID or district in {path}")
            if not all(isinstance(place.get(k), (int, float)) and not isinstance(place[k], bool)
                       and math.isfinite(place[k]) for k in ("lat", "lng")):
                raise ValueError(f"No finite normalized coordinates for {nid}")
            if not (-90 <= place["lat"] <= 90 and -180 <= place["lng"] <= 180):
                raise ValueError(f"Invalid coordinate range for {nid}")
            if nid in places:
                if any(places[nid].get(k) != place.get(k) for k in ("address", "district", "lat", "lng")):
                    raise ValueError(f"Conflicting normalized location for Naver ID {nid}")
                places[nid]["manifestPaths"].append(str(path.resolve()))
            else:
                places[nid] = {**place, "manifestPaths": [str(path.resolve())]}
    return list(places.values()), sources


def audit_places(places: list[dict], entrances: list[dict], *, router, settings) -> dict:
    """Only an OSM route from a confirmed same-district gate can pass."""
    rows = []
    for place in places:
        nid, district = place["naverPlaceId"], place["district"]
        matching = [e for e in entrances if e["district"] == district]
        routes = []
        for entrance in matching:
            route = router.route((entrance["lat"], entrance["lng"]), (place["lat"], place["lng"]),
                                 meters_per_min=settings.meters_per_min, detour_factor=settings.detour_factor)
            routes.append({"entranceId": entrance["id"], "entranceConfirmed": entrance["verification"]["status"] == "confirmed",
                           **route.to_dict(), "exactDurationMin": round(route.distance_m / settings.meters_per_min, 3)})
        # Multiple *confirmed* pedestrian gates on the same side are permitted;
        # pending evidence can never displace a verified eligible route.
        verified = [r for r in routes if r["entranceConfirmed"] and r["source"] == "osm"]
        selected = min(verified or [r for r in routes if r["source"] == "osm"] or routes,
                       key=lambda r: r["distanceM"], default=None)
        if nid in USER_EXCLUDED_IDS:
            status, reason = "user_excluded", "사용자가 거리와 관계없이 후보에서 제외하도록 지정"
        elif not place.get("coordinateSource"):
            status, reason = "coordinate_unconfirmed", "식당 좌표의 출처가 없음"
        elif not matching or not any(e["verification"]["status"] == "confirmed" for e in matching):
            status, reason = "entrance_unconfirmed", "이 식당가의 학교 출입구 위치 확인이 필요"
        elif not verified:
            status, reason = "route_unknown", "확인된 출입구에서 연결된 OSM 보행 경로를 계산하지 못함"
        elif selected["durationMin"] > MAX_WALK_MIN:
            status, reason = "outside_walk_limit", f"해당 식당가 학교 출입구에서 OSM 도보 {MAX_WALK_MIN}분 초과"
        else:
            status, reason = "eligible", f"해당 식당가 학교 출입구에서 OSM 도보 {MAX_WALK_MIN}분 이내"
        rows.append({"naverPlaceId": nid, "name": place["name"], "address": place["address"],
                     "district": district, "lat": place["lat"], "lng": place["lng"],
                     "coordinateSource": place.get("coordinateSource"),
                     "sourceUrl": place.get("sourceUrl"), "manifestPaths": place.get("manifestPaths", []),
                     "eligible": status == "eligible", "status": status, "reason": reason,
                     "entranceId": selected["entranceId"] if selected else None,
                     "durationMin": selected["durationMin"] if selected else None,
                     "distanceM": selected["distanceM"] if selected else None,
                     "routeSource": selected["source"] if selected else None,
                     "connectorM": selected["connectorM"] if selected else None,
                     "routes": routes})
    counts = Counter(r["status"] for r in rows)
    return {"version": 1, "createdAt": datetime.now(timezone.utc).isoformat(),
            "rule": {"maxWalkMin": MAX_WALK_MIN, "metersPerMin": settings.meters_per_min,
                     "maxSnapM": settings.max_snap_m, "routeSourceRequired": "osm",
                     "entranceVerificationRequired": True, "sameDistrictRequired": True,
                     "excludedNaverPlaceIds": sorted(USER_EXCLUDED_IDS)},
            "limitations": ["OSM 경로는 공개 지도 스냅샷 기준이며 현장 통행 가능 여부를 보장하지 않습니다.",
                            "식당 좌표에서 보행망까지의 직선 연결 구간도 거리·시간에 포함하며 connectorM으로 별도 표시합니다.",
                            f"출입구 미확인·경로 미연결·직선거리 fallback은 {MAX_WALK_MIN}분 이내 후보로 판정하지 않습니다."],
            "entrances": entrances, "counts": {"total": len(rows), **dict(sorted(counts.items()))},
            "places": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifests", nargs="+", type=Path, help="Normalized manifests; directory reads *manifest.json")
    parser.add_argument("--entrances", type=Path, default=DEFAULT_ENTRANCES_PATH)
    parser.add_argument("--network", type=Path, default=DEFAULT_NETWORK_PATH)
    parser.add_argument("--boundary", type=Path, default=DEFAULT_BOUNDARY_PATH)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = sorted({p.resolve() for given in args.manifests for p in
                    (given.glob("*manifest.json") if given.is_dir() else [given])})
    if not paths:
        parser.error("No normalized manifests found")
    if args.output.resolve() in set(paths) | {args.entrances.resolve(), args.network.resolve(), args.boundary.resolve()}:
        parser.error("Report output must not overwrite source data")
    places, sources = read_manifests(paths)
    source_bytes = {p: p.read_bytes() for p in (args.entrances, args.network, args.boundary)}
    network = json.loads(source_bytes[args.network])
    entrances = resolve_entrances(json.loads(source_bytes[args.entrances]), network, json.loads(source_bytes[args.boundary]))
    settings = load_settings()
    router = PedestrianRouter(network, max_snap_m=settings.max_snap_m)
    report = audit_places(places, entrances, router=router, settings=settings)
    report["manifestSources"] = sources
    report["networkMetadata"] = network.get("metadata", {})
    report["auditSources"] = [{"path": str(p.resolve()), "sha256": hashlib.sha256(source_bytes[p]).hexdigest()}
                              for p in (args.entrances, args.network, args.boundary)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps(report["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
