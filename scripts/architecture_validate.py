"""End-to-end architecture validation cycle: Databricks medallion -> gold -> 6 agents -> Jev, against live Nemotron.

    python scripts/architecture_validate.py            # sync first, then validate; writes gold.architecture_validation
    python scripts/architecture_validate.py --no-sync  # validate what is already in Databricks

  1. workspace     Is Unity Catalog clean and complete? expected tables only, no empty tables, no nulls in key columns,
                   no duplicates in the latest run, values in range, every listing scored on all 10 metrics, FRED fresh
                   for its cadence, every neighborhood the listings use present in gold. (Read-only: nothing is deleted;
                   suggested cleanup SQL is printed for you to run yourself.)
  2. parity        Does gold carry exactly what the app computed locally? per neighborhood and per FRED series.
  3. agents        Same homes, same buyer: the 10 criterion scores and Jev verdicts computed from Databricks gold vs from
                   local files must match; then the full 6-agent pipeline runs on gold with Nemotron.
  4. completeness  Share of every agent's evidence that comes from a real source (not sample / default / estimate).
  5. relevance     Nemotron rates each scoring agent's evidence for its question: complete / partial / insufficient.
"""
import argparse
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app import agents as AG, gold as G, live, metric_agents as MA, metrics as M, tools as T  # noqa: E402
from pipeline import databricks_sink as D  # noqa: E402
from pipeline.config import DATA_DIR  # noqa: E402

PROMPTS = ["Family of four working at RTP, safe area, great schools, fenced yard, home office, ~$550k, income $165k",
           "3 bed 2 bath under $500k in Raleigh, commute to downtown Raleigh under 25 minutes",
           "Townhome in Cary under $475,000 with 3 beds"]
EXPECTED = {"bronze": {"fred_observations", "neighborhood_snapshots", "listings_raw"},
            "silver": {"neighborhood_risk", "macro_latest"},
            "gold": {"listing_scores", "listing_scorecard", "pipeline_audit", "agent_decisions", "match_results", "agent_eval_runs",
                     "agent_eval_cases", "fred_series", "jev_ready_neighborhoods", "jev_ready_macro", "jev_ready_listings",
                     "architecture_validation"},
            "secure": {"client_intake"}}
OPTIONAL = {"elt_audit", "listing_factors", "neighborhood_indexes", "fred_macro", "police_incidents", "environment_snapshots"}  # notebook job
CADENCE_DAYS = {"MORTGAGE": 14, "ATNHPI": 200, "RALE": 100}  # max acceptable age of the latest observation
NOT_REAL = ("sample", "default", "baseline", "estimate", "no crime data", "no school data", "fallback")
CHECKS = []


def check(category, name, ok, observed, expected, detail="", warn=False):
    CHECKS.append({"category": category, "check_name": name, "status": "pass" if ok else ("warn" if warn else "fail"),
                   "observed": str(observed)[:400], "expected": str(expected)[:200], "detail": str(detail)[:600]})
    return ok


# ------------------------------------------------------------------ 1. workspace
def workspace(c):
    cat = D.catalog()
    schemas = {**D.SCHEMAS, "secure": "estatepulse_secure"}
    for layer, schema in schemas.items():
        try:
            found = {r["tableName"] for r in D.rows(c, f"SHOW TABLES IN {D.q(cat)}.{D.q(schema)}")}
        except Exception as e:
            check("workspace", f"{schema} exists", False, str(e)[:120], "schema present")
            continue
        missing, extra = EXPECTED[layer] - found, found - EXPECTED[layer] - OPTIONAL
        check("workspace", f"{schema}: expected tables", not missing, sorted(found), sorted(EXPECTED[layer]), f"missing: {sorted(missing)}")
        check("workspace", f"{schema}: no unexpected tables", not extra, sorted(extra), "none", "review / drop leftovers yourself", warn=True)
        for t in sorted(found & EXPECTED[layer]):
            if t in ("architecture_validation", "agent_eval_runs", "agent_eval_cases", "match_results", "agent_decisions", "client_intake"):
                continue  # filled by usage, may legitimately be empty
            n = int(D.rows(c, f"SELECT count(*) AS n FROM {D.q(cat)}.{D.q(schema)}.{D.q(t)}")[0]["n"])
            check("workspace", f"{schema}.{t} has rows", n > 0, n, "> 0")
    b, s, g = (lambda t: D.fq("bronze", t)), (lambda t: D.fq("silver", t)), (lambda t: D.fq("gold", t))
    one = lambda sql: D.rows(c, sql)[0]  # noqa: E731
    latest = f"(SELECT max_by(run_id, ingested_at) FROM {b('listings_raw')})"
    r = one(f"SELECT count(*) n, count(DISTINCT listing_id) u, sum(CASE WHEN price IS NULL OR beds IS NULL OR sqft IS NULL THEN 1 ELSE 0 END) nulls "
            f"FROM {b('listings_raw')} WHERE run_id = {latest}")
    check("workspace", "listings: unique ids in latest run", r["n"] == r["u"], f"{r['n']} rows / {r['u']} ids", "equal")
    check("workspace", "listings: no null price/beds/sqft", int(r["nulls"] or 0) == 0, r["nulls"], 0)
    n_list = int(r["u"])
    r = one(f"SELECT count(*) n, count(DISTINCT listing_id) l, sum(CASE WHEN score < 0 OR score > 100 OR score IS NULL THEN 1 ELSE 0 END) bad "
            f"FROM {g('listing_scores')} WHERE run_id = (SELECT max_by(run_id, scored_at) FROM {g('listing_scores')})")
    check("workspace", "scores: every listing x 10 metrics", int(r["n"]) == n_list * 10 and int(r["l"]) == n_list, f"{r['n']} scores / {r['l']} listings",
          f"{n_list * 10} / {n_list}")
    check("workspace", "scores: all within 0-100", int(r["bad"] or 0) == 0, r["bad"], 0)
    r = one(f"SELECT sum(CASE WHEN safety_index NOT BETWEEN 0 AND 100 THEN 1 ELSE 0 END) bs, "
            f"sum(CASE WHEN environment_index NOT BETWEEN 0 AND 100 THEN 1 ELSE 0 END) be FROM {s('neighborhood_risk')}")
    check("workspace", "risk indexes within 0-100", int(r["bs"] or 0) + int(r["be"] or 0) == 0, r, "0 out of range")
    r = one(f"SELECT count(*) n FROM {b('fred_observations')} WHERE value IS NULL OR obs_date IS NULL")
    check("workspace", "FRED: no null values", int(r["n"]) == 0, r["n"], 0)
    for m in D.rows(c, f"SELECT name, series_id, obs_date, datediff(current_date(), obs_date) age, yoy_pct FROM {g('jev_ready_macro')}"):
        limit = next((d for k, d in CADENCE_DAYS.items() if m["series_id"].startswith(k)), 120)
        check("workspace", f"FRED fresh: {m['name']}", int(m["age"]) <= limit, f"latest {m['obs_date']} ({m['age']} days)", f"<= {limit} days",
              warn=int(m["age"]) <= limit * 2)
        check("workspace", f"FRED yoy computed: {m['name']}", m["yoy_pct"] is not None, m["yoy_pct"], "not null")
    names = {r["name"] for r in D.rows(c, f"SELECT name FROM {g('jev_ready_macro')}")}
    check("workspace", "FRED: all 5 series in gold", {"mortgage_30y", "raleigh_hpi", "durham_hpi"} <= names and len(names) >= 5, sorted(names), "5 series")
    r = one(f"SELECT count(*) n FROM {g('jev_ready_listings')} WHERE school_rating IS NULL OR median_ppsf IS NULL")
    check("workspace", "every listing's neighborhood is in gold", int(r["n"]) == 0, f"{r['n']} listings without a neighborhood profile", 0)
    r = one(f"SELECT count(*) n FROM {g('match_results')} m LEFT ANTI JOIN {b('listings_raw')} l ON l.listing_id = m.listing_id")
    check("workspace", "match_results reference known listings", int(r["n"]) == 0, f"{r['n']} unknown ids", 0,
          "uploaded / live-feed homes are not in bronze.listings_raw yet", warn=True)
    r = one(f"SELECT datediff(current_timestamp(), max(ingested_at)) age FROM {b('neighborhood_snapshots')}")
    check("workspace", "neighborhood snapshots fresh", int(r["age"]) <= 7, f"{r['age']} days old", "<= 7 days", warn=True)
    runs = {t: int(one(f"SELECT count(DISTINCT run_id) n FROM {b(t)}")["n"]) for t in ("fred_observations", "neighborhood_snapshots", "listings_raw")}
    check("workspace", "bronze run history", True, runs, "append-only", "older runs are kept for lineage; see cleanup SQL to prune")
    return runs


def cleanup_sql(keep=3):
    b = lambda t: D.fq("bronze", t)  # noqa: E731
    return [f"DELETE FROM {b(t)} WHERE run_id NOT IN (SELECT run_id FROM (SELECT run_id, max(ingested_at) m FROM {b(t)} GROUP BY run_id "
            f"ORDER BY m DESC LIMIT {keep}));" for t in ("fred_observations", "neighborhood_snapshots", "listings_raw")] + [
        f"DELETE FROM {D.fq('gold', 'agent_decisions')} WHERE run_id = 'eval' AND decided_at < current_date() - INTERVAL 30 DAYS;"]


# ------------------------------------------------------------------ 2. parity
def local_snapshot():
    def load(n):
        try:
            with open(os.path.join(live.LIVE_DIR, n)) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}
    with open(os.path.join(DATA_DIR, "neighborhoods.json")) as f:
        profiles = {h["name"]: h for h in json.load(f)}
    return {"safety.json": load("safety.json"), "environment.json": load("environment.json"), "fred.json": load("fred.json"), "profiles": profiles}


def parity(local, gold):
    bad = []
    for name, p in local["profiles"].items():
        gp = gold["profiles"].get(name)
        if not gp:
            bad.append(f"{name}: missing in gold")
            continue
        bad += [f"{name}.{k}: local {p.get(k)} vs gold {gp.get(k)}" for k in ("school_rating", "walk_score", "median_ppsf") if p.get(k) != gp.get(k)]
    for src, key in (("safety.json", "safety_index"), ("environment.json", "environment_index")):
        for name, v in (local[src].get("neighborhoods") or {}).items():
            gv = (gold[src].get("neighborhoods") or {}).get(name, {}).get(key)
            if gv != v.get(key):
                bad.append(f"{name}.{key}: local {v.get(key)} vs gold {gv}")
    check("parity", "neighborhood features: gold == local", not bad, f"{len(bad)} mismatches", 0, "; ".join(bad[:8]))
    fb, newer = [], []
    for name, sv in (local["fred.json"].get("series") or {}).items():
        gv = gold["fred.json"]["series"].get(name)
        if not gv:
            fb.append(f"{name}: missing")
        elif gv["date"] > sv["date"]:
            newer.append(f"{name}: gold has {gv['date']}, local snapshot {sv['date']}")
        elif abs(gv["value"] - sv["value"]) > 1e-6 or gv["date"] != sv["date"]:
            fb.append(f"{name}: local {sv['value']}@{sv['date']} vs gold {gv['value']}@{gv['date']}")
        elif sv.get("yoy_pct") is not None and abs((gv["yoy_pct"] or 0) - sv["yoy_pct"]) > 0.05:
            fb.append(f"{name}.yoy: local {sv['yoy_pct']} vs gold {gv['yoy_pct']}")
    check("parity", "FRED latest + yoy: gold == local", not fb, f"{len(fb)} mismatches", 0, "; ".join(fb), warn=all("yoy" in x for x in fb) and bool(fb))
    if newer:  # the sync pulls FRED fresh, so gold can be ahead of the app's local snapshot: that's an update, not an error
        check("parity", "FRED: gold newer than local snapshot", True, "; ".join(newer), "refresh local with python -m pipeline.run")


# ------------------------------------------------------------------ 3-5. agents on gold
def evidence_sources(d):
    return [str(v) for k, v in d.items() if ("source" in k or k == "hpi_series") and v is not None]


def completeness(ids, profile):
    out = {}
    for a in MA.AGENTS:
        if not a.metrics:
            continue
        real = total = 0
        gaps = set()
        for lid in ids:
            for m in a.metrics:
                srcs = evidence_sources(M.FUNCS[m](lid, profile))
                if not srcs:
                    continue
                total += 1
                if any(any(t in s.lower() for t in NOT_REAL) for s in srcs):
                    gaps.update(s for s in srcs if any(t in s.lower() for t in NOT_REAL))
                else:
                    real += 1
        pct = round(100 * real / total, 1) if total else 100.0
        out[a.key] = pct
        check("completeness", f"{a.name}: evidence from real sources", pct >= 80, f"{pct}%", ">= 80%", "gaps: " + "; ".join(sorted(gaps))[:500],
              warn=pct >= 50)
    return out


RATE = {"type": "function", "function": {"name": "rate_evidence", "description": "Rate whether the evidence answers the agent's question.",
        "parameters": {"type": "object", "required": ["verdict"], "properties": {
            "verdict": {"type": "string", "enum": ["complete", "partial", "insufficient"]},
            "missing": {"type": "array", "items": {"type": "string"}}, "reason": {"type": "string"}}}}}


def relevance(ids, profile):
    out = {}
    for a in MA.AGENTS:
        if not a.metrics:
            continue
        ev = MA.group_evidence(a, ids[:3], profile)
        try:
            resp = AG._chat([{"role": "system", "content": "You audit data for a home-buying agent. Judge only whether the evidence is "
                                                           "sufficient and relevant to answer the question for each home; do not judge the homes."},
                             {"role": "user", "content": f"Agent: {a.name}\nQuestion: {a.question}\nEvidence: {json.dumps(ev)[:5000]}"}],
                            tools=[RATE], tool_choice={"type": "function", "function": {"name": "rate_evidence"}}, max_tokens=300)
            args = json.loads(resp.choices[0].message.tool_calls[0].function.arguments)
        except Exception as e:
            args = {"verdict": "error", "reason": str(e)[:160]}
        out[a.key] = args
        check("relevance", f"Nemotron: {a.name} evidence", args.get("verdict") == "complete", args.get("verdict"), "complete",
              f"missing: {args.get('missing') or []}; {args.get('reason') or ''}", warn=args.get("verdict") == "partial")
    return out


def agents_on_gold(gold_snap, local_snap):
    summary = []
    for prompt in PROMPTS:
        profile = AG.build_profile(prompt, {}, use_llm=False)
        filt = {k: v for k, v in profile.items() if k in ("location", "max_price", "min_beds", "min_baths", "property_types", "must_have_features")}
        ids = [r["id"] for r in T.find_listings(**filt, limit=8)["listings"] if AG.meets_must_haves(r, profile)][:8]
        res = {}
        for mode, snap in (("local", False), ("gold", gold_snap)):
            G.force(snap)
            rows = T.score_matrix(ids, profile, profile["weights"])
            jev = AG.jev_layer(ids, profile, lambda e: None)
            res[mode] = {"scores": {r["listing"]["id"]: r["scores"] for r in rows}, "order": [r["listing"]["id"] for r in rows],
                         "verdicts": {k: v.get("verdict") for k, v in jev.items()}, "engine": {v.get("engine") for v in jev.values()}}
        G.force(False)  # control: Jev again on the identical local inputs = its own run-to-run variance on vLLM
        rerun = {k: v.get("verdict") for k, v in AG.jev_layer(ids, profile, lambda e: None).items()}
        diffs = [abs(res["gold"]["scores"][i][c] - res["local"]["scores"][i][c]) for i in ids for c in T.CRITERIA]
        same_v = sum(res["gold"]["verdicts"].get(i) == res["local"]["verdicts"].get(i) for i in res["local"]["verdicts"])
        same_ctrl = sum(rerun.get(i) == res["local"]["verdicts"].get(i) for i in res["local"]["verdicts"])
        check("agents", f"scores gold == local: {prompt[:40]}", max(diffs or [0]) == 0, f"max diff {max(diffs or [0])} over {len(diffs)} scores", 0)
        check("agents", f"ranking gold == local: {prompt[:40]}", res["gold"]["order"] == res["local"]["order"], res["gold"]["order"][:3],
              res["local"]["order"][:3])
        n = len(res["local"]["verdicts"])
        check("agents", f"Jev verdicts gold vs local: {prompt[:40]}", same_v == n or same_v >= same_ctrl,
              f"{same_v}/{n} same; Jev re-run on identical local data: {same_ctrl}/{n} same ({','.join(sorted(res['gold']['engine']))})",
              "gold agreement >= Jev's own repeatability", "differences within Jev's run-to-run variance are model noise, not data" if same_v < n else "",
              warn=True)
        G.force(gold_snap)  # full crew on gold with Nemotron
        ev = []
        AG.run_pipeline(prompt, {}, ev.append)
        start = next(e for e in ev if e["type"] == "start")
        crew = {e["key"]: e["status"] for e in ev if e["type"] == "crew_agent"}
        rows = next((e["rows"] for e in ev if e["type"] == "matrix"), [])
        rec = next((e["text"] for e in ev if e["type"] == "recommendation"), "")
        check("agents", f"pipeline read Databricks gold: {prompt[:40]}", start["data"].get("data_source") == "databricks gold",
              start["data"].get("data_source"), "databricks gold")
        check("agents", f"6 agents on Nemotron: {prompt[:40]}", all(s == "done" for s in crew.values()) and len(crew) == 5,
              f"listing + {sum(s == 'done' for s in crew.values())}/{len(crew)} scoring done", "listing + 5/5")
        check("agents", f"recommendation grounded: {prompt[:40]}", bool(rows) and not AG.unknown_addresses(rec, [r["listing"]["address"] for r in rows]),
              rec[:200], "names only ranked homes")
        summary.append({"prompt": prompt, "ids": ids})
    G.force(gold_snap)
    allids = sorted({i for s in summary for i in s["ids"]})
    prof = AG.build_profile(PROMPTS[0], {}, use_llm=False)
    comp = completeness(allids, prof)
    rel = relevance(allids, prof)
    G.force(None)
    return comp, rel


def write(c, vid):
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    D._insert_json(c, D.fq("gold", "architecture_validation"), CHECKS,
                   "ARRAY<STRUCT<category:STRING,check_name:STRING,status:STRING,observed:STRING,expected:STRING,detail:STRING>>",
                   f"'{vid}', r.category, r.check_name, r.status, r.observed, r.expected, r.detail, TIMESTAMP '{now}'")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-sync", action="store_true")
    ap.add_argument("--no-agents", action="store_true", help="data checks only (no Nemotron calls)")
    a = ap.parse_args(argv)
    if not D.configured():
        print("Set DATABRICKS_HOST / DATABRICKS_TOKEN / DATABRICKS_WAREHOUSE_ID", file=sys.stderr)
        return 2
    os.environ.setdefault("EP_DECISION_SOURCE", "eval")
    c = D.Client()
    if not a.no_sync:
        print("sync:", json.dumps(D.sync(c, actor="architecture_validate")["counts"]))
    vid = "val-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:6]
    runs = workspace(c)
    gold_snap = G.fetch(c)
    local_snap = local_snapshot()
    parity(local_snap, gold_snap)
    comp = rel = None
    if not a.no_agents:
        comp, rel = agents_on_gold(gold_snap, local_snap)
    write(c, vid)
    by = {}
    for x in CHECKS:
        by.setdefault(x["category"], {"pass": 0, "warn": 0, "fail": 0})[x["status"]] += 1
    report = {"validation_id": vid, "summary": by, "evidence_completeness_pct": comp,
              "nemotron_relevance": {k: v.get("verdict") for k, v in (rel or {}).items()}, "bronze_runs": runs,
              "failed": [f"{x['check_name']}: {x['observed']} (expected {x['expected']}) {x['detail']}" for x in CHECKS if x["status"] == "fail"],
              "warnings": [f"{x['check_name']}: {x['observed']} {x['detail']}" for x in CHECKS if x["status"] == "warn"]}
    print(json.dumps(report, indent=1, default=str))
    print(f"\nAll checks: SELECT * FROM {D.catalog()}.estatepulse_gold.architecture_validation WHERE validation_id = '{vid}'")
    print("Optional cleanup (not run; review first):\n  " + "\n  ".join(cleanup_sql()))
    os.makedirs(os.path.join(DATA_DIR, "..", "results"), exist_ok=True)
    with open(os.path.join(DATA_DIR, "..", "results", "architecture_validation.json"), "w") as f:
        json.dump({"report": report, "checks": CHECKS}, f, indent=1, default=str)
    return 1 if by.get("workspace", {}).get("fail") or by.get("parity", {}).get("fail") or by.get("agents", {}).get("fail") else 0


if __name__ == "__main__":
    sys.exit(main())
