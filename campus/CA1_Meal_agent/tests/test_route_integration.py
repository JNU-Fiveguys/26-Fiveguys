import copy
from datetime import datetime

import pytest

from meal.places import SAMPLES_DIR, check_dirs
from meal.ranking import MealContext, rank
from meal.routing import WalkRoute
from meal.service import recommend
from meal.timing import compute_for_place

ORIGIN = (35.1765, 126.9095)
DEST = (35.18, 126.91)
PLACE = next(p for p in check_dirs([SAMPLES_DIR]).places if p["id"] == "s_01")


class FixedRouter:
    def __init__(self, source="osm", reason=None):
        self.calls = []
        self.source, self.reason = source, reason

    def route(self, origin, dest, *, meters_per_min, detour_factor):
        self.calls.append((origin, dest))
        minutes = 8 if origin == ORIGIN else 12
        return WalkRoute(minutes * meters_per_min, minutes, self.source, self.reason,
                         path=(origin, dest))


def test_osm_legs_drive_arrival_peak_wait_deadline_and_fit():
    router = FixedRouter()
    t = compute_for_place(PLACE, start=705, end=765, origin=ORIGIN, dest=DEST, router=router)
    assert router.calls == [(ORIGIN, (PLACE["lat"], PLACE["lng"])), ((PLACE["lat"], PLACE["lng"]), DEST)]
    assert t.arrive_at == 713 and t.peak and t.breakdown.wait == 8
    assert t.breakdown.walk_total == 20
    assert t.need_min == 63 and t.fit == "tight" and t.slack_min == -3
    assert t.leave_by == 748
    assert t.to_dict()["walkRoutes"]["back"]["durationMin"] == 12


def test_rank_uses_osm_by_default_and_preserves_whole_route_factor(monkeypatch):
    router = FixedRouter()
    monkeypatch.setattr("meal.timing.load_router", lambda max_snap_m: router)
    c = rank([PLACE], MealContext(2, 705, 850, ORIGIN, DEST)).top[0]
    assert c.timing.route_to.source == "osm"
    assert c.features["distance"] == pytest.approx(1 - 20 / 30)


def test_no_next_class_does_not_query_return_route():
    router = FixedRouter()
    t = compute_for_place(PLACE, start=705, end=None, origin=ORIGIN, dest=None, router=router)
    assert len(router.calls) == 1
    assert t.breakdown.walk_back == 0 and t.route_back is None
    assert t.to_dict()["walkRoutes"]["back"] is None


def test_fallback_is_visible_in_recommendation_and_warning():
    result = recommend(at=datetime(2026, 11, 11, 11, 45), origin=ORIGIN,
                       next_room=DEST, next_start="14:00", places=[copy.deepcopy(PLACE)],
                       router=FixedRouter("estimate", "disconnected_walk_network"))
    assert result["routing"]["estimatedLegs"] == 2
    assert result["routing"]["osmLegs"] == 0
    assert any("대체" in w for w in result["warnings"])
    assert result["candidates"][0]["walkRoutes"]["to"]["fallbackReason"] == "disconnected_walk_network"
