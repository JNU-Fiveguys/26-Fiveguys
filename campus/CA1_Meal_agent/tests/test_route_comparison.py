from dataclasses import dataclass
import json

import pytest

from eval.compare_routes import REPORT_PATH, build_comparison, compare_trip, select_candidates, summarize
from meal.settings import load_settings


def survey_row(i, *, lng=None):
    return {"sourceId": f"p{i:03d}", "name": f"가게 {i}", "category": "한식",
            "boundaryM": str(i * 10), "lat": "35.17", "lng": str(lng or 126.9 + i / 10000)}


def test_survey_sample_is_deterministic_spread_and_deduplicated():
    rows = [survey_row(i) for i in range(20)]
    duplicate = {**rows[3], "sourceId": "duplicate"}
    selected = select_candidates(rows + [duplicate], count=5)
    assert selected == select_candidates(list(reversed(rows + [duplicate])), count=5)
    assert len(selected) == 5
    assert selected[0]["boundaryM"] == "0"
    assert selected[-1]["boundaryM"] == "190"
    assert len({(r["lat"], r["lng"]) for r in selected}) == 5


@pytest.mark.parametrize("count", [0, 1, -1, True, 2.5])
def test_bad_sample_count_rejected(count):
    with pytest.raises(ValueError):
        select_candidates([survey_row(1)], count)


@dataclass
class FakeRoute:
    distance_m: float
    duration_min: int
    source: str
    fallback_reason: str | None = None
    connector_m: float = 0


class FakeRouter:
    def __init__(self, routes):
        self.routes = iter(routes)

    def route(self, a, b, **kwargs):
        return next(self.routes)


def trip_with_routes(first, second):
    baseline = FakeRouter([FakeRoute(350, 5, "estimate"), FakeRoute(490, 7, "estimate")])
    return compare_trip(survey_row(1), (35.1, 126.9), (35.2, 126.9), baseline,
                        FakeRouter([first, second]), load_settings())


def test_two_leg_totals_and_delta_use_both_real_routes():
    trip = trip_with_routes(FakeRoute(560, 8, "osm"), FakeRoute(630, 9, "osm"))
    assert trip["fullyOsm"] is True
    assert trip["baseline"]["totalWalkMin"] == 12
    assert trip["routing"]["totalWalkMin"] == 17
    assert trip["deltaMinutes"] == 5
    assert trip["deltaDistanceM"] == 350


def test_fallback_is_counted_but_excluded_from_osm_difference_statistics():
    real = trip_with_routes(FakeRoute(560, 8, "osm"), FakeRoute(630, 9, "osm"))
    mixed = trip_with_routes(FakeRoute(350, 5, "estimate", "disconnected"), FakeRoute(70, 1, "osm"))
    stats = summarize([real, mixed])
    assert mixed["fullyOsm"] is False
    assert stats["fullyOsmTrips"] == 1
    assert stats["tripsWithFallback"] == 1
    assert stats["legSources"] == {"estimate": 1, "osm": 3}
    assert stats["fallbackReasons"] == {"disconnected": 1}
    assert stats["fullyOsmDeltaMinutes"] == {
        "min": 5, "median": 5, "mean": 5, "max": 5,
        "longerTrips": 1, "shorterTrips": 0, "unchangedTrips": 0,
    }


def test_empty_osm_sample_does_not_claim_zero_error():
    stats = summarize([])
    assert stats["fullyOsmTrips"] == 0
    assert stats["fullyOsmDeltaMinutes"]["mean"] is None
    assert stats["fullyOsmDeltaMinutes"]["median"] is None


@pytest.fixture(scope="module")
def real_comparison():
    return build_comparison()


def test_real_snapshot_two_leg_totals_and_fallback_labels(real_comparison):
    trips = real_comparison["trips"]
    assert len(trips) == 24
    assert real_comparison["summary"]["fullyOsmTrips"] > 0
    for trip in trips:
        for variant in ("baseline", "routing"):
            value = trip[variant]
            assert value["totalWalkMin"] == value["outbound"]["durationMin"] + value["toNextClass"]["durationMin"]
            for key in ("outbound", "toNextClass"):
                leg = value[key]
                assert leg["source"] in {"osm", "estimate"}
                assert (leg["fallbackReason"] is None) == (leg["source"] == "osm")
        assert trip["deltaMinutes"] == trip["routing"]["totalWalkMin"] - trip["baseline"]["totalWalkMin"]


def test_existing_timetable_shape_and_manual_destination_are_both_exercised(real_comparison):
    automatic = real_comparison["context"]
    manual = real_comparison["manualOverrideExample"]["context"]
    assert automatic["originSource"] == "timetable"
    assert automatic["destinationSource"] == "timetable"
    assert automatic["origin"]["code"] == "공7"
    assert automatic["destination"]["code"] == "경영1"
    assert manual["destinationSource"] == "manual"
    assert manual["destination"]["code"] == "공7"
    assert automatic["end"] == manual["end"] == "13:30"


def test_saved_route_comparison_is_reproducible_from_current_public_inputs(real_comparison):
    saved = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert saved == json.loads(json.dumps(real_comparison, ensure_ascii=False))
