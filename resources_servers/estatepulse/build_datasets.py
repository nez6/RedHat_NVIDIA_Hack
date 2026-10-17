"""Build NeMo Gym datasets from the live EstatePulse scorers.

    python resources_servers/estatepulse/build_datasets.py   (run from the EstatePulse repo root)

Writes:
  data/eval_metrics.jsonl     8 eval harnesses (lifestyle, school, safety, environment, urban, commute, rooms, maintenance)
  data/rl_forecast_deal.jsonl eval + RL rollouts for the Value/Forecast and Deal Calculator agents
  data/example.jsonl          5-row smoke set
"""
import json, random, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from core import load  # noqa: E402

M, T = load()
from dreamhome_app import metric_agents as MA  # noqa: E402

EVAL_METRICS = [a.key for a in MA.AGENTS if a.gym_mode == "eval"]
RL_METRICS = [a.key for a in MA.AGENTS if a.gym_mode == "eval+rl"]
BUYERS = [
    {"summary": "family of four, needs schools and a yard", "max_price": 550000, "annual_income": 165000, "min_beds": 3,
     "must_have_features": ["fenced yard", "home office"], "commute_destination": "rtp", "urban_target": "cary"},
    {"summary": "young professional who wants walkability", "max_price": 400000, "annual_income": 110000, "min_beds": 1,
     "must_have_features": ["updated kitchen"], "commute_destination": "downtown raleigh", "urban_target": "downtown raleigh"},
    {"summary": "Duke employee, low maintenance", "max_price": 600000, "annual_income": 190000, "min_beds": 2,
     "must_have_features": ["garage"], "commute_destination": "duke", "urban_target": "downtown durham", "max_hoa": 150},
]


def tool_spec(metric):
    s = T.SCHEMAS[M.tool_name(metric)]["function"]
    params = json.loads(json.dumps(s["parameters"]))
    params["properties"]["buyer"] = {"type": "object", "description": "Buyer profile JSON exactly as given in the task"}
    params["additionalProperties"] = False
    return {"type": "function", "name": s["name"], "description": s["description"], "parameters": params, "strict": False}


def row(system, user, tools, task):
    return {"responses_create_params": {"input": [{"role": "developer", "content": system}, {"role": "user", "content": user}],
                                        "tools": tools}, "task": task}


def main(seed=7, per_metric=8, rl_tasks=24):
    rng = random.Random(seed)
    ids_all = [r["id"] for r in T.LISTINGS]
    evals, rls = [], []
    for metric in EVAL_METRICS:
        agent = MA.BY_KEY[metric]
        for i in range(per_metric):
            buyer = BUYERS[i % len(BUYERS)]
            ids = rng.sample(ids_all, 4)
            sc = {lid: M.FUNCS[metric](lid, buyer)["score"] for lid in ids}
            best = max(ids, key=lambda k: (sc[k], -k))
            user = (f"Buyer: {json.dumps(buyer)}\nShortlisted listing_ids: {ids}\nScore them on {M.LABELS[metric]}. "
                    f"End with 'ANSWER: <listing_id>' for the best listing on this metric.")
            evals.append(row(agent.system_prompt, user, [tool_spec(metric)],
                             {"task_type": "eval", "metric": metric, "listing_ids": ids, "best_id": best, "expected": sc}))
    deal_agent, fc_agent = MA.BY_KEY["deal"], MA.BY_KEY["forecast"]
    for i in range(rl_tasks):
        buyer = BUYERS[i % len(BUYERS)]
        if i % 2 == 0:
            ids = rng.sample(ids_all, 5)
            ranking = sorted(ids, key=lambda k: (-M.deal(k, buyer)["score"], k))
            user = (f"Buyer: {json.dumps(buyer)}\nCandidates: {ids}\nWhich candidate is the best deal for this buyer? "
                    f"Use get_deal_inputs (pass the buyer object). End with 'ANSWER: <listing_id>'.")
            rls.append(row(deal_agent.system_prompt, user, [tool_spec("deal")],
                           {"task_type": "deal", "metric": "deal", "listing_ids": ids, "ranking": ranking}))
        else:
            lid = rng.choice(ids_all)
            target = M.forecast(lid, buyer)["expected_12m_pct"]
            user = (f"Listing {lid}. Call get_forecast_inputs for it, then forecast its 12-month price appreciation in percent. "
                    f"House method: 60% of the metro house-price index YoY change (momentum) plus mean reversion of 0.15 x the "
                    f"negative of its price-vs-neighborhood-median %. End with 'ANSWER: <percent>' (two decimals).")
            rls.append(row(fc_agent.system_prompt, user, [tool_spec("forecast")],
                           {"task_type": "forecast", "metric": "forecast", "listing_ids": [lid], "target_pct": target}))
    data = HERE / "data"
    data.mkdir(exist_ok=True)
    for name, rows in (("eval_metrics.jsonl", evals), ("rl_forecast_deal.jsonl", rls), ("example.jsonl", evals[::13][:3] + rls[:2])):
        with open(data / name, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    print(f"eval tasks: {len(evals)} across {EVAL_METRICS}\nrl tasks: {len(rls)} across {RL_METRICS}")


if __name__ == "__main__":
    main()
