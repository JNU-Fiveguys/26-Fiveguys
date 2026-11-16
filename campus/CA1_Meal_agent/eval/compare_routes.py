"""OSM 보행 경로와 기존 직선거리 추정 비교: python -m eval.compare_routes.

공공 조사 후보의 실제 좌표는 이동 거리 비교에만 사용한다. 추천 순위 비교는
기존 가짜 가게 샘플로 분리하며, 실제 사용자 시간표·과목명은 읽거나 저장하지 않는다.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from meal.settings import SETTINGS_PATH, Settings, load_settings
from .input_digest import text_sha256

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "eval" / "route_comparison.json"
SURVEY_PATH = ROOT / "data" / "survey" / "candidates.csv"
BUILDINGS_PATH = ROOT / "data" / "buildings.json"
AT = datetime.fromisoformat("2026-11-11T11:50:00+09:00")


def select_candidates(rows: list[dict], count: int = 24) -> list[dict]:
    """동일 좌표를 합친 뒤 경계 거리순 등간격으로 뽑는다. 결과에 맞춰 고르지 않는다."""
    if isinstance(count, bool) or not isinstance(count, int) or count < 2:
        raise ValueError("sample count는 2 이상의 정수여야 합니다")
    ordered = sorted(rows, key=lambda r: (float(r["boundaryM"]), r["sourceId"]))
    unique = {}
    for row in ordered:
        unique.setdefault((float(row["lat"]), float(row["lng"])), row)
    candidates = list(unique.values())
    if len(candidates) <= count:
        return candidates
    return [candidates[round(i * (len(candidates) - 1) / (count - 1))] for i in range(count)]


def compact_route(route) -> dict:
    """긴 경로 좌표 대신 거리·시간·연결 구간과 실제 사용 원천을 남긴다."""
    return {
        "distanceM": round(route.distance_m, 2),
        "durationMin": route.duration_min,
        "source": route.source,
        "fallbackReason": route.fallback_reason,
        "connectorM": round(route.connector_m, 2),
    }


def compare_trip(row: dict, origin: tuple, dest: tuple, baseline, router, s: Settings) -> dict:
    here = (float(row["lat"]), float(row["lng"]))
    points = ((origin, here), (here, dest))
    variants = {}
    for label, engine in (("baseline", baseline), ("routing", router)):
        legs = [compact_route(engine.route(a, b, meters_per_min=s.meters_per_min,
                                            detour_factor=s.detour_factor)) for a, b in points]
        variants[label] = {
            "outbound": legs[0], "toNextClass": legs[1],
            "totalWalkMin": sum(leg["durationMin"] for leg in legs),
            "totalDistanceM": round(sum(leg["distanceM"] for leg in legs), 2),
        }
    routed = variants["routing"]
    return {
        "sourceId": row["sourceId"], "name": row["name"], "category": row["category"],
        "lat": here[0], "lng": here[1], "boundaryM": float(row["boundaryM"]),
        **variants,
        "deltaMinutes": routed["totalWalkMin"] - variants["baseline"]["totalWalkMin"],
        "deltaDistanceM": round(routed["totalDistanceM"] - variants["baseline"]["totalDistanceM"], 2),
        "fullyOsm": all(routed[leg]["source"] == "osm" for leg in ("outbound", "toNextClass")),
    }


def summarize(trips: list[dict]) -> dict:
    legs = [row["routing"][leg] for row in trips for leg in ("outbound", "toNextClass")]
    osm = [row for row in trips if row["fullyOsm"]]
    deltas = [row["deltaMinutes"] for row in osm]
    return {
        "tripCount": len(trips), "fullyOsmTrips": len(osm),
        "tripsWithFallback": len(trips) - len(osm),
        "legSources": dict(sorted(Counter(leg["source"] for leg in legs).items())),
        "fallbackReasons": dict(sorted(Counter(leg["fallbackReason"] for leg in legs
                                               if leg["source"] != "osm").items())),
        "fullyOsmDeltaMinutes": {
            "min": min(deltas) if deltas else None,
            "median": statistics.median(deltas) if deltas else None,
            "mean": round(statistics.mean(deltas), 2) if deltas else None,
            "max": max(deltas) if deltas else None,
            "longerTrips": sum(d > 0 for d in deltas),
            "shorterTrips": sum(d < 0 for d in deltas),
            "unchangedTrips": sum(d == 0 for d in deltas),
        },
    }


def _hash(path: Path) -> str:
    return text_sha256(path)


def demo_attendance() -> dict:
    """현재 F3 attendance/summary 계약의 합성 일정. 개인 시간표가 아니다."""
    return {
        "semester": {"id": "2026-2", "start": "2026-09-01", "end": "2026-12-20"},
        "current": "2026-2",
        "courses": [{"excluded": False, "sessions": [
            {"date": AT.date().isoformat(), "start": "11:00", "end": "11:50",
             "room": "공7-223", "state": "scheduled", "kind": "regular"},
            {"date": AT.date().isoformat(), "start": "13:30", "end": "14:45",
             "room": "경영1-101", "state": "scheduled", "kind": "regular"},
        ]}],
    }


def compare_sample_ranking(selection, baseline, router, settings: Settings) -> dict:
    from meal.places import SAMPLES_DIR, check_dirs
    from meal.ranking import rank

    checked = check_dirs([SAMPLES_DIR], today=AT.date())
    if checked.errors:
        raise ValueError("샘플 데이터 오류: " + "; ".join(map(str, checked.errors)))
    results = {"baseline": rank(checked.places, selection.context, s=settings, router=baseline),
               "routing": rank(checked.places, selection.context, s=settings, router=router)}
    packed = {}
    for key, result in results.items():
        groups = {}
        for group in ("top", "more", "tight", "cafeteria"):
            groups[group] = [{"placeId": c.place_id, "name": c.place["name"],
                              "category": c.place["category"], "score": round(c.score, 6),
                              "totalWalkMin": c.timing.breakdown.walk_total,
                              "needMin": c.timing.need_min, "fit": c.timing.fit,
                              "legSources": [r["source"] for r in c.timing.to_dict()["walkRoutes"].values() if r]}
                             for c in getattr(result, group)]
        packed[key] = {**groups, "excluded": result.excluded}
    return {"context": selection.to_dict(), "sampleCount": len(checked.places), "results": packed}


def build_comparison(sample_count: int = 24) -> dict:
    from meal.places import SAMPLES_DIR
    from meal.routing import DEFAULT_NETWORK_PATH, EstimateRouter, PedestrianRouter
    from meal.timetable import load_buildings, resolve_context

    settings = load_settings()
    router = PedestrianRouter.from_file(max_snap_m=settings.max_snap_m)
    baseline = EstimateRouter()
    buildings = load_buildings()
    overview = demo_attendance()
    selection = resolve_context(overview, at=AT, buildings=buildings)
    manual = resolve_context(overview, at=AT, next_room="공7-223", buildings=buildings)
    with SURVEY_PATH.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    picked = select_candidates(rows, sample_count)
    trips = [compare_trip(row, selection.context.origin, selection.context.dest,
                          baseline, router, settings) for row in picked]
    paths = [SETTINGS_PATH, BUILDINGS_PATH, SURVEY_PATH, DEFAULT_NETWORK_PATH,
             *sorted(SAMPLES_DIR.glob("*.json"))]
    return {
        "description": "OSM 기반 보행 경로와 직선거리×우회계수 추정의 두 구간 도보 비교",
        "assumptions": [
            "실제 공공 상가정보의 좌표를 사용하지만 영업·메뉴·현장 도보를 검증한 것은 아니다.",
            "시간표는 기존 학사 앱 attendance/summary 형식의 합성 일정이며 개인 시간표를 사용하지 않는다.",
            "기준일 2026-11-11 수요일 11:50, 공과대 7호관 출발, 13:30 경영대 1호관 다음 수업.",
            "건물 위치는 OSM 도형 중심점이다. 실내 이동·층 이동·출입구 정확도는 포함하지 않는다.",
            "OSM 경로에도 위치에서 가까운 보행선까지의 직선 연결 구간(connectorM)이 포함되며 출입구 현장 확인이 필요하다.",
            "직선 추정은 각 구간 직선거리×detourFactor÷분속의 올림, OSM은 경로 길이÷분속의 올림이다.",
            "두 구간 모두 OSM을 사용한 여행만 차이 통계에 포함한다. 추정 대체는 원인과 함께 따로 센다.",
            "추천 순위 비교는 실제 가게가 아닌 기존 s_01~s_08 가짜 샘플을 사용하며 학식은 별도 목록이다.",
            "OSM도 실측 시간이 아니다. 신호 대기·임시 통제·미등록 보행로에 따른 차이는 남아 있다.",
        ],
        "network": {"file": str(DEFAULT_NETWORK_PATH.relative_to(ROOT)).replace("\\", "/"),
                    "metadata": router.metadata},
        "settings": {"metersPerMin": settings.meters_per_min,
                     "estimateDetourFactor": settings.detour_factor,
                     "maxSnapM": settings.max_snap_m},
        "context": selection.to_dict(),
        "syntheticAttendance": overview,
        "selection": {"surveyCount": len(rows), "selectedCount": len(picked),
                      "method": "좌표 중복 제거 후 경계 거리·sourceId 순으로 정렬한 목록에서 등간격 선택"},
        "summary": summarize(trips),
        "trips": trips,
        "manualOverrideExample": {
            "input": {"nextRoom": "공7-223", "nextStart": None},
            "context": manual.to_dict(),
            "trip": compare_trip(picked[0], manual.context.origin, manual.context.dest,
                                 baseline, router, settings) if picked else None,
        },
        "sampleRankings": {
            "timetable": compare_sample_ranking(selection, baseline, router, settings),
            "manualOverride": compare_sample_ranking(manual, baseline, router, settings),
        },
        "inputSha256": {str(path.relative_to(ROOT)).replace("\\", "/"): _hash(path) for path in paths},
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    parser.add_argument("--sample-count", type=int, default=24)
    args = parser.parse_args()
    report = build_comparison(args.sample_count)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    context, summary = report["context"], report["summary"]
    print(f"{context['origin']['name']} → 식당 → {context['destination']['name']}")
    print(f"두 구간 OSM {summary['fullyOsmTrips']}/{summary['tripCount']}곳; 추정 대체 포함 {summary['tripsWithFallback']}곳")
    print("OSM 적용 구간:", summary["legSources"], "대체 이유:", summary["fallbackReasons"])
    print("시간 차이(분, OSM-기존):", summary["fullyOsmDeltaMinutes"])
    print("가게                         기존  경로  차이  양구간OSM")
    for trip in report["trips"]:
        print(f"{trip['name']:<25} {trip['baseline']['totalWalkMin']:>3}  "
              f"{trip['routing']['totalWalkMin']:>3}  {trip['deltaMinutes']:>+3}  {trip['fullyOsm']}")
    for label, scenario in report["sampleRankings"].items():
        print(f"가짜 샘플 순위 [{label}]: " + " → ".join(
            ", ".join(c["placeId"] for c in scenario["results"][variant]["top"])
            for variant in ("baseline", "routing")))
    print("현재 시간표 형식의 합성 일정이며 실제 개인 시간표·실측 도보 평가가 아닙니다.")
    print(f"JSON: {args.output}")


if __name__ == "__main__":
    main()
