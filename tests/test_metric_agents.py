"""The 10 metric agents must be wired correctly: tool, scorer, weight, prompt, NeMo Gym mode, fallbacks."""
import pytest

from app import metric_agents as MA, metrics as M, tools as T

PROFILE = {"max_price": 500_000, "annual_income": 150_000, "min_beds": 3, "must_have_features": ["garage"],
           "commute_destination": "rtp", "urban_target": "downtown durham", "max_hoa": 100}


def test_registry_is_valid():
    assert MA.validate_registry() == []
    assert [a.key for a in MA.AGENTS] == M.METRICS == T.CRITERIA


@pytest.mark.parametrize("metric", M.METRICS)
def test_each_metric_scores_every_listing_in_range(metric):
    for r in T.LISTINGS:
        out = M.FUNCS[metric](r["id"], PROFILE)
        assert 0 <= out["score"] <= 100, (metric, r["id"], out)


@pytest.mark.parametrize("agent", MA.AGENTS, ids=lambda a: a.key)
def test_each_agent_tool_returns_one_row_per_listing(agent):
    rows = M.run_tool(agent.key, [1001, 1002, 999999], PROFILE)
    assert [r["listing_id"] for r in rows] == [1001, 1002, 999999]
    assert "error" in rows[2] and "score" in rows[0]
    assert T.SCHEMAS[agent.tool]["function"]["parameters"]["required"] == ["listing_ids"]


def test_live_environment_snapshot_is_used():
    oak = next(r for r in T.LISTINGS if r["neighborhood"] == "Oakwood")
    e = M.environment(oak["id"], {})
    assert e["score"] == 45 and e["flood_zone"] == "AE"
    other = next(r for r in T.LISTINGS if r["neighborhood"] == "Garner")
    assert M.environment(other["id"], {})["source"].startswith("baseline")


def test_forecast_uses_metro_hpi_and_mean_reversion():
    lid = T.LISTINGS[0]["id"]
    f = M.forecast(lid, {})
    a = T.affordability(lid)
    assert f["hpi_yoy_pct"] == 1.2  # Raleigh fixture
    assert f["expected_12m_pct"] == pytest.approx(0.6 * 1.2 - a["price_vs_area_pct"] * 0.15, abs=0.01)
    durham = next(r for r in T.LISTINGS if r["city"] == "Durham")
    assert M.forecast(durham["id"], {})["hpi_series"] == "default"  # no Durham HPI in fixture -> default 3%


def test_user_controls_change_scores():
    r = next(x for x in T.LISTINGS if x["hoa_monthly"] > 100)
    assert M.maintenance(r["id"], {"max_hoa": 50})["score"] < M.maintenance(r["id"], {})["score"]
    lid = T.LISTINGS[0]["id"]
    assert M.commute(lid, {"max_commute_min": 1})["score"] <= M.commute(lid, {})["score"]


def test_agent_falls_back_when_it_skips_its_tool():
    events = []
    lazy = lambda *a, **k: ("I am confident without data.", [])  # never calls the tool
    key, status = MA.run_one(MA.BY_KEY["deal"], [1001, 1002], PROFILE, events.append, lazy)
    assert status == "fallback" and any(e["type"] == "fallback" for e in events)


def test_agent_uses_its_tool_when_model_calls_it():
    events = []

    def good(name, system, user, tools, emit, max_steps, capture):
        rows = capture[tools[0]]({"listing_ids": [1001]})
        return f"best is {rows[0]['listing_id']}", []

    key, status = MA.run_one(MA.BY_KEY["safety"], [1001], PROFILE, events.append, good)
    assert status == "done" and events[-1]["text"] == "best is 1001"
