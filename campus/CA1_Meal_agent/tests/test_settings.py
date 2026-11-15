import json

import pytest
from jsonschema import Draft202012Validator

from meal import check_data
from meal.settings import SETTINGS_PATH, SETTINGS_SCHEMA_PATH, Settings, load_settings
from meal.timing import compute


def config():
    return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))


def test_settings_schema_and_current_values_are_valid():
    Draft202012Validator.check_schema(json.loads(SETTINGS_SCHEMA_PATH.read_text(encoding="utf-8")))
    assert Settings.from_dict(config()).buffer_min == 5


@pytest.mark.parametrize("path,value", [
    (("walk", "metersPerMin"), 0),
    (("walk", "detourFactor"), -1),
    (("walk", "maxSnapM"), 0),
    (("bufferMin",), -5),
    (("tightMarginMin",), -1),
    (("mealTypes", "일반", "eat"), -1),
    (("mealTypes", "일반", "wait"), -1),
    (("mealTypes", "일반", "waitPeak"), -1),
    (("mealTypes", "일반", "eat"), 2.5),
    (("ranking", "slackFullMin"), 0),
    (("ranking", "distanceZeroMin"), 0),
    (("ranking", "rainDistanceMultiplier"), 0),
    (("ranking", "weights", "distance"), -0.1),
    (("ranking", "overBudgetOneBand"), 1.1),
    (("ranking", "chipMinFactor"), -0.1),
    (("ranking", "penaltyRecent"), -0.05),
    (("walk", "metersPerMin"), True),
    (("bufferMin",), "5"),
])
def test_invalid_values_rejected_before_calculation(path, value):
    data = config()
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError, match=path[-1]):
        Settings.from_dict(data)


def test_negative_buffer_cannot_make_tight_meal_look_safe():
    data = config()
    baseline = compute(start=600, end=643, walk_to=4, walk_back=6, meal_type="일반")
    assert baseline.need_min == 48 and baseline.fit == "tight"
    data["bufferMin"] = -5
    with pytest.raises(ValueError, match="bufferMin"):
        Settings.from_dict(data)


def test_all_zero_weights_rejected():
    data = config()
    data["ranking"]["weights"] = dict.fromkeys(data["ranking"]["weights"], 0)
    with pytest.raises(ValueError, match="ranking.weights"):
        Settings.from_dict(data)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_values_rejected(value):
    data = config()
    data["ranking"]["weights"]["distance"] = value
    with pytest.raises(ValueError, match="ranking.weights.distance"):
        Settings.from_dict(data)


def test_rain_weight_overflow_rejected():
    data = config()
    data["ranking"]["weights"]["distance"] = 1e308
    data["ranking"]["rainDistanceMultiplier"] = 2
    with pytest.raises(ValueError, match="ranking.weights"):
        Settings.from_dict(data)


@pytest.mark.parametrize("weights", [
    {"slack": 1},
    {"slack": 1, "budget": 1, "taste": 1, "distance": 1, "crowd": 1, "typo": 1},
])
def test_missing_or_unknown_score_factor_rejected(weights):
    data = config()
    data["ranking"]["weights"] = weights
    with pytest.raises(ValueError, match="weights"):
        Settings.from_dict(data)


@pytest.mark.parametrize("peak", [["12:30", "11:50"], ["12:00", "12:00"], ["11:99", "12:30"]])
def test_invalid_peak_window_rejected(peak):
    data = config()
    data["peaks"] = [peak]
    with pytest.raises(ValueError, match="peaks"):
        Settings.from_dict(data)


def test_zero_wait_buffer_and_individual_weight_are_allowed():
    data = config()
    data["bufferMin"] = 0
    data["mealTypes"]["일반"]["wait"] = 0
    data["ranking"]["weights"]["crowd"] = 0
    s = Settings.from_dict(data)
    t = compute(start=600, end=643, walk_to=4, walk_back=6, meal_type="일반", s=s)
    assert t.need_min == 40 and t.fit == "ok"


@pytest.mark.parametrize("start,wait,done", [(600, 3, "10:37"), (710, 8, "12:32")])
def test_integral_json_numbers_serialize_as_minute_values(start, wait, done):
    # JSON Schema의 integer는 30.0 같은 정수 값도 허용한다.
    data = config()
    data["mealTypes"]["일반"] = {"eat": 30.0, "wait": 3.0, "waitPeak": 8.0}
    s = Settings.from_dict(data)
    t = compute(start=start, end=start + 100, walk_to=4, walk_back=6, meal_type="일반", s=s)
    assert t.to_dict()["doneAt"] == done
    assert type(t.breakdown.eat) is int
    assert type(t.breakdown.wait) is int and t.breakdown.wait == wait


def test_load_settings_checks_file_and_cli_reports_error(tmp_path, monkeypatch, capsys):
    data = config()
    data["bufferMin"] = -5
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="bufferMin"):
        load_settings(path)
    monkeypatch.setattr(check_data, "load_settings", lambda: load_settings(path))
    assert check_data.main() == 1
    out = capsys.readouterr().out
    assert "settings.json" in out and "bufferMin" in out and "오류 1" in out
