"""Live accuracy eval for the 6-agent crew against Nemotron on vLLM (the hermetic versions live in tests/test_agent_quality.py).

    python scripts/agent_eval.py                 # needs NEMOTRON_BASE_URL reachable; writes results/agent_eval.json
    python scripts/agent_eval.py --quick         # extraction cases only

  1. Parameter extraction  each case in tests/fixtures/extraction_cases.json through
                           (a) Nemotron alone (forced set_search_filters tool call), (b) rules alone, (c) the shipped merge;
                           field-level precision / recall and exact-match rate
  2. Empty data            impossible requests through the full pipeline: must end in no_match, zero homes, no narration
  3. Pipeline integrity    normal requests: all 6 agents finish on Nemotron, every shortlisted id is a real listing that meets
                           the must-haves, every matrix row is scored, the recommendation names only homes in the results
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app import agents as AG, extract as X, tools as T  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CASES = json.load(open(os.path.join(ROOT, "tests", "fixtures", "extraction_cases.json")))
IMPOSSIBLE = ["4 bedroom house under $150k in Durham", "6 bed 5 bath in Five Points under $300,000", "10 bedrooms in Cary max $400k"]
NORMAL = ["Family of four working at RTP, safe area, great schools, fenced yard, home office, ~$550k, income $165k",
          "3 bed 2 bath under $500k in Raleigh, commute to downtown Raleigh under 25 minutes",
          "Townhome in Cary under $475,000 with 3 beds"]


def same(a, b):
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) < 1e-6
        except (TypeError, ValueError):
            return False
    if isinstance(a, list) and isinstance(b, list):
        return sorted(map(str, a)) == sorted(map(str, b))
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().lower() == b.strip().lower()
    return a == b


def score(pred, exp):
    tp = sum(1 for k, v in exp.items() if k in pred and same(pred[k], v))
    return {"tp": tp, "fp": sum(1 for k in pred if k not in exp or not same(pred[k], exp[k])), "fn": len(exp) - tp, "exact": tp == len(exp) and len(pred) == len(exp)}


def summarize(rows):
    tp, fp, fn = (sum(r[k] for r in rows) for k in ("tp", "fp", "fn"))
    return {"precision": round(tp / (tp + fp), 3) if tp + fp else 1.0, "recall": round(tp / (tp + fn), 3) if tp + fn else 1.0,
            "exact_match": round(sum(r["exact"] for r in rows) / len(rows), 3), "cases": len(rows)}


def chat(messages, tools=None, tool_choice="auto"):
    return AG._chat(messages, tools=tools, tool_choice=tool_choice, max_tokens=300)


def eval_extraction():
    per, modes = [], {"llm": [], "rules": [], "merged": []}
    for c in CASES:
        t0 = time.time()
        try:
            llm = X.llm_params(c["prompt"], chat)
            err = None
        except Exception as e:
            llm, err = {}, str(e)[:160]
        ms = int((time.time() - t0) * 1000)
        rules = X.validate(X.rule_params(c["prompt"]))
        merged, src = dict(rules), {k: "rules" for k in rules}
        for k, v in llm.items():
            if k not in merged and X.grounded(k, v, c["prompt"]):
                merged[k], src[k] = v, "llm"
        row = {"prompt": c["prompt"], "expected": c["expected"], "llm": llm, "rules": rules, "merged": merged, "llm_ms": ms, "llm_error": err}
        for m in modes:
            s = score(row[m], c["expected"])
            modes[m].append(s)
            row[m + "_score"] = s
        per.append(row)
    return {m: summarize(v) for m, v in modes.items()}, per


def collect(dream):
    ev = []
    t0 = time.time()
    AG.run_pipeline(dream, {}, ev.append)
    return ev, round(time.time() - t0, 1)


def eval_empty():
    out = []
    for q in IMPOSSIBLE:
        ev, secs = collect(q)
        kinds = [e["type"] for e in ev]
        ids = next((e["ids"] for e in ev if e["type"] == "shortlist"), None)
        rec = next((e["text"] for e in ev if e["type"] == "recommendation"), "")
        ok = "no_match" in kinds and ids == [] and "Would you like to" in rec and "crew_agent" not in kinds
        out.append({"prompt": q, "pass": ok, "recommendation": rec, "secs": secs})
    return {"passed": sum(r["pass"] for r in out), "cases": len(out)}, out


def eval_pipeline():
    out = []
    for q in NORMAL:
        ev, secs = collect(q)
        prof = next(e["profile"] for e in ev if e["type"] == "profile")
        ids = next((e["ids"] for e in ev if e["type"] == "shortlist"), [])
        crew = {e["key"]: e["status"] for e in ev if e["type"] == "crew_agent"}
        rows = next((e["rows"] for e in ev if e["type"] == "matrix"), [])
        rec = next((e["text"] for e in ev if e["type"] == "recommendation"), "")
        listing_fb = [e["reason"] for e in ev if e["type"] == "fallback" and e.get("agent") == AG.MA.BY_KEY["listing"].name]
        checks = {
            "listing_agent_called_tool": any(e["type"] == "tool" and e["tool"] == "find_listings" for e in ev),
            "scoring_agents_on_nemotron": f"{sum(s == 'done' for s in crew.values())}/{len(crew)}",
            "shortlist_real_and_within_must_haves": bool(ids) and all(i in T.BY_ID and AG.meets_must_haves(T.BY_ID[i], prof) for i in ids),
            "matrix_complete": bool(rows) and all(len(r["scores"]) == len(T.CRITERIA) for r in rows),
            "recommendation_grounded": not AG.unknown_addresses(rec, [r["listing"]["address"] for r in rows]),
            "recommendation_from_model": not any(e["type"] == "fallback" and e.get("agent") == "JEB Recommendation" for e in ev),
        }
        out.append({"prompt": q, "checks": checks, "extracted": prof.get("extracted"), "listing_fallbacks": listing_fb, "secs": secs,
                    "recommendation": rec})
    agg = {k: sum(1 for r in out if r["checks"][k] is True) for k in out[0]["checks"] if k != "scoring_agents_on_nemotron"} if out else {}
    agg["scoring_agents_done"] = sum(int(r["checks"]["scoring_agents_on_nemotron"].split("/")[0]) for r in out)
    agg["scoring_agents_total"] = sum(int(r["checks"]["scoring_agents_on_nemotron"].split("/")[1]) for r in out)
    return {"cases": len(out), **agg}, out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--out", default=os.path.join(ROOT, "results", "agent_eval.json"))
    a = ap.parse_args(argv)
    h = AG.health()
    report = {"model": h.get("model"), "nemotron_ok": h["ok"], "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    report["extraction"], ext_rows = eval_extraction()
    detail = {"extraction": ext_rows}
    if not a.quick:
        report["empty_data"], detail["empty_data"] = eval_empty()
        report["pipeline"], detail["pipeline"] = eval_pipeline()
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump({"summary": report, "detail": detail}, f, indent=1)
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
