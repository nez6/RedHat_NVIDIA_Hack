"""Agent Test Lab: run one agent at a time on live Nemotron with an edited prompt, score it on per-agent KPIs and
validate every run with the NeMo Gym reward functions (resources_servers/estatepulse/rewards.py).

    scenario   a fixed buyer + shortlist, so prompt variants are compared on identical inputs
    KPIs       listing agent: tool called, filters passed exactly, must-have compliance, homes returned, latency
               scoring agents: tool called, id coverage, best-home correct, answer given, evidence from real sources, latency
    validator  NeMo Gym eval_reward (0.5 x called its tool with ALL ids + 0.5 x named the right best home); the listing
               agent uses the same shape (0.5 x exact filters + 0.5 x every home meets the must-haves). pass = reward >= 0.75
Runs are appended to TESTLAB_DIR/runs.jsonl and, when Databricks is configured, to gold.agent_test_runs.
"""
import importlib.util
import json
import os
import statistics
import threading
import time
import uuid

from . import agents as AG, metric_agents as MA, metrics as M, tools as T

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DIR = os.getenv("TESTLAB_DIR", os.path.join(ROOT, "data", "testlab"))
PASS = 0.75
NOT_REAL = ("sample", "default", "baseline", "estimate", "no crime data", "no school data", "fallback")
_LOCK = threading.Lock()

SCENARIOS = [
    {"id": "family-rtp", "label": "Family of four, RTP commute",
     "buyer": {"summary": "family of four, needs schools and a yard", "max_price": 550000, "annual_income": 165000, "min_beds": 3,
               "must_have_features": ["fenced yard", "home office"], "commute_destination": "rtp", "urban_target": "cary"}},
    {"id": "young-pro", "label": "Young professional, walkable Raleigh",
     "buyer": {"summary": "young professional who wants walkability", "location": "Raleigh", "max_price": 400000, "annual_income": 110000,
               "min_beds": 1, "must_have_features": ["updated kitchen"], "commute_destination": "downtown raleigh", "urban_target": "downtown raleigh"}},
    {"id": "duke-lowmaint", "label": "Duke employee, low maintenance",
     "buyer": {"summary": "Duke employee, low maintenance", "max_price": 600000, "annual_income": 190000, "min_beds": 2,
               "must_have_features": ["garage"], "commute_destination": "duke", "urban_target": "downtown durham", "max_hoa": 150}},
    {"id": "cary-townhome", "label": "Cary townhome, 3 beds",
     "buyer": {"summary": "townhome in Cary", "location": "Cary", "max_price": 475000, "min_beds": 3, "property_types": ["Townhouse"]}},
]
KPIS = {
    "listing": [("tool_called", "called find_listings"), ("filters_exact", "share of the buyer's filters passed exactly"),
                ("compliance", "share of returned homes meeting every must-have"), ("homes", "homes returned"), ("latency_ms", "wall time")],
    "scoring": [("tool_called", "called its evidence tool"), ("coverage", "share of shortlisted ids passed to the tool"),
                ("best_correct", "named the same best home as the deterministic scores"), ("answer_given", "ended with ANSWER: <id>"),
                ("evidence_real", "share of evidence from real sources (data, not prompt)"), ("latency_ms", "wall time")],
}
FILTER_KEYS = ("location", "max_price", "min_beds", "min_baths", "property_types", "must_have_features")


def _rewards():
    """The NeMo Gym environment's reward module (pure Python), loaded from resources_servers/."""
    path = os.path.join(ROOT, "resources_servers", "estatepulse", "rewards.py")
    spec = importlib.util.spec_from_file_location("estatepulse_gym_rewards", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def scenario(sid):
    s = next((x for x in SCENARIOS if x["id"] == sid), None)
    if not s:
        raise KeyError(f"unknown scenario {sid}")
    b = s["buyer"]
    out = T.find_listings(**{k: b[k] for k in FILTER_KEYS if b.get(k)}, limit=8)
    ids = [r["id"] for r in out["listings"] if AG.meets_must_haves(r, b)][:5]
    return {**s, "ids": ids, "profile": {**b, "weights": dict(M.DEFAULT_WEIGHTS)}}


def describe():
    return {"agents": [{"key": a.key, "name": a.name, "tool": a.tool, "metrics": [M.LABELS[m] for m in a.metrics], "default_prompt": a.system_prompt,
                        "kpis": [{"key": k, "about": d} for k, d in KPIS["listing" if not a.metrics else "scoring"]]} for a in MA.AGENTS],
            "scenarios": [{"id": s["id"], "label": s["label"], "buyer": s["buyer"]} for s in SCENARIOS],
            "validator": "NeMo Gym eval_reward (resources_servers/estatepulse/rewards.py), pass >= 0.75"}


def _evidence_real(agent, ids, profile):
    real = total = 0
    for lid in ids:
        for m in agent.metrics:
            srcs = [str(v) for k, v in M.FUNCS[m](lid, profile).items() if ("source" in k or k == "hpi_series") and v is not None]
            if srcs:
                total += 1
                real += not any(t in s.lower() for s in srcs for t in NOT_REAL)
    return round(real / total, 3) if total else 1.0


def _expected_best(agent, ids, profile):
    sc = {lid: statistics.mean(M.FUNCS[m](lid, profile)["score"] for m in agent.metrics) for lid in ids}
    return max(ids, key=lambda k: (sc[k], -k)), {str(k): round(v, 1) for k, v in sc.items()}


def run_listing(agent, sc, prompt, events):
    filt = {k: sc["profile"].get(k) for k in FILTER_KEYS if sc["profile"].get(k)}
    t0 = time.time()
    text, res = AG.run_agent(agent.name, prompt, f"Buyer profile: {json.dumps(filt)}", ["find_listings"], events.append, max_steps=3)
    ms = int((time.time() - t0) * 1000)
    calls = [(n, a, o) for n, a, o in res if n == "find_listings"]
    first = calls[0][1] if calls else {}
    exact = [k for k in filt if json.dumps(first.get(k), sort_keys=True) == json.dumps(filt[k], sort_keys=True)]
    homes = [r for _, _, o in calls if isinstance(o, dict) for r in o.get("listings") or []]
    comp = sum(AG.meets_must_haves(r, sc["profile"]) for r in homes) / len(homes) if homes else 0.0
    kpis = {"tool_called": bool(calls), "filters_exact": round(len(exact) / len(filt), 3) if filt else 1.0,
            "compliance": round(comp, 3), "homes": len({r["id"] for r in homes}), "latency_ms": ms}
    reward = 0.5 * (kpis["filters_exact"] == 1.0) + 0.5 * (bool(homes) and comp == 1.0)
    gym = {"reward": reward, "function": "listing reward (filters exact + must-have compliance)",
           "detail": {"missing_or_changed_filters": sorted(set(filt) - set(exact)), "tool_args": first}}
    return kpis, gym, text


def run_scoring(agent, sc, prompt, events):
    ids, profile = sc["ids"], sc["profile"]
    best, scores = _expected_best(agent, ids, profile)
    called = {}

    def capture(args):
        called.setdefault("ids", []).extend(int(x) for x in args.get("listing_ids") or [])
        return MA.group_evidence(agent, args.get("listing_ids") or ids, profile)

    user = (f"Buyer: {json.dumps({k: v for k, v in profile.items() if k != 'weights'})}\nShortlisted listing_ids: {ids}\n"
            f"End with 'ANSWER: <listing_id>' for the best listing on your area.")
    t0 = time.time()
    text, res = AG.run_agent(agent.name, prompt, user, [agent.tool], events.append, max_steps=3, capture={agent.tool: capture})
    ms = int((time.time() - t0) * 1000)
    R = _rewards()
    # the Gym reward expects get_<metric>_inputs; the crew tool is get_<agent>_evidence -> same check, renamed
    items = [{"type": "function_call", "name": f"get_{agent.key}_inputs" if n == agent.tool else n, "arguments": json.dumps(a)} for n, a, _ in res]
    reward, detail = R.reward({"task_type": "eval", "metric": agent.key, "listing_ids": ids, "best_id": best}, items, text)
    ans = R.parse_answer(text)
    kpis = {"tool_called": bool(called), "coverage": round(len(set(called.get("ids", [])) & set(ids)) / len(ids), 3) if ids else 0.0,
            "best_correct": ans == best, "answer_given": ans is not None, "evidence_real": _evidence_real(agent, ids, profile), "latency_ms": ms}
    gym = {"reward": reward, "function": "NeMo Gym eval_reward", "detail": {**detail, "expected_best": best, "answer": ans, "scores": scores}}
    return kpis, gym, text


def run(agent_key, prompt=None, label="default", scenario_ids=None, repeats=1, sink=None):
    agent = MA.BY_KEY[agent_key]
    prompt = (prompt or "").strip() or agent.system_prompt
    batch = "lab-" + time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:4]
    rows = []
    for sid in scenario_ids or [s["id"] for s in SCENARIOS]:
        sc = scenario(sid)
        for rep in range(max(1, min(int(repeats or 1), 5))):
            events = []
            try:
                kpis, gym, text = (run_listing if not agent.metrics else run_scoring)(agent, sc, prompt, events)
                error = None
            except Exception as e:
                kpis, gym, text, error = {}, {"reward": 0.0, "function": "-", "detail": {}}, "", str(e)[:200]
            rows.append({"run_id": f"{batch}-{len(rows) + 1}", "batch": batch, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                         "agent": agent_key, "agent_name": agent.name, "label": (label or "default")[:60], "scenario": sid, "repeat": rep + 1,
                         "prompt_is_default": prompt == agent.system_prompt, "prompt": prompt[:4000], "kpis": kpis,
                         "gym_reward": round(float(gym["reward"]), 3), "gym_pass": gym["reward"] >= PASS, "gym": gym,
                         "llm_calls": sum(e["type"] == "llm" for e in events), "fallbacks": [e.get("reason") for e in events if e["type"] == "fallback"],
                         "tool_calls": [{"tool": e["tool"], "args": e["args"]} for e in events if e["type"] == "tool"],
                         "output": (text or "")[:800], "error": error})
    save(rows)
    (sink or log_databricks)(rows)
    return {"batch": batch, "rows": rows, "summary": summarize(rows)}


def summarize(rows):
    if not rows:
        return {}
    kp = {}
    for r in rows:
        for k, v in r["kpis"].items():
            kp.setdefault(k, []).append(float(v))
    return {"runs": len(rows), "pass_rate": round(sum(r["gym_pass"] for r in rows) / len(rows), 3),
            "mean_reward": round(statistics.mean(r["gym_reward"] for r in rows), 3), "kpis": {k: round(statistics.mean(v), 3) for k, v in kp.items()},
            "errors": sum(bool(r["error"]) for r in rows)}


def save(rows):
    os.makedirs(DIR, exist_ok=True)
    with _LOCK, open(os.path.join(DIR, "runs.jsonl"), "a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def history(limit=500):
    try:
        with open(os.path.join(DIR, "runs.jsonl")) as f:
            rows = [json.loads(line) for line in f if line.strip()]
    except OSError:
        rows = []
    rows = rows[-limit:]
    groups = {}
    for r in rows:
        groups.setdefault((r["agent"], r["label"]), []).append(r)
    board = [{"agent": a, "label": lab, "prompt_is_default": rs[-1]["prompt_is_default"], **summarize(rs), "last": rs[-1]["at"]}
             for (a, lab), rs in groups.items()]
    board.sort(key=lambda b: (b["agent"], -b["mean_reward"]))
    return {"runs": list(reversed(rows)), "leaderboard": board}


def log_databricks(rows):
    """Best effort, in the background: gold.agent_test_runs (one row per run, KPIs as JSON)."""
    def go():
        try:
            from pipeline import databricks_sink as D
            if not D.configured():
                return
            c = D.Client()
            c.sql(f"""CREATE TABLE IF NOT EXISTS {D.fq('gold', 'agent_test_runs')} (run_id STRING, batch STRING, agent STRING, label STRING,
                scenario STRING, prompt_is_default BOOLEAN, prompt STRING, kpis STRING, gym_reward DOUBLE, gym_pass BOOLEAN, output STRING,
                error STRING, ran_at TIMESTAMP) COMMENT 'Agent Test Lab: prompt variants x scenarios on live Nemotron, KPIs + NeMo Gym reward'""")
            D._insert_json(c, D.fq("gold", "agent_test_runs"),
                           [{**{k: r[k] for k in ("run_id", "batch", "agent", "label", "scenario", "prompt_is_default", "prompt", "gym_reward",
                                                   "gym_pass", "output", "error")}, "kpis": json.dumps(r["kpis"]), "at": r["at"].replace("T", " ").rstrip("Z")}
                            for r in rows],
                           "ARRAY<STRUCT<run_id:STRING,batch:STRING,agent:STRING,label:STRING,scenario:STRING,prompt_is_default:BOOLEAN,prompt:STRING,"
                           "gym_reward:DOUBLE,gym_pass:BOOLEAN,output:STRING,error:STRING,kpis:STRING,at:STRING>>",
                           "r.run_id, r.batch, r.agent, r.label, r.scenario, r.prompt_is_default, r.prompt, r.kpis, r.gym_reward, r.gym_pass, "
                           "r.output, r.error, to_timestamp(r.at)")
        except Exception:
            pass
    threading.Thread(target=go, daemon=True).start()
