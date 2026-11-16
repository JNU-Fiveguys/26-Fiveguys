"""아이콘 없는 바탕 지도 — OSM 길·건물·녹지만 골라 작은 파일로."""

from meal.basemap import area_class, build, road_class


def way(tags, n=3):
    return {"type": "way", "id": 1, "tags": tags, "geometry": [{"lat": 35.1 + i * 1e-4, "lon": 126.9} for i in range(n)]}


def test_classes():
    assert road_class({"highway": "primary"}) == "major" and road_class({"highway": "residential"}) == "minor"
    assert road_class({"highway": "footway"}) == "path"
    assert area_class({"building": "yes"}) == "buildings" and area_class({"leisure": "park"}) == "green"
    assert area_class({"natural": "water"}) == "water" and area_class({"amenity": "cafe"}) is None


def test_build_keeps_only_shapes():
    walk = {"elements": [way({"highway": "secondary"}), way({"highway": "footway", "access": "private"})]}
    base = {"elements": [way({"building": "yes", "name": "가게"}, 4), way({"amenity": "restaurant"}, 4),
                         {"type": "relation", "id": 2, "tags": {"landuse": "grass"},
                          "members": [{"role": "outer", **way({}, 4)}, {"role": "inner", **way({}, 4)}]}]}
    d = build(walk, base)
    assert len(d["major"]) == 1 and d["path"] == []            # 출입 금지 길은 그리지 않는다
    assert len(d["buildings"]) == 1 and len(d["green"]) == 1    # 가게 점·이름은 들어가지 않는다
    assert d["buildings"][0][0] == [35.1, 126.9]
