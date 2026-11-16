"""가게 데이터 읽기 · 검증 · 가격대 계산.

가게 한 곳 = 파일 하나 (data/places/p_001.json, 개발용 샘플은 data/samples/s_01.json).
형식은 data/schema/place.schema.json 이 기준이고, 스키마로 표현하기 어려운 규칙(시간 순서 등)은 여기서 검사한다.
"""

from __future__ import annotations

import json
import copy
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from statistics import median

from jsonschema import Draft202012Validator, FormatChecker

from .availability import availability_verified, candidate_active, fresh_store_menu_observation, fresh_store_observations, fresh_hours_observation
from .settings import to_min

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SCHEMA_PATH = DATA_DIR / "schema" / "place.schema.json"
PLACES_DIR = DATA_DIR / "places"
SAMPLES_DIR = DATA_DIR / "samples"

# 광주 캠퍼스 대략 중심 ⏳ — 건물 마스터가 생기면 캠퍼스 경계 기준으로 바꾼다
CAMPUS_CENTER = (35.1765, 126.9095)
FAR_WARN_M = 1500

# 가격대 상한(원) ⏳ — 기능 정의서 CA1 7절: A 7천 이하 · B 1만 이하 · C 1.5만 이하 · D 그 위
PRICE_BANDS = (("A", 7000), ("B", 10000), ("C", 15000))


@dataclass
class Issue:
    level: str  # "error" | "warn"
    file: str
    message: str

    def __str__(self) -> str:
        mark = "오류" if self.level == "error" else "경고"
        return f"[{mark}] {self.file}: {self.message}"


@dataclass
class CheckResult:
    places: list[dict] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]


# ── 계산 도우미 ─────────────────────────────────────────────

def recommendation_ready(place: dict) -> bool:
    """메뉴·가격과 요일별 영업시간이 확인된 가게를 추천 대상으로 삼는다."""
    attrs = place.get("attrs") or {}
    if not candidate_active(place) or not effective_hours(place) or not meal_menus(place):
        return False
    if fresh_store_observations(place):
        return True
    research = place.get("research")
    if research and research.get("identityStatus") == "closed":
        return False
    # 웹 추가 조사에서 값을 못 찾았어도 기존 사람 검수 결과를 무효화하지 않는다.
    if attrs.get("status") != "partial" and attrs.get("verifiedAt") and attrs.get("verifiedBy"):
        return True
    if research:
        return (research.get("identityStatus") == "matched" and all(
            research.get("facts", {}).get(field, {}).get("status") == "confirmed"
            for field in ("menus", "hours")
        ))
    return False


def meal_type_for_timing(place: dict) -> str:
    """미조사 식사유형은 저장하지 않고 계산에만 설정의 일반 식사 시간을 쓴다."""
    return place.get("attrs", {}).get("mealType", "일반")


def rep_price(place: dict) -> int | None:
    """현재 적용할 매장 대표 메뉴 가격의 중앙값. 원본 attrs는 수정하지 않는다."""
    prices = [m["price"] for m in meal_menus(place)]
    return int(median(prices)) if prices else None


def effective_menus(place: dict) -> list[dict]:
    """검증된 최신 매장 관찰을 우선하되 배달·미확인·0원 메뉴는 계산에서 뺀다.

    반환값은 독립된 사본이다. 관찰 원문과 과거 attrs는 상세 API에서 별도로 보존한다.
    """
    current = fresh_store_menu_observation(place)
    menus = (place["menuObservation"]["menus"] if current else place.get("attrs", {}).get("menus", []))
    return [{"name": m["name"], "price": m["price"], "main": m.get("main") is True,
             **({"orderWindow": copy.deepcopy(m["orderWindow"])} if m.get("orderWindow") else {})}
            for m in menus if isinstance(m, dict) and type(m.get("price")) is int and m["price"] > 0
            and (not current or m.get("priceContext", "in_store") == "in_store")]


def meal_menus(place: dict) -> list[dict]:
    """예산·대표 가격 계산에 쓸, 가격이 확인된 주 식사 메뉴만 돌려준다."""
    return [m for m in effective_menus(place) if m["main"]]


def effective_price_source(place: dict) -> dict:
    if fresh_store_menu_observation(place):
        observation = place["menuObservation"]
        kind = "kakao_in_store" if observation["sourceUrl"].startswith("https://place.map.kakao.com/") else "naver_in_store"
        if observation["sourceUrl"].startswith("https://blog.naver.com/"):
            kind = "naver_blog_in_store"
        return {"kind": kind, "priceContext": "in_store",
                "sourceUrl": observation["sourceUrl"], "checkedAt": observation["checkedAt"]}
    return {"kind": "attrs", "priceContext": "legacy", "sourceUrl": None,
            "checkedAt": place.get("attrs", {}).get("verifiedAt")}


def effective_hours(place: dict) -> list[dict]:
    observation = place.get("hoursObservation", {})
    hours = observation.get("hours", []) if fresh_hours_observation(place) else place.get("attrs", {}).get("hours", [])
    return copy.deepcopy(hours)


def effective_closed_dates(place: dict) -> list[str]:
    if not fresh_hours_observation(place):
        return []
    return list(place["hoursObservation"].get("closedDates", []))


def price_band(price: int | None) -> str | None:
    if price is None:
        return None
    for band, upper in PRICE_BANDS:
        if price <= upper:
            return band
    return "D"


def price_label(price: int | None) -> str | None:
    """이유 칩 문구: 7500 → '7천 원대', 14000 → '1만 원대'."""
    if price is None:
        return None
    if price < 10000:
        return f"{price // 1000}천 원대"
    return f"{price // 10000}만 원대"


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """두 좌표 사이 직선거리(m, haversine)."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ── 검증 ────────────────────────────────────────────────────

def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _hours_errors(hours: list[dict]) -> list[str]:
    out = []
    by_day: dict[str, list[tuple[int, int]]] = {}
    for idx, h in enumerate(hours, 1):
        o, c = to_min(h["open"]), to_min(h["close"])
        tag = f"hours[{idx}]"
        if o >= c:
            out.append(f"{tag} open {h['open']} 이 close {h['close']} 보다 늦습니다 (자정 넘김은 아직 지원하지 않음)")
            continue
        if "break" in h:
            bs, be = to_min(h["break"]["start"]), to_min(h["break"]["end"])
            if not (o < bs < be < c):
                out.append(f"{tag} 브레이크 {h['break']['start']}~{h['break']['end']} 가 영업시간 안에 있지 않습니다")
        if "lastOrder" in h:
            lo = to_min(h["lastOrder"])
            # 자정 라스트오더를 익일 00:00~마감 구간으로 나눈 관찰은 그대로 표현한다.
            if not (o < lo <= c or o == lo == 0):
                out.append(f"{tag} lastOrder {h['lastOrder']} 가 영업시간 안에 있지 않습니다")
            elif "break" in h and lo <= to_min(h["break"]["end"]):
                out.append(f"{tag} lastOrder {h['lastOrder']} 는 브레이크 종료 후여야 합니다. 점심·저녁 라스트오더는 hours 행을 나눠 기록하세요")
        for day in h["days"]:
            by_day.setdefault(day, []).append((o, c))
    for day, spans in by_day.items():
        spans.sort()
        for (_, c1), (o2, _) in zip(spans, spans[1:]):
            if o2 < c1:
                out.append(f"{day}요일 영업 구간이 겹칩니다")
    return out


def _check_rules(place: dict, fname: str, is_sample_dir: bool, today: date) -> list[Issue]:
    out: list[Issue] = []

    def err(msg: str) -> None:
        out.append(Issue("error", fname, msg))

    def warn(msg: str) -> None:
        out.append(Issue("warn", fname, msg))

    pid = place["id"]
    if Path(fname).name != f"{pid}.json":
        err(f"파일 이름이 id 와 다릅니다 (id={pid})")

    is_sample = place["source"] == "sample"
    if pid.startswith("s_") != is_sample:
        err("s_ 아이디와 source=sample 은 함께 써야 합니다")
    if is_sample != is_sample_dir:
        err("샘플은 data/samples/ 에만, 실제 가게는 data/places/ 에만 둡니다")
    if place["source"] == "semas":
        source_id = place.get("sourceId", "")
        if not source_id.strip():
            err("source=semas 이면 sourceId(상가업소번호)가 필요합니다")
        elif source_id != source_id.strip():
            err("sourceId(상가업소번호) 앞뒤 공백을 제거하세요")

    # JSON 파서는 NaN을 읽으며 JSON Schema 범위 검사만으로는 걸러지지 않는다.
    try:
        finite = all(math.isfinite(place[key]) for key in ("lat", "lng"))
    except (OverflowError, TypeError):
        finite = False
    if not finite:
        err("lat/lng 좌표는 유한한 숫자여야 합니다")
        return out

    if place.get("sourceStatus", "active") == "missing":
        warn("후보 목록에서 빠짐 → 재확인 전 추천에서 제외합니다(폐업 확정 아님)")

    for field in ("availability", "menuObservation", "hoursObservation"):
        observation = place.get(field)
        if observation and date.fromisoformat(observation["checkedAt"]) > today:
            err(f"{field}.checkedAt이 미래입니다")
    if "availability" in place and not availability_verified(place):
        warn("지도 재확인에서 제외·미확인 → 기본 식당 목록과 추천에서 제외합니다(폐업 판정과 구분)")
    menu_observation = place.get("menuObservation", {})
    if menu_observation.get("status") == "confirmed" and not any(
        type(menu.get("price")) is int and menu["price"] > 0 for menu in menu_observation.get("menus", [])
    ):
        err("menuObservation 확인됨에는 가격이 확인된 매장 메뉴가 필요합니다")
    for message in _hours_errors(place.get("hoursObservation", {}).get("hours", [])):
        err("hoursObservation." + message)
    for menu in menu_observation.get("menus", []):
        window = menu.get("orderWindow")
        if window and to_min(window["start"]) >= to_min(window["end"]):
            err("menuObservation.orderWindow 시작은 종료보다 빨라야 합니다")

    d = distance_m(place["lat"], place["lng"], *CAMPUS_CENTER)
    if d > FAR_WARN_M:
        warn(f"캠퍼스 중심에서 {d:.0f}m — 좌표를 확인하세요")

    research = place.get("research")
    if research:
        source_ids = [s["id"] for s in research["sources"]]
        if len(source_ids) != len(set(source_ids)):
            err("research 출처 id가 중복되었습니다")
        sources = {s["id"]: s for s in research["sources"]}
        if date.fromisoformat(research["checkedAt"]) > today:
            err("research.checkedAt이 미래입니다")
        for key, evidence in research["facts"].items():
            if not set(evidence["sourceIds"]).issubset(sources):
                err(f"research.{key}에 없는 출처 id가 있습니다")
            if evidence["status"] == "confirmed":
                if not evidence.get("value") or not evidence["sourceIds"]:
                    err(f"research.{key} 확인됨에는 값과 출처가 필요합니다")
                if research["identityStatus"] != "matched" or not any(
                    sources.get(sid, {}).get("addressMatch") for sid in evidence["sourceIds"]
                ):
                    err(f"research.{key} 확인됨에는 동일 주소 업소 확인이 필요합니다")
                if key == "hours":
                    for message in _hours_errors(evidence.get("value", [])):
                        err(f"research.{message}")
                elif key == "menus" and evidence.get("value"):
                    menus = evidence["value"]
                    if not any(m["main"] for m in menus) or len({m["name"] for m in menus}) != len(menus):
                        err("research.menus 대표 메뉴가 없거나 이름이 중복됩니다")

    attrs = place.get("attrs")
    if not attrs:
        warn("attrs 없음 → '정보 없음'으로만 보이고 추천하지 않습니다")
        return out

    # 영업시간: 순서 · 브레이크 · 라스트오더 · 같은 요일 구간 겹침
    if attrs.get("status") == "partial":
        if recommendation_ready(place):
            warn("attrs 부분 조사 → 메뉴·가격·영업시간 확인으로 추천 가능, 나머지 정보는 미확인")
        else:
            warn("attrs 부분 조사 → 메뉴·가격·영업시간 확인 전 추천하지 않습니다")
    for message in _hours_errors(attrs.get("hours", [])):
        err(message)

    # 메뉴
    if "menus" in attrs and not any(m["main"] for m in attrs["menus"]):
        err("대표 메뉴(main=true)가 하나도 없습니다 — 가격대를 계산할 수 없습니다")
    names = [m["name"] for m in attrs.get("menus", [])]
    for menu in attrs.get("menus", []):
        window = menu.get("orderWindow")
        if window and to_min(window["start"]) >= to_min(window["end"]):
            err("attrs.menus.orderWindow 시작은 종료보다 빨라야 합니다")
    if len(names) != len(set(names)):
        err("같은 이름의 메뉴가 두 번 있습니다")

    # 검증일
    if "verifiedAt" not in attrs:
        return out
    verified = date.fromisoformat(attrs["verifiedAt"])
    if verified > today:
        err(f"verifiedAt {verified} 이 미래입니다")
    elif (today - verified).days > 90:
        warn(f"검증한 지 {(today - verified).days}일 — 다시 확인하세요")

    return out


def check_dirs(dirs: list[Path] | None = None, today: date | None = None) -> CheckResult:
    """data/places · data/samples 의 모든 파일을 읽어 검사한다."""
    dirs = dirs if dirs is not None else [PLACES_DIR, SAMPLES_DIR]
    today = today or date.today()
    validator = _validator()
    result = CheckResult()
    seen: dict[str, str] = {}
    seen_source: dict[str, str] = {}
    seen_naver: dict[str, str] = {}

    for folder in dirs:
        is_sample_dir = folder.name == "samples"
        for path in sorted(folder.glob("*.json")):
            fname = f"{folder.name}/{path.name}"
            try:
                place = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                result.issues.append(Issue("error", fname, f"JSON 을 읽을 수 없습니다: {e}"))
                continue

            schema_errors = sorted(validator.iter_errors(place), key=lambda e: list(e.path))
            if schema_errors:
                for e in schema_errors:
                    where = "/".join(str(p) for p in e.path) or "(최상위)"
                    result.issues.append(Issue("error", fname, f"형식 {where}: {e.message}"))
                continue

            if place["id"] in seen:
                result.issues.append(Issue("error", fname, f"id {place['id']} 가 {seen[place['id']]} 와 겹칩니다"))
                continue
            seen[place["id"]] = fname

            if place["source"] == "semas" and place.get("sourceId", "").strip():
                source_id = place["sourceId"].strip()
                if source_id in seen_source:
                    result.issues.append(Issue("error", fname, f"sourceId {source_id} 가 {seen_source[source_id]} 와 겹칩니다"))
                    continue
                seen_source[source_id] = fname

            if candidate_active(place) and place.get("availability"):
                naver_id = place["availability"]["naverPlaceId"]
                if naver_id in seen_naver:
                    result.issues.append(Issue("error", fname, f"naverPlaceId {naver_id} 가 {seen_naver[naver_id]} 와 겹칩니다"))
                    continue
                seen_naver[naver_id] = fname

            result.issues.extend(_check_rules(place, fname, is_sample_dir, today))
            result.places.append(place)

    return result


def load_places(include_samples: bool = False) -> list[dict]:
    """추천 계산에 쓸 가게 목록. 검증 오류가 있으면 예외를 던진다."""
    dirs = [PLACES_DIR] + ([SAMPLES_DIR] if include_samples else [])
    result = check_dirs(dirs)
    if result.errors:
        raise ValueError("가게 데이터 오류:\n" + "\n".join(map(str, result.errors)))
    return result.places
