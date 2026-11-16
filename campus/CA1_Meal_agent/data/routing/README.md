# OSM walking snapshot

- Source: [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), **ODbL 1.0**. Attribution must remain visible when displaying map-derived paths or distributing this dataset.
- `walk_network.json`: public nodes/ways and provenance (`metadata`), downloaded through Overpass. Bounds are south 35.15, west 126.88, north 35.20, east 126.94. The query and exact OSM timestamp are stored in the file.
- `osm_buildings.json`: named building coordinates extracted from that snapshot. A single mapped entrance is preferred; otherwise the unique polygon vertices' mean is used and labelled `building_centroid`. These are building-level coordinates, not classroom or verified entrance coordinates. The download covers surrounding buildings too; the application building catalog selects campus buildings separately.
- `osm_engineering2.json`: supplemental public [OSM relation 15057693](https://www.openstreetmap.org/relation/15057693) and its outer way/nodes, fetched from the [OSM API](https://api.openstreetmap.org/api/0.6/relation/15057693/full). This is the main Engineering 2 building, distinct from annex way 1125471453. Its two mapped entrances already connect to the existing walking snapshot. The building catalog uses the outer vertices' mean (`35.1792471, 126.9088783`).
- The downloader now includes named building relations and resolves their outer member ways. Incomplete outer geometry is rejected instead of producing a partial center point; inner courtyard vertices are excluded.

Refresh from `CA1_Meal_agent`:

```bash
python scripts/fetch_osm_walk.py
```

The downloader never uploads personal locations or timetables. It replaces the files only after a successful, nonempty Overpass response. Routing then works entirely offline.

`meal.routing.PedestrianRouter` builds a pedestrian graph and finds a shortest route. It respects pedestrian access, `oneway:foot`, impassable barrier nodes, mapped walls/fences, and a maximum endpoint snap distance. Ordinary vehicle `oneway` does not restrict walking. Conditional, permit, military, customers-only, destination-only and unknown access restrictions are conservatively excluded unless a specific pedestrian permission allows access; closed pedestrian areas are not converted to shortcuts. OSM access conventions: [foot](https://wiki.openstreetmap.org/wiki/Key:foot), [barrier](https://wiki.openstreetmap.org/wiki/Key:barrier), [oneway:foot](https://wiki.openstreetmap.org/wiki/Key:oneway:foot).

Returned distance includes the OSM path plus straight connectors from supplied coordinates to the nearest eligible segment (`connectorM`). These short connectors are estimates; mapped wall crossings are rejected, but unmapped entrances, barriers and real opening hours cannot be verified from this snapshot. Floor and classroom-door travel are not modelled.

Missing/invalid data, outside bounds, a distant segment or disconnected components return `source="estimate"` plus a specific `fallbackReason`, using the old straight-line × detour factor. A fallback is never labelled an OSM route. Network distance itself is **not** multiplied by the detour factor again.
