"""NeMo Gym environment: datasets build from the real scorers and rewards behave (no nemo_gym needed)."""
import importlib.util, json, sys
from pathlib import Path

import pytest

ENV = Path(__file__).resolve().parents[1] / "resources_servers" / "estatepulse"
sys.path.insert(0, str(ENV))
import rewards  # noqa: E402


def call(name, ids=None):
    return {"type": "function_call", "name": name, "arguments": json.dumps({"listing_ids": ids or []})}


def test_eval_reward_needs_tool_coverage_and_correct_best():
    task = {"task_type": "eval", "metric": "safety", "listing_ids": [1, 2, 3], "best_id": 2}
    assert rewards.reward(task, [call("get_safety_inputs", [1, 2, 3])], "best is 2. ANSWER: 2")[0] == 1.0
    assert rewards.reward(task, [call("get_safety_inputs", [1, 2])], "ANSWER: 2")[0] == 0.5
    assert rewards.reward(task, [call("get_commute_inputs", [1, 2, 3])], "ANSWER: 3")[0] == 0.0


def test_deal_reward_is_graded():
    task = {"task_type": "deal", "ranking": [5, 9, 1]}
    assert rewards.reward(task, [call("get_deal_inputs")], "ANSWER: 5")[0] == 1.0
    assert rewards.reward(task, [call("get_deal_inputs")], "ANSWER: 9")[0] == 0.5
    assert rewards.reward(task, [], "ANSWER: 5")[0] == pytest.approx(0.8)


def test_forecast_reward_is_continuous():
    task = {"task_type": "forecast", "target_pct": 3.0}
    assert rewards.reward(task, [call("get_forecast_inputs")], "ANSWER: 3.0")[0] == 1.0
    assert rewards.reward(task, [call("get_forecast_inputs")], "ANSWER: 4.0")[0] == pytest.approx(0.5)
    assert rewards.reward(task, [call("get_forecast_inputs")], "no number")[0] == 0.0


def test_datasets_cover_8_eval_metrics_and_2_rl_metrics(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("build_datasets", ENV / "build_datasets.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "HERE", tmp_path)
    mod.main(per_metric=2, rl_tasks=4)
    evals = [json.loads(x) for x in (tmp_path / "data" / "eval_metrics.jsonl").read_text().splitlines()]
    rls = [json.loads(x) for x in (tmp_path / "data" / "rl_forecast_deal.jsonl").read_text().splitlines()]
    assert {r["task"]["metric"] for r in evals} == {"lifestyle", "school", "safety", "environment", "urban", "commute", "rooms", "maintenance"}
    assert {r["task"]["task_type"] for r in rls} == {"deal", "forecast"}
    for r in evals:
        t = r["task"]
        assert t["best_id"] == max(t["listing_ids"], key=lambda k: (t["expected"][str(k)] if str(k) in t["expected"] else t["expected"][k], -k))
        assert r["responses_create_params"]["tools"][0]["name"] == f"get_{t['metric']}_inputs"
