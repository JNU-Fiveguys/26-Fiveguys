"""계산 기본값 읽기 — data/settings.json."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

SETTINGS_PATH = Path(__file__).resolve().parent.parent / "data" / "settings.json"
SETTINGS_SCHEMA_PATH = SETTINGS_PATH.parent / "schema" / "settings.schema.json"


def _check_finite(value, path: str = "settings") -> None:
    """JSON 로더가 허용하는 NaN/Infinity도 계산 설정에서는 거부한다."""
    if isinstance(value, dict):
        for key, item in value.items():
            _check_finite(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _check_finite(item, f"{path}[{index}]")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError(f"설정 오류 {path}: 유한한 숫자여야 합니다")


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = json.loads(SETTINGS_SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def validate_settings(data: dict) -> None:
    """형식·범위와 분모·정규화·피크 시각의 계산 전제를 확인한다."""
    _check_finite(data)
    errors = sorted(_validator().iter_errors(data), key=lambda error: error.json_path)
    if errors:
        raise ValueError("설정 오류:\n" + "\n".join(f"{e.json_path}: {e.message}" for e in errors))

    weights = data["ranking"]["weights"]
    for label, multiplier in (("맑음", 1), ("비", data["ranking"]["rainDistanceMultiplier"])):
        total = sum(value * (multiplier if key == "distance" else 1) for key, value in weights.items())
        try:
            finite = math.isfinite(total)
        except OverflowError:
            finite = False
        if not finite or total <= 0:
            raise ValueError(f"설정 오류 ranking.weights: {label} 가중치 합은 유한한 양수여야 합니다")
    for index, (start, end) in enumerate(data["peaks"]):
        if to_min(start) >= to_min(end):
            raise ValueError(f"설정 오류 peaks[{index}]: 시작 시각이 끝 시각보다 빨라야 합니다")


def to_min(hhmm: str) -> int:
    """'11:50' → 710 (자정부터 분)."""
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def to_hhmm(minutes: int) -> str:
    """710 → '11:50'."""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


@dataclass(frozen=True)
class MealTypeTimes:
    eat: int
    wait: int
    wait_peak: int


@dataclass(frozen=True)
class RankingSettings:
    weights: dict[str, float]
    rain_distance_multiplier: float
    slack_full_min: int
    distance_zero_min: int  # 편도당 기준. 다음 건물이 있으면 전체 도보를 이 값의 2배와 비교
    over_budget_one_band: float
    bonus_new_place: float
    penalty_recent: float
    bonus_exam_quick: float
    chip_min_factor: float

    @classmethod
    def from_dict(cls, r: dict) -> "RankingSettings":
        return cls(
            weights={k: float(v) for k, v in r["weights"].items()},
            rain_distance_multiplier=float(r["rainDistanceMultiplier"]),
            slack_full_min=int(r["slackFullMin"]),
            distance_zero_min=int(r["distanceZeroMin"]),
            over_budget_one_band=float(r["overBudgetOneBand"]),
            bonus_new_place=float(r["bonusNewPlace"]),
            penalty_recent=float(r["penaltyRecent"]),
            bonus_exam_quick=float(r["bonusExamQuick"]),
            chip_min_factor=float(r["chipMinFactor"]),
        )


@dataclass(frozen=True)
class Settings:
    detour_factor: float
    meters_per_min: float
    buffer_min: int
    tight_margin_min: int
    meal_types: dict[str, MealTypeTimes]
    peaks: tuple[tuple[int, int], ...]  # (시작 분, 끝 분) — 시작 포함, 끝 미포함
    quick_eat_max_min: int              # 이 이하 식사 시간 유형 = '빠른 식사'
    ranking: RankingSettings
    max_snap_m: float = 150.0  # OSM 보행망에 연결할 수 있는 최대 거리

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        validate_settings(d)
        return cls(
            detour_factor=float(d["walk"]["detourFactor"]),
            meters_per_min=float(d["walk"]["metersPerMin"]),
            buffer_min=int(d["bufferMin"]),
            tight_margin_min=int(d["tightMarginMin"]),
            meal_types={
                k: MealTypeTimes(eat=int(v["eat"]), wait=int(v["wait"]), wait_peak=int(v["waitPeak"]))
                for k, v in d["mealTypes"].items()
            },
            peaks=tuple((to_min(a), to_min(b)) for a, b in d["peaks"]),
            quick_eat_max_min=int(d["quickEatMaxMin"]),
            ranking=RankingSettings.from_dict(d["ranking"]),
            max_snap_m=float(d["walk"].get("maxSnapM", 150)),
        )


@lru_cache(maxsize=1)
def load_settings(path: Path = SETTINGS_PATH) -> Settings:
    return Settings.from_dict(json.loads(path.read_text(encoding="utf-8")))
