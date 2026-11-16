import copy
import json
from types import SimpleNamespace

import pytest

from meal.campus_walk_audit import audit_places, read_manifests, resolve_entrances, segment_intersection
from meal.routing import WalkRoute


SETTINGS = SimpleNamespace(meters_per_min=70, detour_factor=1.3, max_snap_m=150)


def place(**extra):
    return {"naverPlaceId": "123", "name": "식당", "address": "광주 북구 예시로 1", "district": "예대",
            "lat": 35.18, "lng": 126.90, "coordinateSource": {"provider": "public_address_match"}, **extra}


def entrance(*, status="confirmed", district="예대", eid="gate"):
    return {"id": eid, "district": district, "lat": 35.181, "lng": 126.901,
            "verification": {"status": status}}


class Router:
    def __init__(self, route):
        self.result = route
        self.calls = []

    def route(self, origin, destination, **settings):
        self.calls.append((origin, destination, settings))
        return self.result


def run(*, route=None, p=None, gates=None):
    router = Router(route or WalkRoute(1050, 15, "osm", connector_m=20))
    result = audit_places([p or place()], [entrance()] if gates is None else gates,
                          router=router, settings=SETTINGS)
    return result, router


def test_fifteen_minutes_inclusive_and_app_walking_settings_preserved():
    report, router = run()
    row = report["places"][0]
    assert row["eligible"] is True
    assert row["durationMin"] == 15 and row["connectorM"] == 20
    assert row["routes"][0]["exactDurationMin"] == 15
    assert router.calls[0][2] == {"meters_per_min": 70, "detour_factor": 1.3}
    assert report["rule"]["maxSnapM"] == 150
    assert report["rule"]["maxWalkMin"] == 15
    assert "15분 이내" in row["reason"]


def test_over_fifteen_minutes_is_excluded_even_when_geometrically_near():
    report, _ = run(route=WalkRoute(1051, 16, "osm"))
    assert report["places"][0]["status"] == "outside_walk_limit"
    assert not report["places"][0]["eligible"]
    assert "15분 초과" in report["places"][0]["reason"]


def test_twelve_minute_restaurant_is_now_included():
    report, _ = run(route=WalkRoute(834.2, 12, "osm"))
    assert report["places"][0]["eligible"]


@pytest.mark.parametrize("reason", ["network_missing", "outside_network_bounds", "disconnected_walk_network"])
def test_short_fallback_is_never_accepted_as_a_real_walk(reason):
    report, _ = run(route=WalkRoute(30, 1, "estimate", reason))
    row = report["places"][0]
    assert row["status"] == "route_unknown" and not row["eligible"]
    assert row["routes"][0]["fallbackReason"] == reason


def test_pending_gate_keeps_route_evidence_without_passing_selection():
    report, _ = run(gates=[entrance(status="pending")])
    row = report["places"][0]
    assert row["durationMin"] == 15
    assert row["status"] == "entrance_unconfirmed" and not row["eligible"]


def test_another_district_gate_cannot_qualify_a_restaurant():
    report, router = run(gates=[entrance(district="정문")])
    assert router.calls == []
    assert report["places"][0]["durationMin"] is None
    assert report["places"][0]["status"] == "entrance_unconfirmed"


def test_missing_coordinate_source_does_not_pass():
    report, _ = run(p=place(coordinateSource={}))
    assert report["places"][0]["status"] == "coordinate_unconfirmed"


def test_user_excluded_gimbap_sandwich_is_excluded_even_at_one_minute():
    report, _ = run(p=place(naverPlaceId="2097663323"), route=WalkRoute(20, 1, "osm"))
    assert report["places"][0]["status"] == "user_excluded"
    assert not report["places"][0]["eligible"]


def test_input_place_and_entrance_observations_are_not_mutated():
    p, e = place(), entrance()
    before = copy.deepcopy((p, e))
    run(p=p, gates=[e])
    assert (p, e) == before


def test_confirmed_gate_wins_over_a_shorter_pending_gate():
    class MultipleRouter:
        def route(self, origin, dest, **kwargs):
            return WalkRoute(1120, 16, "osm") if origin[0] == 35.181 else WalkRoute(70, 1, "osm")
    gates = [entrance(), {**entrance(status="pending", eid="unconfirmed"), "lat": 35.182}]
    result = audit_places([place()], gates, router=MultipleRouter(), settings=SETTINGS)
    assert result["places"][0]["status"] == "outside_walk_limit"
    assert result["places"][0]["entranceId"] == "gate"


def source_geometry():
    config = {"version": 1, "entrances": [{"id": "gate", "district": "예대", "name": "학교 입구",
              "verification": {"status": "pending"},
              "osm": {"boundaryWayId": 100, "walkWayId": 200, "walkNodeIds": [5, 6],
                      "boundaryNodeIds": [2, 3]}}]}
    nodes = [{"type": "node", "id": 5, "lat": 0.5, "lon": 0.5},
             {"type": "node", "id": 6, "lat": 0.5, "lon": 1.5}]
    network = {"elements": nodes + [{"type": "way", "id": 200, "nodes": [5, 6],
                                    "tags": {"highway": "footway"}}]}
    boundary = {"boundary": {"elements": [{"type": "way", "id": 100, "tags": {"amenity": "university"},
                "nodes": [1, 2, 3, 4, 1], "geometry": [{"lat": lat, "lon": lon} for lat, lon in
                [(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)]]}]}}
    return config, network, boundary


def test_gate_coordinate_is_computed_from_real_source_intersection():
    gates = resolve_entrances(*source_geometry())
    assert (gates[0]["lat"], gates[0]["lng"]) == (0.5, 1)
    assert gates[0]["provenance"]["kind"] == "boundary_walk_intersection"
    assert gates[0]["provenance"]["walkWayUrl"] == "https://www.openstreetmap.org/way/200"


def test_invented_segment_is_rejected():
    config, network, boundary = source_geometry()
    config["entrances"][0]["osm"]["boundaryNodeIds"] = [1, 3]
    with pytest.raises(ValueError, match="adjacent segment"):
        resolve_entrances(config, network, boundary)


def test_missing_map_evidence_cannot_confirm_district_gate():
    config, network, boundary = source_geometry()
    config["entrances"][0]["verification"]["status"] = "confirmed"
    with pytest.raises(ValueError, match="map observation evidence"):
        resolve_entrances(config, network, boundary)


def test_inaccessible_walkway_is_not_an_entrance():
    config, network, boundary = source_geometry()
    network["elements"][-1]["tags"]["foot"] = "no"
    with pytest.raises(ValueError, match="walkable way"):
        resolve_entrances(config, network, boundary)


def test_locked_gate_node_is_not_bypassed_by_snapping_to_nearby_road():
    config, network, boundary = source_geometry()
    network["elements"][0]["tags"] = {"barrier": "gate", "locked": "yes"}
    with pytest.raises(ValueError, match="blocked OSM endpoint"):
        resolve_entrances(config, network, boundary)


def test_nonintersecting_or_collinear_segments_are_not_guessed():
    assert segment_intersection((0, 0), (0, 1), (1, 0), (1, 1)) is None
    assert segment_intersection((0, 0), (0, 1), (0, 0.5), (0, 2)) is None
    assert segment_intersection((0, 0), (0, 1), (-1, 2), (1, 2)) is None


def test_manifest_deduplication_preserves_sources_and_original_bytes(tmp_path):
    paths = [tmp_path / "a.json", tmp_path / "b.json"]
    data = {"version": 1, "checkedAt": "2026-11-11", "places": [place()]}
    for p in paths:
        p.write_text(json.dumps(data), encoding="utf-8")
    before = [p.read_bytes() for p in paths]
    places, sources = read_manifests(paths)
    assert len(places) == 1 and len(places[0]["manifestPaths"]) == 2 and len(sources) == 2
    assert [p.read_bytes() for p in paths] == before


def test_conflicting_branches_or_coordinates_fail_instead_of_overwriting(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps({"version": 1, "places": [place()]}), encoding="utf-8")
    b.write_text(json.dumps({"version": 1, "places": [place(lng=126.91)]}), encoding="utf-8")
    with pytest.raises(ValueError, match="Conflicting normalized location"):
        read_manifests([a, b])


def test_raw_unlocated_records_cannot_silently_gain_coordinates(tmp_path):
    path = tmp_path / "raw.json"
    path.write_text(json.dumps({"version": 1, "places": [place(lat=None)]}), encoding="utf-8")
    with pytest.raises(ValueError, match="finite normalized coordinates"):
        read_manifests([path])
