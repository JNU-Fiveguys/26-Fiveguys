import json

import pytest

from eval.compare_ranking import EVAL_DIR, build_comparison


@pytest.fixture(scope="module")
def comparison():
    return build_comparison()


def test_previous_score_policy_uses_current_common_candidate_rules(comparison):
    scenario = comparison["scenarios"][0]
    # 이전 가중치만 고정한다. 혼밥·좌석을 쓰던 과거 전체 후보 판정을 재현하지 않는다.
    assert scenario["ranking"]["before"]["top"] == ["s_05", "s_01", "s_02"]
    scores = {c["placeId"]: c["score"]["before"] for c in scenario["candidates"]}
    assert scores["s_01"] == pytest.approx(0.672222, abs=1e-6)
    assert scores["s_03"] == pytest.approx(0.661111, abs=1e-6)
    assert scores["s_05"] == pytest.approx(0.727778, abs=1e-6)
    assert "solo" not in scenario["excluded"] and "party" not in scenario["excluded"]


def test_comparison_separates_crowd_and_route_effects(comparison):
    scenarios = {s["id"]: s for s in comparison["scenarios"]}
    same = scenarios["same_building"]["ranking"]
    other = scenarios["different_building"]["ranking"]
    assert same["crowdOnly"]["top"] == same["after"]["top"] == ["s_05", "s_01", "s_02"]
    assert other["crowdOnly"]["top"] == ["s_05", "s_01", "s_03"]
    assert other["after"]["top"] == ["s_03", "s_05", "s_01"]
    for c in scenarios["no_next_class"]["candidates"]:
        assert c["walkBack"] == 0
        assert c["score"]["crowdOnly"] == c["score"]["after"]


def test_crowd_change_relaxes_only_remaining_cafeteria_peak_signal(comparison):
    rows = {c["placeId"]: c for c in comparison["scenarios"][0]["candidates"]}
    assert {pid for pid, row in rows.items() if row["crowded"]} == {"s_08"}
    assert rows["s_08"]["score"]["crowdOnly"] > rows["s_08"]["score"]["before"]
    # 좌석이 작았던 분식 샘플에는 더 이상 혼잡 감점 신호를 만들지 않는다.
    assert rows["s_05"]["crowded"] is False
    assert rows["s_05"]["score"]["before"] > rows["s_08"]["score"]["before"]


def test_saved_comparison_matches_current_inputs(comparison):
    saved = json.loads((EVAL_DIR / "ranking_comparison.json").read_text(encoding="utf-8"))
    # JSON 직렬화가 좌표 tuple을 list로 바꾸는 부분도 동일하게 비교한다.
    assert saved == json.loads(json.dumps(comparison, ensure_ascii=False))
