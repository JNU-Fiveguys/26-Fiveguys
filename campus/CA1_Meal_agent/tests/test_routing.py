import json
import math

import pytest

from meal.routing import EstimateRouter, PedestrianRouter


def point(east, north):
    return (35.17 + north / 111195, 126.90 + east / (111195 * math.cos(math.radians(35.17))))


def node(nid, east, north, **tags):
    lat, lng = point(east, north)
    return {"type": "node", "id": nid, "lat": lat, "lon": lng, "tags": tags}


def way(wid, nodes, **tags):
    return {"type": "way", "id": wid, "nodes": nodes, "tags": {"highway": "footway", **tags}}


def graph(*elements, max_snap_m=20):
    return PedestrianRouter({"elements": list(elements)}, max_snap_m=max_snap_m)


def detour(*extra):
    return graph(node(1, 0, 0), node(2, 0, 100), node(3, 100, 100), node(4, 100, 0),
                 way(1, [1, 2, 3, 4]), *extra)


def test_osm_route_follows_topology_instead_of_straight_line_factor():
    route = detour().route(point(0, 0), point(100, 0))
    estimate = EstimateRouter().route(point(0, 0), point(100, 0))
    assert route.source == "osm"
    assert route.distance_m == pytest.approx(300, abs=0.1)
    assert route.duration_min == 5
    assert estimate.distance_m == pytest.approx(130, abs=0.1)
    assert estimate.duration_min == 2
    assert route.connector_m == pytest.approx(0)
    assert route.fallback_reason is None
    assert point(0, 100) in route.path and point(100, 100) in route.path


def test_osm_distance_does_not_apply_estimate_detour_factor_again():
    router = detour()
    a = router.route(point(0, 0), point(100, 0), detour_factor=1.3)
    b = router.route(point(0, 0), point(100, 0), detour_factor=2)
    assert a == b


def test_projection_and_unmapped_connectors_are_counted():
    router = graph(node(1, 0, 0), node(2, 100, 0), way(1, [1, 2]))
    route = router.route(point(20, 10), point(80, 10))
    assert route.source == "osm"
    assert route.distance_m == pytest.approx(80, abs=0.1)  # 10 + 60 + 10, not 120 via endpoints
    assert route.connector_m == pytest.approx(20, abs=0.1)
    assert route.to_dict()["connectorM"] == 20
    assert route.duration_min == 2


@pytest.mark.parametrize("tags", [
    {"access": "private"}, {"foot": "no"}, {"foot": "private"},
    {"highway": "motorway"}, {"highway": "construction"},
    {"foot": "use_sidepath"}, {"foot:conditional": "yes @ (Mo-Fr 09:00-17:00)"},
    {"access": "permit"}, {"access": "military"}, {"access": "customers"},
    {"access": "destination"}, {"access": "unknown_permission"},
    {"foot": "permit"}, {"foot": "military"}, {"foot": "destination"},
    {"foot": "unknown_permission"}, {"access": "yes", "foot": "permit"},
])
def test_forbidden_shortcut_is_not_walkable(tags):
    route = detour(way(2, [1, 4], **tags)).route(point(0, 0), point(100, 0))
    assert route.source == "osm"
    assert route.distance_m == pytest.approx(300, abs=0.1)


@pytest.mark.parametrize("access", ["private", "permit", "military"])
def test_explicit_foot_permission_overrides_general_restricted_access(access):
    route = detour(way(2, [1, 4], access=access, foot="yes")).route(point(0, 0), point(100, 0))
    assert route.distance_m == pytest.approx(100, abs=0.1)


def test_vehicle_oneway_is_not_a_pedestrian_oneway():
    router = graph(node(1, 0, 0), node(2, 100, 0), way(1, [1, 2], oneway="yes"))
    route = router.route(point(100, 0), point(0, 0))
    assert route.source == "osm"
    assert route.distance_m == pytest.approx(100, abs=0.1)


def test_pedestrian_oneway_is_respected_including_segment_interior():
    router = graph(node(1, 0, 0), node(2, 100, 0), way(1, [1, 2], **{"oneway:foot": "yes"}))
    forward = router.route(point(20, 0), point(80, 0))
    reverse = router.route(point(80, 0), point(20, 0))
    assert forward.source == "osm" and forward.distance_m == pytest.approx(60, abs=0.1)
    assert reverse.source == "estimate"
    assert reverse.fallback_reason == "disconnected_walk_network"


@pytest.mark.parametrize("tags", [{"barrier": "wall"}, {"barrier": "gate", "access": "private"},
                                  {"barrier": "gate", "locked": "yes"},
                                  {"barrier": "gate", "access": "permit"},
                                  {"barrier": "gate", "access": "military"}])
def test_impassable_barrier_node_prevents_shortcut(tags):
    route = detour(node(5, 50, 0, **tags), way(2, [1, 5, 4])).route(point(0, 0), point(100, 0))
    assert route.distance_m == pytest.approx(300, abs=0.1)


def test_open_gate_and_bollard_allow_walking():
    route = detour(node(5, 50, 0, barrier="gate"), way(2, [1, 5, 4])).route(point(0, 0), point(100, 0))
    assert route.distance_m == pytest.approx(100, abs=0.1)


def test_connector_cannot_jump_across_mapped_wall():
    router = graph(node(1, 0, 0), node(2, 100, 0), way(1, [1, 2]),
                   node(3, 0, 5), node(4, 100, 5),
                   {"type": "way", "id": 2, "nodes": [3, 4], "tags": {"barrier": "wall"}})
    route = router.route(point(50, 10), point(80, 0))
    assert route.source == "estimate"
    assert route.fallback_reason == "no_walkable_segment_nearby"


def test_graph_edge_cannot_cross_a_wall_without_an_opening():
    router = detour(node(5, 50, -10), node(6, 50, 10), way(2, [1, 4]),
                    {"type": "way", "id": 3, "nodes": [5, 6], "tags": {"barrier": "wall"}})
    route = router.route(point(0, 0), point(100, 0))
    assert route.source == "osm"
    assert route.distance_m == pytest.approx(300, abs=0.1)


@pytest.mark.parametrize("gate", [False, True])
def test_wall_vertex_is_only_passable_if_mapped_as_gate(gate):
    router = detour(node(5, 50, -10), node(6, 50, 0, **({"barrier": "gate"} if gate else {})),
                    node(7, 50, 10), way(2, [1, 6, 4]),
                    {"type": "way", "id": 3, "nodes": [5, 6, 7], "tags": {"barrier": "wall"}})
    route = router.route(point(0, 0), point(100, 0))
    assert route.distance_m == pytest.approx(100 if gate else 300, abs=0.1)


def test_disconnected_nearby_ways_are_not_joined_by_invented_edge():
    router = graph(node(1, 0, 0), node(2, 50, 0), node(3, 60, 0), node(4, 100, 0),
                   way(1, [1, 2]), way(2, [3, 4]))
    route = router.route(point(0, 0), point(100, 0))
    assert route.source == "estimate"
    assert route.fallback_reason == "disconnected_walk_network"
    assert route.distance_m == pytest.approx(130, abs=0.1)


def test_far_endpoint_fallback_is_explicit():
    route = detour().route(point(0, 0), point(500, 500))
    assert route.source == "estimate"
    assert route.fallback_reason == "no_walkable_segment_nearby"


def test_outside_download_bounds_is_not_routed_along_truncated_graph():
    router = PedestrianRouter({"metadata": {"bbox": [35.169, 126.899, 35.171, 126.901]},
                               "elements": [node(1, 0, 0), node(2, 500, 0), way(1, [1, 2])]})
    route = router.route(point(0, 0), point(400, 0))
    assert route.fallback_reason == "outside_network_bounds"


def test_missing_and_corrupt_snapshots_have_distinct_fallbacks(tmp_path):
    missing = PedestrianRouter.from_file(tmp_path / "missing.json")
    assert missing.route(point(0, 0), point(100, 0)).fallback_reason == "network_missing"
    invalid = tmp_path / "invalid.json"
    invalid.write_text("not json", encoding="utf-8")
    assert PedestrianRouter.from_file(invalid).route(point(0, 0), point(100, 0)).fallback_reason == "network_invalid"


def test_snapshot_cache_reloads_after_update(tmp_path):
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"elements": []}), encoding="utf-8")
    assert PedestrianRouter.from_file(path).route(point(0, 0), point(100, 0)).source == "estimate"
    path.write_text(json.dumps({"elements": [node(1, 0, 0), node(2, 100, 0), way(1, [1, 2])]}), encoding="utf-8")
    assert PedestrianRouter.from_file(path).route(point(0, 0), point(100, 0)).source == "osm"


def test_same_location_has_zero_walk_time():
    assert detour().route(point(0, 0), point(0, 0)).duration_min == 0
    assert EstimateRouter().route(point(0, 0), point(0, 0)).duration_min == 0


@pytest.mark.parametrize("point_value", [(math.nan, 0), (91, 0), (35, 181), (True, 0)])
def test_invalid_coordinate_rejected(point_value):
    with pytest.raises(ValueError):
        detour().route(point_value, point(0, 0))


@pytest.mark.parametrize("value", [0, -1, math.nan, math.inf])
def test_nonpositive_or_nonfinite_speed_rejected(value):
    with pytest.raises(ValueError):
        detour().route(point(0, 0), point(100, 0), meters_per_min=value)
