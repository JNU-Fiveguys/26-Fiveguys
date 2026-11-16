"""시간표/직접 입력과 OSM 경로를 하나의 추천 계산기에 연결한다."""
from __future__ import annotations

from datetime import datetime

from . import academic
from .availability import availability_verified, candidate_active
from .places import load_places, recommendation_ready, effective_menus, effective_price_source, rep_price
from .ranking import Filters, TasteSummary, rank
from .routing import EstimateRouter
from .settings import load_settings
from .timetable import resolve_context
from .timing import load_router


def catalog_counts(places: list[dict]) -> dict:
    real = [p for p in places if p["source"] != "sample"]
    active = [p for p in real if candidate_active(p)]
    ready = sum(recommendation_ready(p) for p in active)
    return {"total": len(real), "active": len(active), "ready": ready,
            "needsSurvey": len(active) - ready,
            "missing": sum(p.get("sourceStatus", "active") == "missing" for p in real),
            "availabilityExcluded": sum(not availability_verified(p) for p in real)}


def list_places(*, query: str = "", status: str = "active", ready: bool | None = None,
                offset: int = 0, limit: int = 20) -> dict:
    """실제 식당 목록. 조사 전 가게도 표시하지만 추천 준비 여부를 분명히 구분한다."""
    if status not in {"active", "missing", "all"} or offset < 0 or not 1 <= limit <= 100:
        raise ValueError("목록 조회 조건이 올바르지 않습니다.")
    places = load_places()
    counts = catalog_counts(places)
    words = query.strip().casefold().split()
    selected = []
    for p in places:
        if p["source"] == "sample":
            continue
        state = p.get("sourceStatus", "active")
        is_ready = recommendation_ready(p)
        haystack = " ".join(str(p.get(key, "")) for key in ("id", "name", "category", "address")).casefold()
        if ((status == "active" and not candidate_active(p)) or
                (status == "missing" and state != "missing") or (ready is not None and is_ready != ready)
                or any(word not in haystack for word in words)):
            continue
        research = p.get("research", {})
        selected.append({key: p.get(key) for key in ("id", "name", "category", "address", "lat", "lng", "source", "sourceId")}
                        | {"sourceStatus": state, "ready": is_ready, "repPrice": rep_price(p),
                           "priceSource": effective_price_source(p),
                           "availability": p.get("availability"), "menuObservation": p.get("menuObservation"),
                           "hoursObservation": p.get("hoursObservation"), "followupReview": p.get("followupReview"), "research": {
                            "checkedAt": research.get("checkedAt"),
                            "menus": research.get("facts", {}).get("menus", {}).get("status", "unconfirmed"),
                            "hours": research.get("facts", {}).get("hours", {}).get("status", "unconfirmed"),
                            "unknownFields": research.get("unknownFields", []),
                        }})
    selected.sort(key=lambda p: (p["name"], p["id"]))
    researched = [p for p in places if p.get("research") and p["source"] != "sample" and candidate_active(p)]
    research_counts = {"checked": len(researched), **{key: sum(
        p["research"]["facts"][key]["status"] == "confirmed" for p in researched
    ) for key in ("menus", "hours")}}
    return {"items": selected[offset:offset + limit], "total": len(selected), "offset": offset,
            "limit": limit, "counts": counts, "researchCounts": research_counts}


def get_place(place_id: str) -> dict:
    for place in load_places():
        if place["id"] == place_id and place["source"] != "sample":
            return {**place, "ready": recommendation_ready(place), "effectiveMenus": effective_menus(place),
                    "repPrice": rep_price(place), "priceSource": effective_price_source(place)}
    raise KeyError(place_id)


def recommend(*, at: datetime, timetable: dict | None = None, auto_timetable: bool = False, origin=None,
              next_room=None, next_start: str | None = None, no_next_class: bool = False,
              rain: bool = False, exam_period: bool = False, filters: Filters | None = None,
              taste: TasteSummary | None = None, include_samples: bool = False,
              route_mode: str = "osm", places=None, buildings=None, router=None, timetable_reader=None) -> dict:
    """개인 시간표·위치는 요청 동안만 사용한다. 명시한 다음 강의실이 시간표보다 우선한다."""
    if route_mode not in {"osm", "estimate"}:
        raise ValueError("경로 방식은 osm 또는 estimate여야 합니다.")
    if not isinstance(at, datetime):
        raise ValueError("추천 시각은 날짜와 시간을 포함해야 합니다.")
    if timetable is not None:
        academic_summary = academic.explicit_summary(academic.local_date(at), provided=True)
    elif auto_timetable:
        snapshot = (timetable_reader or academic.read_timetable)(academic.local_date(at))
        timetable = snapshot.require_usable()
        academic_summary = snapshot.summary
    else:
        academic_summary = academic.explicit_summary(academic.local_date(at), provided=False)
    selection = resolve_context(timetable, at=at, origin=origin, next_room=next_room,
                                next_start=next_start, no_next_class=no_next_class,
                                buildings=buildings, rain=rain, exam_period=exam_period)
    settings = load_settings()
    selected_router = (EstimateRouter() if route_mode == "estimate" else
                       (router if router is not None else load_router(settings.max_snap_m)))
    places = load_places(include_samples=include_samples) if places is None else places
    counts = catalog_counts(places)
    result = rank(places, selection.context, filters, taste, settings, router=selected_router)
    payload = result.to_dict()
    candidates = result.top + result.more + result.tight + result.cafeteria
    routes = [r for c in candidates for r in (c.timing.route_to, c.timing.route_back) if r is not None]
    fallbacks = [r for r in routes if r.source != "osm"]
    warnings = list(dict.fromkeys(academic_summary["warnings"] + list(selection.warnings)))
    if not places:
        warnings.append("추천용 식당 JSON이 없습니다. 현장 조사 데이터를 추가하거나 샘플 모드를 선택하세요.")
    elif counts["active"] == 0 and counts["availabilityExcluded"]:
        warnings.append("지도에서 동일 주소의 전남대 주변 식당으로 확인된 후보가 아직 없어 추천에 포함되지 않습니다.")
    elif counts["needsSurvey"] and counts["ready"] == 0:
        if any(p.get("research") for p in places):
            warnings.append(f"실제 식당 {counts['active']:,}곳의 공개 자료를 조사했습니다. 메뉴·가격과 영업시간이 모두 확인된 식당이 없어 추천에 포함되지 않습니다. 목록에서 확인값과 미확인 항목을 볼 수 있습니다.")
        else:
            warnings.append(f"실제 식당 {counts['active']:,}곳이 연결되어 있지만 메뉴·영업시간 조사가 아직 없어 추천에 포함되지 않습니다.")
    if include_samples:
        warnings.append("샘플 식당은 개발용 가짜 데이터입니다. 각 구간의 경로 출처는 카드에 표시합니다.")
    if any(not c.place.get("attrs", {}).get("mealType") for c in candidates):
        warnings.append(f"식사 소요 시간이 따로 확인되지 않은 곳은 기본 {settings.meal_types['일반'].eat}분과 시간대별 예상 대기를 적용합니다.")
    if fallbacks and route_mode == "osm":
        warnings.append("일부 구간의 OSM 경로를 찾지 못해 직선거리 추정으로 대체했습니다. 카드의 경로 출처를 확인하세요.")
    return {**payload, "catalog": counts, "context": selection.to_dict(), "routeMode": route_mode,
            "sampleMode": include_samples, "warnings": warnings, "academic": academic_summary,
            "routing": {"osmLegs": len(routes) - len(fallbacks), "estimatedLegs": len(fallbacks),
                        "attribution": "© OpenStreetMap contributors",
                        "licenseUrl": "https://www.openstreetmap.org/copyright",
                        "note": "건물 좌표와 보행망 사이 연결 구간은 추정입니다. 실내 이동·신호 대기·실시간 통제는 포함하지 않습니다."}}
