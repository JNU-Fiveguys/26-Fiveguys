"""Download a bounded public OSM walking network and campus buildings.

Run from CA1_Meal_agent: python scripts/fetch_osm_walk.py
Only public map coordinates are sent; no personal timetable/location is sent.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "data" / "routing"
BBOX = (35.15, 126.88, 35.20, 126.94)  # south, west, north, east
BUILDING_BBOX = (35.166, 126.898, 35.186, 126.920)
ENDPOINTS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")


def query_text() -> str:
    bbox = ",".join(map(str, BBOX))
    buildings = ",".join(map(str, BUILDING_BBOX))
    return f'''[out:json][timeout:40];
(
  way["highway"]({bbox});
  way["barrier"]({bbox});
  way["building"]["name"]({buildings});
  node["building"]["name"]({buildings});
  relation["building"]["name"]({buildings});
  node["barrier"]({bbox});
);
(._;>;);
out body;'''


def fetch(endpoint: str, query: str) -> dict:
    request = Request(endpoint, data=urlencode({"data": query}).encode("utf-8"),
                      headers={"User-Agent": "JNU-Fiveguys-CA1/0.1 (public campus walking network)",
                               "Content-Type": "application/x-www-form-urlencoded"})
    with urlopen(request, timeout=55) as response:
        data = json.load(response)
    if "remark" in data or not data.get("elements"):
        raise ValueError(f"Overpass returned incomplete data: {data.get('remark', 'empty')}")
    return data


def extract_buildings(elements: list[dict], metadata: dict) -> dict:
    nodes = {item["id"]: item for item in elements if item["type"] == "node"}
    ways = {item["id"]: item for item in elements if item["type"] == "way"}
    buildings = []
    for item in elements:
        tags = item.get("tags", {})
        if not tags.get("building") or not tags.get("name"):
            continue
        if item["type"] == "node":
            coords = [item]
        elif item["type"] == "relation":
            # 이름·building 태그가 relation에만 있는 건물도 포함한다(공과대 2호관).
            # 안뜰(inner)은 제외하고, 여러 outer way의 공유점·닫힘 끝점은 한 번만 센다.
            outer = [member for member in item.get("members", []) if member.get("role", "") in {"outer", ""}]
            if not outer or any(member.get("type") != "way" or member.get("ref") not in ways for member in outer):
                continue
            node_ids = dict.fromkeys(nid for member in outer for nid in ways[member["ref"]].get("nodes", []))
            if any(nid not in nodes for nid in node_ids):
                continue  # 불완전한 외곽으로 잘못된 중심점을 만들지 않는다.
            coords = [nodes[nid] for nid in node_ids]
        else:
            coords = [nodes[nid] for nid in dict.fromkeys(item.get("nodes", [])) if nid in nodes]
        if not coords:
            continue
        entrances = [n for n in coords if n.get("tags", {}).get("entrance") not in (None, "no")]
        used = entrances if len(entrances) == 1 else coords
        buildings.append({
            "osmType": item["type"], "osmId": item["id"], "name": tags["name"],
            "lat": round(sum(n["lat"] for n in used) / len(used), 7),
            "lng": round(sum(n["lon"] for n in used) / len(used), 7),
            "coordinateKind": "mapped_entrance" if len(entrances) == 1 else "building_centroid",
            "tags": tags,
        })
    return {"metadata": metadata, "buildings": sorted(buildings, key=lambda b: (b["name"], b["osmId"]))}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", help="Override the public Overpass endpoint")
    args = parser.parse_args()
    query = query_text()
    failures = []
    data = None
    for endpoint in ([args.endpoint] if args.endpoint else ENDPOINTS):
        print(f"OSM 다운로드: {endpoint}", flush=True)
        try:
            data = fetch(endpoint, query)
            break
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            failures.append(f"{endpoint}: {error}")
    if data is None:
        raise SystemExit("OSM 다운로드 실패. 기존 파일은 유지합니다.\n" + "\n".join(failures))
    metadata = {
        "attribution": "© OpenStreetMap contributors",
        "license": "ODbL-1.0", "licenseUrl": "https://www.openstreetmap.org/copyright",
        "source": endpoint, "downloadedAt": datetime.now(timezone.utc).isoformat(),
        "osmTimestamp": data.get("osm3s", {}).get("timestamp_osm_base"),
        "bbox": list(BBOX), "query": query,
        "notes": "Public OSM snapshot. Unknown access, unmapped entrances and missing paths require field verification.",
    }
    snapshot = {"metadata": metadata, "elements": data["elements"]}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for filename, value in (("walk_network.json", snapshot),
                            ("osm_buildings.json", extract_buildings(data["elements"], metadata))):
        target = OUTPUT / filename
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        temporary.replace(target)
        print(f"저장: {target.name}")
    print(f"OSM 객체 {len(data['elements'])}개")


if __name__ == "__main__":
    main()
