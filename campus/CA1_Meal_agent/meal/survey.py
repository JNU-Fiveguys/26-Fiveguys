"""직접 조사 입력함. 추천 데이터와 분리해 저장하고, 후속 검토에 사용한다."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .availability import candidate_active
from .import_places import ImportPlacesError, _atomic_write, _encoded, _lock
from .places import DATA_DIR, PLACES_DIR, effective_hours, meal_menus, load_places, recommendation_ready
from .timetable import SEOUL

ENTRIES_DIR = DATA_DIR / "survey" / "user_entries"
DAYS = list("월화수목금토일")
Day = Literal["월", "화", "수", "목", "금", "토", "일"]
TIME = r"^(?:[01]\d|2[0-3]):[0-5]\d$"
CLOSE_TIME = r"^(?:(?:[01]\d|2[0-3]):[0-5]\d|24:00)$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)


class MenuEntry(StrictModel):
    name: str = Field(min_length=1, max_length=40)
    price: int | None = Field(None, ge=1, le=200000, strict=True)
    main: bool = False
    note: str = Field("", max_length=500)


class HourEntry(StrictModel):
    days: list[Day] = Field(min_length=1, max_length=7)
    open: str = Field(pattern=TIME)
    close: str = Field(pattern=CLOSE_TIME)
    last_order: str | None = Field(None, alias="lastOrder", pattern=TIME)
    break_start: str | None = Field(None, alias="breakStart", pattern=TIME)
    break_end: str | None = Field(None, alias="breakEnd", pattern=TIME)

    @model_validator(mode="after")
    def time_fields(self):
        if len(set(self.days)) != len(self.days):
            raise ValueError("영업 요일이 중복되었습니다.")
        if self.open == self.close:
            raise ValueError("개점·마감이 같으면 24시간 영업 여부를 메모에 적어 주세요.")
        if bool(self.break_start) != bool(self.break_end):
            raise ValueError("브레이크 시작과 종료를 함께 입력하세요.")
        return self


class SurveyEntry(StrictModel):
    expected_revision: int = Field(alias="expectedRevision", ge=0, strict=True)
    checked_at: date | None = Field(None, alias="checkedAt")
    method: Literal["unknown", "store_menu", "visit", "phone", "official", "map"] = "unknown"
    price_context: Literal["unknown", "in_store", "delivery"] = Field("unknown", alias="priceContext")
    menus: list[MenuEntry] = Field(default_factory=list, max_length=250)
    menus_confirmed: bool = Field(False, alias="menusConfirmed")
    hours: list[HourEntry] = Field(default_factory=list, max_length=28)
    closed_days: list[Day] = Field(default_factory=list, alias="closedDays", max_length=7)
    hours_confirmed: bool = Field(False, alias="hoursConfirmed")
    hours_note: str = Field("", alias="hoursNote", max_length=3000)
    sources: list[str] = Field(default_factory=list, max_length=12)
    notes: str = Field("", max_length=5000)

    @field_validator("sources")
    @classmethod
    def public_urls(cls, values):
        for value in values:
            try:
                parsed = urlsplit(value)
                valid = (len(value) <= 2000 and parsed.scheme in {"http", "https"}
                         and parsed.hostname and not parsed.username and not parsed.password
                         and not any(c.isspace() for c in value))
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("출처는 로그인 정보가 없는 http/https 링크로 입력하세요.")
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def confirmation(self):
        if self.checked_at and self.checked_at > datetime.now(SEOUL).date():
            raise ValueError("확인일은 미래 날짜일 수 없습니다.")
        if self.menus_confirmed:
            if self.price_context != "in_store" or not any(m.main and m.price for m in self.menus):
                raise ValueError("매장 가격 확인에는 가격이 있는 주 식사 메뉴와 매장 가격 구분이 필요합니다.")
        if self.hours_confirmed:
            opened = {d for h in self.hours for d in h.days}
            closed = set(self.closed_days)
            if not self.hours or opened & closed or opened | closed != set(DAYS):
                raise ValueError("요일별 시간 확인에는 7일의 영업 또는 휴무를 빠짐없이 구분해야 합니다.")
        if self.menus_confirmed or self.hours_confirmed:
            if not self.checked_at or self.method == "unknown":
                raise ValueError("확인 항목을 체크하려면 확인일과 확인 방법을 입력하세요.")
            if self.method in {"store_menu", "official", "map"} and not self.sources:
                raise ValueError("게시된 자료를 확인했다면 출처 링크를 입력하세요.")
            if self.method in {"visit", "phone"} and not self.notes:
                raise ValueError("방문·전화 확인 내용은 메모에 남겨 주세요.")
        return self


class RevisionConflict(ValueError):
    pass


def _entry_path(pid: str, entries_dir: Path | None = None) -> Path:
    if not re.fullmatch(r"p_[0-9]{4,}", pid):
        raise KeyError("식당 ID가 올바르지 않습니다.")
    return (entries_dir if entries_dir is not None else ENTRIES_DIR) / f"{pid}.json"


def read_entry(pid: str, entries_dir: Path | None = None) -> dict | None:
    path = _entry_path(pid, entries_dir)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def missing_info(place: dict) -> list[dict]:
    missing = []
    m = place.get("menuObservation", {})
    if not meal_menus(place):
        if m.get("priceContext") != "in_store":
            label, detail = "매장 메뉴·가격", "배달 가격 또는 가격 구분이 불명확한 자료만 있어 매장 가격을 확인해야 합니다."
        elif any(x.get("main") and x.get("price") is None for x in m.get("menus", [])):
            label, detail = "식사 메뉴의 실제 가격", "주 식사 메뉴가 ‘가격 변동’으로 게시되어 실제 매장 가격이 필요합니다."
        elif not any(x.get("main") for x in m.get("menus", [])):
            label, detail = "식사 메뉴·분량 기준", "여러 명용 메뉴·사이드 등의 게시 가격만으로 1인 식사 가격을 확정할 수 없습니다."
            if place["id"] == "p_0159":
                detail = "튀김 1,500원만 확인되어, 한 끼 식사가 되는 메뉴와 그 가격을 확인해야 합니다."
        else:
            label, detail = "매장 메뉴·가격", "추천에 쓸 수 있는 확인된 주 식사 메뉴·가격이 필요합니다."
        missing.append({"key": "menus", "label": label, "detail": detail})
    if not effective_hours(place):
        missing.append({"key": "hours", "label": "요일별 영업시간", "detail": "월~일 영업·휴무와 개점·마감 시간이 필요합니다. 브레이크·마지막 주문은 확인한 경우만 입력하세요."})
    return missing


def _summary(place: dict, entries_dir: Path | None = None) -> dict:
    draft = read_entry(place["id"], entries_dir)
    missing = missing_info(place)
    complete = bool(draft) and all(draft["entry"].get(k["key"] + "Confirmed") for k in missing)
    return {"id": place["id"], "name": place["name"], "address": place.get("address", ""),
            "district": place.get("availability", {}).get("district"), "missing": missing,
            "reasons": place.get("followupReview", {}).get("reasons", []),
            "mapUrl": f"https://pcmap.place.naver.com/restaurant/{place['availability']['naverPlaceId']}/home",
            "savedAt": draft["savedAt"] if draft else None, "revision": draft["revision"] if draft else 0,
            "entryStatus": "review" if complete else "partial" if draft else "empty"}


def queue(*, entries_dir: Path | None = None) -> dict:
    rows = [_summary(p, entries_dir) for p in load_places() if p["source"] != "sample" and candidate_active(p) and not recommendation_ready(p)]
    rows.sort(key=lambda p: (p["district"], p["name"]))
    return {"places": rows, "counts": {"total": len(rows),
            "menus": sum(any(m["key"] == "menus" for m in p["missing"]) for p in rows),
            "hours": sum(any(m["key"] == "hours" for m in p["missing"]) for p in rows),
            "saved": sum(p["revision"] > 0 for p in rows)}}


def detail(pid: str, *, entries_dir: Path | None = None) -> dict:
    _entry_path(pid, entries_dir)
    place = next((p for p in load_places() if p["id"] == pid and p["source"] != "sample" and candidate_active(p)), None)
    if place is None:
        raise KeyError("활성 식당 목록에서 찾을 수 없습니다.")
    return {"place": {**_summary(place, entries_dir), "menuObservation": place.get("menuObservation"),
                      "hoursObservation": place.get("hoursObservation"), "effectiveHours": effective_hours(place)},
            "draft": read_entry(pid, entries_dir)}


def save(pid: str, body: SurveyEntry, *, entries_dir: Path | None = None) -> dict:
    path = _entry_path(pid, entries_dir)
    record = detail(pid, entries_dir=entries_dir)["place"]
    base_path = PLACES_DIR / (pid + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _lock(path.parent / ".save.lock"):
            previous = read_entry(pid, entries_dir)
            revision = previous["revision"] if previous else 0
            if body.expected_revision != revision:
                raise RevisionConflict("다른 창에서 저장한 내용이 있습니다. 입력 내용을 복사해 둔 뒤 새로고침하세요.")
            result = {"version": 1, "placeId": pid, "placeName": record["name"], "status": "draft",
                      "revision": revision + 1, "savedAt": datetime.now(SEOUL).isoformat(timespec="seconds"),
                      "baseSnapshotHash": hashlib.sha256(base_path.read_bytes()).hexdigest(),
                      "entry": body.model_dump(mode="json", by_alias=True, exclude={"expected_revision"})}
            _atomic_write(path, _encoded(result))
    except ImportPlacesError as exc:
        raise RevisionConflict("다른 저장이 진행 중입니다. 잠시 후 다시 저장하세요.") from exc
    return result
