"""Data-flow view for /pipeline: sources -> Databricks bronze / silver / gold -> 6 agents -> Jev -> outputs.

Static wiring (which tables and fields each agent reads, which tool it calls, what it writes) plus live numbers:
Databricks row counts, the latest architecture validation and agent eval. Databricks answers are cached for
FLOW_CACHE_SECS (default 300) so the page never hammers the warehouse.
"""
import os
import threading
import time

from . import gold as G, metric_agents as MA, metrics as M

SOURCES = [
    {"name": "FRED (St. Louis Fed)", "what": "30/15-yr mortgage rates, Raleigh + Durham FHFA house-price index, unemployment", "to": "bronze.fred_observations",
     "key": None, "cost": "free"},
    {"name": "Raleigh PD + Cary PD open data", "what": "90-day incidents by FBI NIBRS category", "to": "bronze.neighborhood_snapshots (police)",
     "key": None, "cost": "free"},
    {"name": "FEMA NFHL, Open-Meteo AQI, NOAA ACIS", "what": "flood zone, 30-day air quality, heat / heavy-rain days", "to": "bronze.neighborhood_snapshots (environment)",
     "key": None, "cost": "free"},
    {"name": "NC DPI + NCES, EPA walkability, Wake County parcels", "what": "school proficiency, walk index, sales price / sq ft",
     "to": "bronze.neighborhood_snapshots (profile)", "key": None, "cost": "free"},
    {"name": "Zillow saved pages, Redfin CSV, RentCast", "what": "for-sale listings (parsed, cleaned, de-duplicated)", "to": "app listing store + bronze.listings_raw",
     "key": "RENTCAST_API_KEY (optional)", "cost": "free tier"},
    {"name": "Census, TIGERweb, FBI CDE, Education Data API", "what": "live lookup for a city outside the Triangle", "to": "data/cities (neighborhood profiles)",
     "key": "CENSUS_API_KEY, FBI_API_KEY", "cost": "free"},
    {"name": "OpenRouteService", "what": "drive time to the buyer's work location", "to": "Commute agent (on request)", "key": "ORS_API_KEY", "cost": "free"},
    {"name": "Home Match Toolkit questionnaire", "what": "budget, rooms, area, 10 priority weights, financing document", "to": "secure.client_intake + client_documents volume",
     "key": None, "cost": "-"},
]
LAYERS = {
    "bronze": [("fred_observations", "Raw FRED observations, 10 years, every sync appended (lineage)"),
               ("neighborhood_snapshots", "Raw police / environment / profile JSON per neighborhood"),
               ("listings_raw", "Listings as ingested")],
    "silver": [("neighborhood_risk", "Latest validated safety + environment index per neighborhood"),
               ("macro_latest", "Latest value of each FRED series")],
    "gold": [("jev_ready_neighborhoods", "What the agents read: profile + latest police + environment"),
             ("jev_ready_macro", "What the agents read: latest FRED value + YoY"),
             ("jev_ready_listings", "One row per home with features, live rate, HPI trend and the 10 scores"),
             ("listing_scores", "Deterministic sub-scores per listing (10 metrics)"),
             ("fred_series", "FRED history, one row per series and date (view)"),
             ("agent_decisions", "Every AI recommendation: model, weights, top homes"),
             ("match_results", "Every ranked home per recommendation"),
             ("agent_eval_runs", "Nemotron eval metrics per run"),
             ("architecture_validation", "Data-quality + architecture checks per run")],
    "secure": [("client_intake", "Questionnaire submissions (row filter: clients see only their own)")],
}
# what each agent reads (gold columns / app inputs), the tool it calls on Nemotron, and what it hands on
READS = {
    "listing": {"profile": ["location", "max_price", "min_beds", "min_baths", "property_types", "must_have_features"],
                "data": ["app listing store (Zillow pages, Redfin CSV, RentCast, sample)", "data/cities for new cities"],
                "guards": ["must-haves enforced on every candidate", "zero matches -> no_match + relax suggestions"]},
    "safety": {"profile": ["weights.safety", "weights.environment"],
               "data": ["gold.jev_ready_neighborhoods.safety_json (incidents, NIBRS mix)", "gold.jev_ready_neighborhoods.environment_json (flood, AQI, weather)"],
               "guards": ["Jev safety judgement blended 50/50"]},
    "commute": {"profile": ["commute_destination", "max_commute_min"],
                "data": ["listing lat / lon", "OpenRouteService or Google drive time, else estimate"], "guards": ["halved when over the max commute"]},
    "budget": {"profile": ["max_price", "annual_income", "down_payment_pct", "risk_tolerance"],
               "data": ["gold.jev_ready_macro: mortgage_30y, raleigh_hpi / durham_hpi YoY", "gold.jev_ready_neighborhoods.median_ppsf", "listing price, sqft, HOA"],
               "guards": ["Jev never does arithmetic: payments are computed, then passed as facts"]},
    "lifestyle": {"profile": ["must_have_features", "urban_target", "min school rating"],
                  "data": ["gold.jev_ready_neighborhoods.school_rating (NC DPI)", "gold.jev_ready_neighborhoods.walk_score (EPA)", "listing features"],
                  "guards": ["Jev lifestyle fit replaces the rule score when available"]},
    "property": {"profile": ["max_hoa", "renovation_tolerance", "min_beds"],
                 "data": ["listing year_built, hoa_monthly, beds, baths, sqft, description"], "guards": ["year built / HOA marked when estimated"]},
}
JEV = {"reads": ["math inputs per home: monthly payment, live rate, housing ratio, % vs neighborhood price/sqft, commute minutes, safety index",
                 "safety narrative from gold.jev_ready_neighborhoods.safety_json"],
       "does": "one calibrated vLLM call per home in parallel: constrained choice over option letters, softmax over logprobs",
       "writes": ["verdict (tour now / consider / skip) + safety and lifestyle judgements -> decision matrix", "gold.agent_decisions, gold.match_results"]}
OUTPUTS = ["Decision matrix: 10 criteria x weights -> fit score per home", "JEB recommendation (Nemotron narrates, guarded against invented homes)",
           "gold.agent_decisions + gold.match_results (audit)", "gold.agent_eval_runs / agent_eval_cases (accuracy)", "gold.architecture_validation (data quality)"]

_CACHE = {"at": 0.0, "data": None}
_LOCK = threading.Lock()


def _live(client=None):
    from pipeline import databricks_sink as D
    if not (client or D.configured()):
        return {"connected": False}
    c = client or D.Client()
    cat = D.catalog()
    parts = []
    for layer, tables in LAYERS.items():
        schema = "estatepulse_secure" if layer == "secure" else D.SCHEMAS[layer]
        parts += [f"SELECT '{layer}.{t}' AS t, count(*) AS n FROM {D.q(cat)}.{D.q(schema)}.{D.q(t)}" for t, _ in tables]
    out = {"connected": True, "catalog": cat, "counts": {}, "errors": []}
    try:
        out["counts"] = {r["t"]: int(r["n"]) for r in D.rows(c, " UNION ALL ".join(parts))}
    except Exception:  # one missing table fails the union: fall back to per-table counts
        for q in parts:
            try:
                r = D.rows(c, q)[0]
                out["counts"][r["t"]] = int(r["n"])
            except Exception as e:
                out["errors"].append(str(e)[:120])
    g = lambda t: D.fq("gold", t)  # noqa: E731
    try:
        vid = D.rows(c, f"SELECT max(validation_id) AS v FROM {g('architecture_validation')}")[0]["v"]
        rows = D.rows(c, f"SELECT category, check_name, status, observed, detail FROM {g('architecture_validation')} WHERE validation_id = :v", {"v": vid})
        out["validation"] = {"id": vid, "checks": rows}
    except Exception as e:
        out["validation"] = {"error": str(e)[:160]}
    try:
        eid = D.rows(c, f"SELECT max(eval_id) AS e FROM {g('agent_eval_runs')}")[0]["e"]
        out["eval"] = {"id": eid, "metrics": {r["metric"]: float(r["value"]) for r in
                                              D.rows(c, f"SELECT metric, value FROM {g('agent_eval_runs')} WHERE eval_id = :e", {"e": eid})}}
    except Exception as e:
        out["eval"] = {"error": str(e)[:160]}
    return out


def _refresh(client=None):
    try:
        live = _live(client)
    except Exception as e:
        live = {"connected": False, "error": str(e)[:200]}
    with _LOCK:
        _CACHE.update(at=time.time(), data=live, busy=False)
    return live


def warm():
    """Called at startup: fetch counts in the background so the first page view is instant."""
    with _LOCK:
        if _CACHE.get("busy"):
            return
        _CACHE["busy"] = True
    threading.Thread(target=_refresh, daemon=True).start()


def build(refresh=False, client=None):
    """Stale-while-revalidate: a cached answer is served at once and refreshed in the background after FLOW_CACHE_SECS."""
    ttl = float(os.getenv("FLOW_CACHE_SECS", "300"))
    with _LOCK:
        live, age = _CACHE["data"], time.time() - _CACHE["at"]
    if live is None or refresh or client is not None:
        live = _refresh(client)
    elif age > ttl:
        warm()
    checks = (live.get("validation") or {}).get("checks") or []
    agents = []
    for a in MA.AGENTS:
        mine = [ch for ch in checks if a.name in ch["check_name"]]
        agents.append({"key": a.key, "name": a.name, "question": a.question, "tool": a.tool, "sources": a.sources,
                       "metrics": [{"key": m, "label": M.LABELS[m], "formula": M.FORMULAS[m]} for m in a.metrics],
                       **READS[a.key], "checks": mine})
    return {"data_source": G.status(), "sources": SOURCES, "layers": {k: [{"table": t, "about": d} for t, d in v] for k, v in LAYERS.items()},
            "agents": agents, "jev": JEV, "outputs": OUTPUTS, "databricks": live}
