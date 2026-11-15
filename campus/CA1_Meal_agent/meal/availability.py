"""지도 재확인 상태를 공공 후보 목록 존재 여부·메뉴 조사와 구분한다."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit


DISTRICTS = frozenset({"후문", "상대", "정문", "예대"})


def availability_verified(place: dict) -> bool:
    """재검증 기록이 있으면 동일 주소·근처 식당으로 확인된 곳만 허용한다.

    필드가 없는 기존·샘플 레코드는 호환한다. 조사 대상 전체에 unconfirmed를
    명시하면 미확인 곳은 즉시 보류되며 sourceStatus 복구로 되살아나지 않는다.
    네이버 등록 확인은 지금 이 순간 영업 중이라는 뜻이 아니다.
    """
    if "availability" not in place:
        return True
    evidence = place.get("availability")
    if not isinstance(evidence, dict):
        return False
    naver_id = evidence.get("naverPlaceId")
    return (
        evidence.get("status") == "listed"
        and evidence.get("provider") == "naver"
        and evidence.get("addressVerified") is True
        and evidence.get("nearby") is True
        and evidence.get("district") in DISTRICTS
        and isinstance(naver_id, str)
        and re.fullmatch(r"[1-9][0-9]*", naver_id) is not None
        and isinstance(evidence.get("observedName"), str)
        and bool(evidence["observedName"].strip())
        and isinstance(evidence.get("observedAddress"), str)
        and bool(evidence["observedAddress"].strip())
    )


def candidate_active(place: dict) -> bool:
    """기본 목록과 추천에서 쓸 수 있는 후보. 메뉴·시간 준비 여부는 별도다."""
    return place.get("sourceStatus", "active") == "active" and availability_verified(place)


def _same_naver_place(url: object, naver_id: str) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.hostname not in {
        "map.naver.com", "m.map.naver.com", "place.naver.com", "m.place.naver.com", "pcmap.place.naver.com"
    }:
        return False
    matches = re.findall(r"/(?:restaurant|place)/([1-9][0-9]*)(?:/|$)", parsed.path)
    return matches == [naver_id]


def _same_naver_order_source(observation: dict, naver_id: str) -> bool:
    """주문 메뉴 출처는 같은 지점의 플레이스 출처가 함께 있을 때만 연결한다."""
    if not _same_naver_place(observation.get("placeSourceUrl"), naver_id):
        return False
    try:
        url = urlsplit(observation.get("sourceUrl", ""))
    except (TypeError, ValueError):
        return False
    return (url.scheme == "https" and url.hostname == "m.booking.naver.com"
            and re.fullmatch(r"/order/bizes/[1-9][0-9]*/items/[1-9][0-9]*/?", url.path) is not None)


def _same_kakao_place_source(place: dict, observation: dict) -> bool:
    """카카오 보완 출처는 같은 네이버 지점과 도로명 건물번호를 함께 대조한다."""
    if not _same_naver_place(observation.get("placeSourceUrl"), place["availability"]["naverPlaceId"]):
        return False
    try:
        url = urlsplit(observation.get("sourceUrl", ""))
    except (TypeError, ValueError):
        return False
    if not (url.scheme == "https" and url.hostname == "place.map.kakao.com"
            and re.fullmatch(r"/[1-9][0-9]*/?", url.path)):
        return False
    return _same_source_address(place, observation)


def _same_source_address(place: dict, observation: dict) -> bool:
    """보완 자료의 주소가 재확인한 지점의 도로명·건물번호와 같은지 검사한다."""

    def address_key(value):
        if not isinstance(value, str) or "광주" not in value:
            return None
        match = re.search(r"북구\s+([가-힣0-9·]+(?:로|길))\s+([1-9][0-9]*(?:-[0-9]+)?)(?=\s|$)", value)
        return match.groups() if match else None

    observed = address_key(observation.get("sourceAddress"))
    identity = address_key(place["availability"].get("observedAddress"))
    return observed is not None and observed == identity


def _same_naver_blog_source(place: dict, observation: dict) -> bool:
    """같은 지점·주소의 최근 매장 방문 메뉴판만 보완 가격으로 연결한다."""
    if not _same_naver_place(observation.get("placeSourceUrl"), place["availability"]["naverPlaceId"]):
        return False
    try:
        url = urlsplit(observation.get("sourceUrl", ""))
        published = date.fromisoformat(observation["sourcePublishedAt"])
        checked = date.fromisoformat(observation["checkedAt"])
    except (KeyError, TypeError, ValueError):
        return False
    return (url.scheme == "https" and url.hostname == "blog.naver.com" and url.username is None
            and re.fullmatch(r"/[A-Za-z0-9_-]+/[1-9][0-9]*/?", url.path) is not None
            and timedelta(0) <= checked - published <= timedelta(days=180)
            and _same_source_address(place, observation))


def _fresh_observation(place: dict, observation: object, *, allow_kakao: bool = False, allow_blog: bool = False) -> bool:
    if not place.get("availability") or not availability_verified(place):
        return False
    if not isinstance(observation, dict) or observation.get("status") != "confirmed":
        return False
    availability = place["availability"]
    try:
        observed_at = date.fromisoformat(observation["checkedAt"])
        identity_at = date.fromisoformat(availability["checkedAt"])
        historical_at = date.fromisoformat(place.get("research", {}).get("checkedAt", availability["checkedAt"]))
        verified_at = date.fromisoformat(place.get("attrs", {}).get("verifiedAt", availability["checkedAt"]))
    except (KeyError, TypeError, ValueError):
        return False
    today = datetime.now(timezone(timedelta(hours=9))).date()
    return (max(identity_at, historical_at, verified_at) <= observed_at <= today
            and (_same_naver_place(observation.get("sourceUrl"), availability["naverPlaceId"])
                 or _same_naver_order_source(observation, availability["naverPlaceId"])
                 or (allow_kakao and _same_kakao_place_source(place, observation))
                 or (allow_blog and _same_naver_blog_source(place, observation))))


def fresh_store_menu_observation(place: dict) -> bool:
    """같은 지점의 최신 매장 메뉴 관찰만 기존 저장 가격보다 우선한다."""
    menu = place.get("menuObservation")
    if not _fresh_observation(place, menu, allow_kakao=True, allow_blog=True) or menu.get("priceContext") != "in_store":
        return False
    return any(type(m.get("price")) is int and m["price"] > 0
               and m.get("priceContext", "in_store") == "in_store"
               for m in menu.get("menus", []) if isinstance(m, dict))


def fresh_store_observations(place: dict) -> bool:
    """동일 지점의 최신 매장 메뉴·시간 증거는 과거 research 충돌과 구분한다."""
    return fresh_store_menu_observation(place) and fresh_hours_observation(place)


def fresh_hours_observation(place: dict) -> bool:
    """동일 지점에서 확인한 최신 영업시간. 메뉴 가격 맥락과는 독립적이다."""
    hours = place.get("hoursObservation")
    if not isinstance(hours, dict) or hours.get("hoursContext", "in_store") != "in_store":
        return False
    return _fresh_observation(place, hours, allow_kakao=hours.get("hoursContext") == "in_store") and bool(hours.get("hours"))
