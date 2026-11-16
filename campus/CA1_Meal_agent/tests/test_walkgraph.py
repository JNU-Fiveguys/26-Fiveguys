"""길 기준 도보 — OSM 길 조각으로 그래프를 만들고 가장 짧은 길을 찾는다."""

import pytest

from meal.walkgraph import WalkGraph, buildings_from_overpass, walkable

LAT, LNG = 35.1765, 126.9055


def way(wid, ids, pts, **tags):
    return {"type": "way", "id": wid, "tags": {"highway": "footway", **tags}, "nodes": ids,
            "geometry": [{"lat": a, "lon": b} for a, b in pts]}


def test_walkable_filters():
    assert walkable({"highway": "footway"}) and walkable({"highway": "primary"})
    assert not walkable({"highway": "motorway"}) and not walkable({"building": "yes"})
    assert not walkable({"highway": "service", "access": "private"})
    assert walkable({"highway": "service", "access": "private", "foot": "yes"})
    assert not walkable({"highway": "footway", "foot": "no"})


def test_route_follows_path_not_straight_line():
    # ㄷ자 길: A(남서) → 북서 → 북동 → B(남동). 직선 A–B 는 길이 없다
    a, nw, ne, b = (LAT, LNG), (LAT + 0.002, LNG), (LAT + 0.002, LNG + 0.002), (LAT, LNG + 0.002)
    g = WalkGraph.from_overpass({"elements": [way(1, [1, 2, 3, 4], [a, nw, ne, b])]})
    m, pts = g.route(a, b)
    assert 600 < m < 630                       # 222m + 182m + 222m — 직선(182m)보다 길다
    assert pts[0] == a and pts[-1] == b and len(pts) == 6
    assert g.meters(a, b) == pytest.approx(m)


def test_isolated_pieces_dropped_and_private_skipped():
    main = way(1, [1, 2, 3], [(LAT, LNG), (LAT + 0.001, LNG), (LAT + 0.002, LNG)])
    island = way(2, [10, 11], [(LAT + 0.01, LNG), (LAT + 0.011, LNG)])
    private = way(3, [3, 20], [(LAT + 0.002, LNG), (LAT + 0.002, LNG + 0.003)], access="private")
    g = WalkGraph.from_overpass({"elements": [main, island, private]})
    assert set(g.nodes) == {1, 2, 3}           # 떨어진 조각과 출입 금지 길은 빠진다
    assert g.snap((LAT + 0.0102, LNG)) == 3    # 떨어진 조각 근처라도 남은 길 위 점으로 붙는다


def test_buildings_from_overpass():
    data = {"elements": [
        {"type": "way", "id": 1, "tags": {"building": "university", "name": "공과대 7호관"}, "center": {"lat": 35.1, "lon": 126.9}},
        {"type": "way", "id": 2, "tags": {"building": "apartments", "name": "101"}, "center": {"lat": 35.2, "lon": 126.9}},
        {"type": "relation", "id": 3, "tags": {"building": "dormitory", "name": "생활관 9A동"}, "center": {"lat": 35.3, "lon": 126.8}},
    ]}
    assert [(b["name"], b["osm"]) for b in buildings_from_overpass(data)] == [("공과대 7호관", "way/1"), ("생활관 9A동", "relation/3")]
