"""현재 공통 판정하에서 이전 가중치·편도 점수 정책과 현재 정책을 비교한다.

CA1_Meal_agent 에서 실행: python -m eval.compare_ranking
이전 점수 가중치는 baseline_settings.json 에 고정한다. 모든 변형은 현재 후보
판정/시간 계산/정렬을 공유하며, 좌석·혼밥을 사용하던 과거 전체 동작의 재현은 아니다.
비교에서만 이전 편도 거리 점수식을 복원한다.
실제 식당·강의실·도로 경로를 검증하는 평가가 아니라 합성 샘플 회귀 비교다.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path

from meal.places import SAMPLES_DIR, check_dirs
from meal.ranking import (
    Candidate, Filters, MealContext, RankResult, TasteSummary,
    _candidate_order, _diverse_top, _weights, rank,
)
from meal.settings import SETTINGS_PATH, Settings, to_hhmm, to_min
from meal.routing import EstimateRouter

EVAL_DIR = Path(__file__).resolve().parent
BASELINE_PATH = EVAL_DIR / "baseline_settings.json"
VALIDATION_DATE = date(2026, 11, 11)
ORIGIN = (35.1765, 126.9095)
NEXT_BUILDING = (35.1740, 126.9055)  # 비교용 가상 좌표; 실제 강의실 이름을 부여하지 않는다.
VARIANTS = ("before", "crowdOnly", "after")


def scenarios() -> list[tuple[str, str, MealContext]]:
    base = MealContext(weekday=2, start=to_min("11:50"), end=to_min("13:30"),
                       origin=ORIGIN, dest=ORIGIN)
    other = replace(base, dest=NEXT_BUILDING)
    return [
        ("same_building", "같은 건물로 복귀", base),
        ("different_building", "남서쪽 가상 다음 건물로 이동", other),
        ("different_building_rain", "남서쪽 가상 다음 건물로 이동 · 비", replace(other, rain=True)),
        ("no_next_class", "다음 강의 없음", replace(base, end=None, dest=None)),
    ]


def legacy_rank(places: list[dict], ctx: MealContext, s: Settings) -> RankResult:
    """공통 판정 결과를 기존 편도 점수로 재평가한다. 이유 칩은 비교 대상이 아니다.

    private helper 사용은 이 오프라인 평가에 한정한다. 운영 정렬·다양성 규칙을
    복제하지 않아 순위 차이가 두 점수 정책의 변경만으로 생기게 한다.
    """
    result = rank(places, ctx, Filters(), TasteSummary(), s, router=EstimateRouter())
    weights = _weights(ctx, s)

    def rescore(c: Candidate) -> Candidate:
        features = dict(c.features)
        features["distance"] = max(0.0, 1 - c.timing.breakdown.walk_to / s.ranking.distance_zero_min)
        contributions = {key: weights[key] * features[key] for key in weights}
        bonus = c.score - sum(c.contributions.values())
        return replace(c, features=features, contributions=contributions,
                       score=sum(contributions.values()) + bonus, reasons=[])

    ok = sorted((rescore(c) for c in result.top + result.more), key=_candidate_order)
    top, more = _diverse_top(ok)
    return RankResult(
        top=top, more=more,
        tight=sorted((rescore(c) for c in result.tight), key=_candidate_order),
        cafeteria=sorted((rescore(c) for c in result.cafeteria), key=_candidate_order),
        excluded=result.excluded,
    )


def candidates(result: RankResult) -> dict[str, tuple[str, Candidate]]:
    return {c.place_id: (group, c) for group in ("top", "more", "tight", "cafeteria")
            for c in getattr(result, group)}


def compare_scenario(sid: str, label: str, ctx: MealContext, places: list[dict],
                     baseline: Settings, crowd_only: Settings, current: Settings) -> dict:
    results = {
        "before": legacy_rank(places, ctx, baseline),
        "crowdOnly": legacy_rank(places, ctx, crowd_only),
        "after": rank(places, ctx, Filters(), TasteSummary(), current, router=EstimateRouter()),
    }
    indexed = {key: candidates(result) for key, result in results.items()}
    # 이 비교는 점수 변경만을 대상으로 한다. 추후 시간/제외 정책까지 바뀌면
    # 서로 다른 후보 집합으로 점수 원인을 혼동하지 않도록 명시적으로 중단한다.
    if any(set(indexed[key]) != set(indexed["before"]) or
           results[key].excluded != results["before"].excluded for key in VARIANTS):
        raise ValueError("기존/현재 후보 판정이 달라졌습니다. 비교 기준 설정을 검토하세요.")

    rows = []
    for pid in sorted(indexed["before"]):
        c = indexed["after"][pid][1]
        if any(indexed[key][pid][1].timing != c.timing for key in VARIANTS):
            raise ValueError(f"{pid}: 기존/현재 시간 계산이 다릅니다. 비교 기준을 검토하세요.")
        walk = c.timing.breakdown
        distance_limit = current.ranking.distance_zero_min * (2 if ctx.dest is not None else 1)
        expected_distance = max(0.0, 1 - (walk.walk_to + walk.walk_back) / distance_limit)
        if not math.isclose(c.features["distance"], expected_distance, abs_tol=1e-12):
            raise ValueError(f"{pid}: 현재 거리 점수가 총 도보 시간 공식과 다릅니다.")
        rows.append({
            "placeId": pid, "name": c.place["name"], "category": c.place["category"],
            "walkTo": walk.walk_to, "walkBack": walk.walk_back,
            "totalWalkMin": walk.walk_to + walk.walk_back, "needMin": c.timing.need_min,
            "crowded": c.features["crowd"] == 0,
            "group": {key: indexed[key][pid][0] for key in VARIANTS},
            "score": {key: round(indexed[key][pid][1].score, 6) for key in VARIANTS},
            "distanceFeature": {
                key: round(indexed[key][pid][1].features["distance"], 6) for key in VARIANTS
            },
        })
    context = asdict(ctx)
    context["start"] = to_hhmm(ctx.start)
    context["end"] = to_hhmm(ctx.end) if ctx.end is not None else None
    return {
        "id": sid, "label": label, "context": context,
        "normalizedWeights": {
            key: {factor: round(weight, 8) for factor, weight in _weights(ctx, setting).items()}
            for key, setting in zip(VARIANTS, (baseline, crowd_only, current))
        },
        "ranking": {
            key: {group: [c.place_id for c in getattr(result, group)]
                  for group in ("top", "more", "tight", "cafeteria")}
            for key, result in results.items()
        },
        "excluded": results["before"].excluded,
        "candidates": rows,
    }


def build_comparison() -> dict:
    checked = check_dirs([SAMPLES_DIR], today=VALIDATION_DATE)
    if checked.errors:
        raise ValueError("샘플 데이터 오류:\n" + "\n".join(map(str, checked.errors)))
    baseline_data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    current_data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    crowd_data = copy.deepcopy(baseline_data)
    crowd_data["ranking"]["weights"]["crowd"] = current_data["ranking"]["weights"]["crowd"]
    baseline, crowd_only, current = map(Settings.from_dict, (baseline_data, crowd_data, current_data))
    return {
        "description": "합성 샘플 8곳: 현재 공통 판정하에서 이전 가중치·편도 점수 / 혼잡 가중치만 변경 / 현재 총 도보 점수 비교",
        "assumptions": [
            "현재 data/samples 파일을 수정하지 않고 읽는다. 실제 영업·혼잡·강의실 검증이 아니다.",
            "기준일 2026-11-11(수), 출발 11:50, 다음 강의가 있으면 13:30.",
            "좌석·혼밥 조건 없이 예산·취향·방문 이력 없음. 모든 변형에 같은 입력을 사용한다.",
            "이전 가중치와 편도 거리 점수만 비교한다. 좌석·혼밥을 사용하던 과거 앱 전체 동작은 재현하지 않는다.",
            "남서쪽 목적지는 비교용 가상 좌표이며 실명 강의실을 뜻하지 않는다.",
            "도보는 직선거리×우회계수÷속도의 올림 추정치이며 실제 보행 경로가 아니다.",
            "학식은 별도 목록으로 유지한다. 일반 식당 상위 3곳과 경쟁하지 않는다.",
            "점수와 가중치는 출력 시에만 반올림한다. 순위는 반올림 전 점수로 결정한다.",
        ],
        "variants": {
            "before": {"crowdRawWeight": baseline.ranking.weights["crowd"],
                       "distanceFormula": "max(0, 1 - walkTo / distanceZeroMin)"},
            "crowdOnly": {"crowdRawWeight": crowd_only.ranking.weights["crowd"],
                          "distanceFormula": "max(0, 1 - walkTo / distanceZeroMin)"},
            "after": {"crowdRawWeight": current.ranking.weights["crowd"],
                      "distanceFormula": "max(0, 1 - (walkTo + walkBack) / (distanceZeroMin * (2 if dest is not None else 1)))"},
        },
        "sampleValidationDate": VALIDATION_DATE.isoformat(),
        "sampleCount": len(checked.places),
        "inputSha256": {
            "eval/baseline_settings.json": hashlib.sha256(BASELINE_PATH.read_bytes()).hexdigest(),
            "data/settings.json": hashlib.sha256(SETTINGS_PATH.read_bytes()).hexdigest(),
            **{f"data/samples/{path.name}": hashlib.sha256(path.read_bytes()).hexdigest()
               for path in sorted(SAMPLES_DIR.glob("*.json"))},
        },
        "currentSettings": current_data,
        "scenarios": [compare_scenario(sid, label, ctx, checked.places, baseline, crowd_only, current)
                      for sid, label, ctx in scenarios()],
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=EVAL_DIR / "ranking_comparison.json")
    args = parser.parse_args()
    report = build_comparison()
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("샘플 비교 (현재 공통 판정): 이전 점수 정책 -> 혼잡 가중치 완화 -> 총 도보 점수")
    print("점수는 가중치 재정규화 후 값이며 학식은 별도 목록입니다.")
    for scenario in report["scenarios"]:
        print(f"\n[{scenario['id']}] {scenario['label']}")
        print("  상위3: " + " -> ".join(", ".join(scenario["ranking"][v]["top"]) for v in VARIANTS))
        print("  후보   종류         갈때/다음건물/합계  혼잡  기존     혼잡만   개선")
        for c in scenario["candidates"]:
            scores = "  ".join(f"{c['score'][v]:.4f}" for v in VARIANTS)
            walk = f"{c['walkTo']}/{c['walkBack']}/{c['totalWalkMin']}분"
            print(f"  {c['placeId']}  {c['category']:<8}  {walk:<12}  {'Y' if c['crowded'] else '-'}    {scores}")
        print("  제외: " + ", ".join(f"{key}={value}" for key, value in scenario["excluded"].items() if value))
    print(f"\nJSON: {args.output}")


if __name__ == "__main__":
    main()
