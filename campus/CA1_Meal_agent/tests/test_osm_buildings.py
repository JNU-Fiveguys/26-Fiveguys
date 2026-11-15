"""공개 OSM 건물 relation 추출. 네트워크 조회나 보행망 재생성은 실행하지 않는다."""
import copy
import json
from pathlib import Path

import pytest

from scripts.fetch_osm_walk import extract_buildings, query_text

ROOT = Path(__file__).resolve().parent.parent


def relation_elements():
    return [
        {"type": "node", "id": 1, "lat": 35.0, "lon": 126.0},
        {"type": "node", "id": 2, "lat": 35.0, "lon": 126.004},
        {"type": "node", "id": 3, "lat": 35.004, "lon": 126.004},
        {"type": "node", "id": 4, "lat": 35.004, "lon": 126.0},
        {"type": "node", "id": 5, "lat": 35.001, "lon": 126.001, "tags": {"entrance": "yes"}},
        {"type": "way", "id": 10, "nodes": [1, 2, 3]},
        {"type": "way", "id": 11, "nodes": [3, 4, 1]},
        {"type": "way", "id": 12, "nodes": [5]},
        {"type": "relation", "id": 20,
         "members": [{"type": "way", "ref": 10, "role": "outer"},
                     {"type": "way", "ref": 11, "role": "outer"},
                     {"type": "way", "ref": 12, "role": "inner"}],
         "tags": {"building": "university", "name": "검증용 본관", "type": "multipolygon"}},
    ]


def test_fetch_query_includes_named_building_relations_and_members():
    assert 'relation["building"]["name"]' in query_text()
    assert '(._;>;);' in query_text()


def test_relation_uses_unique_outer_nodes_and_ignores_inner_entrance():
    data = extract_buildings(relation_elements(), {"source": "synthetic"})
    assert data["metadata"] == {"source": "synthetic"}
    (building,) = data["buildings"]
    assert building["osmType"] == "relation" and building["osmId"] == 20
    assert (building["lat"], building["lng"]) == (35.002, 126.002)
    assert building["coordinateKind"] == "building_centroid"


def test_relation_with_one_outer_entrance_uses_that_mapped_location():
    elements = relation_elements()
    elements[0]["tags"] = {"entrance": "yes"}
    (building,) = extract_buildings(elements, {})["buildings"]
    assert (building["lat"], building["lng"]) == (35.0, 126.0)
    assert building["coordinateKind"] == "mapped_entrance"


@pytest.mark.parametrize("missing", [3, 10])
def test_incomplete_relation_geometry_is_not_assigned_an_invented_center(missing):
    elements = [element for element in relation_elements() if element["id"] != missing]
    assert extract_buildings(elements, {})["buildings"] == []


def test_existing_named_way_and_point_behavior_is_preserved():
    elements = relation_elements()[:4]
    elements += [
        {"type": "way", "id": 30, "nodes": [1, 2, 3, 4, 1], "tags": {"building": "university", "name": "가 건물"}},
        {"type": "node", "id": 31, "lat": 35.1, "lon": 126.1, "tags": {"building": "yes", "name": "나 건물"}},
    ]
    buildings = extract_buildings(elements, {})["buildings"]
    assert [(b["osmType"], b["osmId"], b["lat"], b["lng"]) for b in buildings] == [
        ("way", 30, 35.002, 126.002), ("node", 31, 35.1, 126.1),
    ]


def test_real_engineering_two_relation_is_distinct_from_annex_and_has_two_network_entrances():
    snapshot = json.loads((ROOT / "data/routing/osm_engineering2.json").read_text(encoding="utf-8"))
    original = copy.deepcopy(snapshot)
    (building,) = extract_buildings(snapshot["elements"], snapshot["metadata"])["buildings"]
    assert snapshot == original
    assert building["name"] == "공과대 2호관"
    assert (building["osmType"], building["osmId"]) == ("relation", 15057693)
    assert (building["lat"], building["lng"]) == (35.1792471, 126.9088783)
    assert building["coordinateKind"] == "building_centroid"
    assert snapshot["metadata"]["source"] == "https://api.openstreetmap.org/api/0.6/relation/15057693/full.json"

    entrances = {item["id"]: (item["lat"], item["lon"]) for item in snapshot["elements"]
                 if item["type"] == "node" and item.get("tags", {}).get("entrance") == "yes"}
    assert entrances == {10292607373: (35.1793354, 126.9087906), 10292607425: (35.1790372, 126.9089506)}
    walk = json.loads((ROOT / "data/routing/walk_network.json").read_text(encoding="utf-8"))
    assert all(any(item["type"] == "way" and item.get("tags", {}).get("highway") == "footway"
                   and entrance in item.get("nodes", []) for item in walk["elements"]) for entrance in entrances)

    known = json.loads((ROOT / "data/routing/osm_buildings.json").read_text(encoding="utf-8"))["buildings"]
    (annex,) = [b for b in known if b["osmId"] == 1125471453]
    assert annex["name"] == "공과대 2호관 별관"
    assert (annex["lat"], annex["lng"]) != (building["lat"], building["lng"])
