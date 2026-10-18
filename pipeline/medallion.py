"""Bronze -> silver -> gold logic, shared by the Databricks job (databricks/*.py) and the tests.

Spark-free on purpose: every function takes/returns plain Python rows, so the exact same code that scores homes in
the app produces the Databricks tables (one source of truth), and it is unit-tested in CI without a cluster.

  bronze  raw records as received (FRED observations, police incidents, offense counts, environment payloads, listings)
  silver  one row per (listing, factor) with the factor's inputs, formula and score, plus neighborhood-level indexes
  gold    one row per listing, ready for Jev: 10 factor scores, pre-computed math, safety text, Jev state + questions
"""
import datetime as dt
import json
import os
import sys
import tempfile

from . import transform
from .config import DATA_DIR, LOOKBACK_DAYS

ROOT = os.path.abspath(os.path.join(DATA_DIR, ".."))
DEFAULT_PROFILE = {"summary": "Family of four working at RTP; safe area, good schools, fenced yard and home office",
                   "max_price": 550000, "annual_income": 165000, "down_payment_pct": 20, "min_beds": 3,
                   "must_have_features": ["fenced yard", "home office"], "commute_destination": "rtp", "urban_target": "cary"}


def new_run_id():
    return "run-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ------------------------------------------------------------------ bronze
def bronze(fetch_fred, fetch_raleigh, fetch_cary, fetch_env, snapshot_dir=None):
    """Call each fetcher; on failure fall back to the last committed snapshot. Returns {table: [rows]} + source log."""
    snapshot_dir = snapshot_dir or os.path.join(DATA_DIR, "live")
    out, log = {}, {}

    def snap(name):
        with open(os.path.join(snapshot_dir, name)) as f:
            return json.load(f)

    try:
        series, errors = fetch_fred()
        mode = "live_api"
        if "mortgage_30y" not in series:
            raise RuntimeError(f"FRED incomplete: {errors}")
    except Exception as e:
        series, mode = snap("fred.json")["series"], "snapshot"
        log["fred_error"] = str(e)[:200]
    out["fred_observations"] = [{"name": k, "series_id": s["series_id"], "obs_date": d, "value": float(v), "source_mode": mode}
                                for k, s in series.items() for d, v in (s.get("history") or [[s["date"], s["value"]]])]
    log["fred"] = mode

    try:
        incidents, mode = fetch_raleigh(), "live_api"
    except Exception as e:
        incidents, mode = [], "unavailable"
        log["raleigh_error"] = str(e)[:200]
    out["police_incidents"] = [{**i, "source_mode": mode} for i in incidents]
    log["raleigh"] = mode

    try:
        counts, mode = fetch_cary(), "live_api"
    except Exception as e:
        counts, mode = {}, "unavailable"
        log["cary_error"] = str(e)[:200]
    out["cary_offense_counts"] = [{"offense": k, "n": int(v), "source_mode": mode} for k, v in counts.items()]
    log["cary"] = mode

    with open(os.path.join(DATA_DIR, "neighborhoods.json")) as f:
        hoods = json.load(f)
    env_rows = []
    try:
        env_snap = snap("environment.json")["neighborhoods"]
    except (OSError, KeyError, ValueError):
        env_snap = {}
    for h in hoods:
        try:
            payload, mode = fetch_env(h), "live_api"
        except Exception:
            payload, mode = env_snap.get(h["name"]), "snapshot"
        if payload:
            env_rows.append({"neighborhood": h["name"], "payload": json.dumps({k: payload[k] for k in ("flood", "aqi_30d", "weather_12m")}),
                             "source_mode": mode})
    out["environment_raw"] = env_rows
    log["environment"] = sorted({r["source_mode"] for r in env_rows})

    out["neighborhoods_raw"] = [dict(h) for h in hoods]
    import csv
    with open(os.path.join(DATA_DIR, "listings.csv")) as f:
        sample = [dict(r, source_mode="synthetic_sample") for r in csv.DictReader(f)]
    cols = list(sample[0]) if sample else []
    zillow = []
    try:  # parsed Zillow pages + live feed (pipeline/listings.py), stored with the same columns as strings
        with open(os.path.join(DATA_DIR, "zillow", "listings.json")) as f:
            for r in json.load(f).get("listings", []):
                row = {c: ("|".join(r[c]) if isinstance(r.get(c), list) else str(r.get(c, ""))) for c in cols if c != "source_mode"}
                zillow.append(dict(row, source_mode=r.get("source") or "zillow"))
    except (OSError, ValueError):
        pass
    out["listings_raw"] = zillow + sample
    log["listings"] = {"zillow": len(zillow), "sample": len(sample)}
    return out, log


# ------------------------------------------------------------------ silver
def _series_from_obs(obs_rows):
    from .sources.fred import YOY, summarize
    by = {}
    for r in obs_rows:
        by.setdefault((r["name"], r["series_id"]), []).append((str(r["obs_date"])[:10], float(r["value"])))
    out = {}
    for (name, sid), obs in by.items():
        obs = sorted(set(obs))
        out[name] = {"series_id": sid, **summarize(obs, YOY.get(name))}
    return out


def neighborhood_indexes(b):
    """Silver neighborhood layer: safety (police) + environment indexes, and the macro series."""
    hoods = b["neighborhoods_raw"]
    safety = {}
    if b["police_incidents"]:
        safety.update(transform.raleigh_profiles(hoods, b["police_incidents"]))
    if b["cary_offense_counts"]:
        safety.update(transform.cary_profiles(hoods, {r["offense"]: r["n"] for r in b["cary_offense_counts"]}))
    for name, p in safety.items():
        p["jev_state"] = transform.jev_state(name, p, LOOKBACK_DAYS)
    env = {}
    for r in b["environment_raw"]:
        e = json.loads(r["payload"])
        env[r["neighborhood"]] = {**e, "environment_index": transform.environment_index(e), "source": "fema_nfhl+open_meteo"}
    return safety, env, _series_from_obs(b["fred_observations"])


def _load_app(live_dir):
    """Import the app's scorers against a given live-snapshot directory (fresh import every call)."""
    os.environ["DREAMHOME_LIVE_DIR"] = live_dir
    while ROOT in sys.path:  # ROOT must win over any other "app" on the path (e.g. the NeMo Gym server's app.py)
        sys.path.remove(ROOT)
    sys.path.insert(0, ROOT)
    for m in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[m]
    from app import metrics, tools
    return metrics, tools


def silver(b, profile=None):
    """Returns (neighborhood_rows, factor_rows). factor_rows: one per listing x factor with inputs + formula + score."""
    profile = profile or DEFAULT_PROFILE
    safety, env, macro = neighborhood_indexes(b)
    live_dir = tempfile.mkdtemp(prefix="estatepulse_live_")
    for name, obj in (("fred.json", {"series": macro}), ("safety.json", {"lookback_days": LOOKBACK_DAYS, "neighborhoods": safety}),
                      ("environment.json", {"neighborhoods": env})):
        with open(os.path.join(live_dir, name), "w") as f:
            json.dump(obj, f, default=str)
    M, T = _load_app(live_dir)
    hood_rows = []
    for h in b["neighborhoods_raw"]:
        s, e = safety.get(h["name"]), env.get(h["name"])
        hood_rows.append({"neighborhood": h["name"], "city": h["city"],
                          "safety_index": (s or {}).get("safety_index", h["safety_index"]),
                          "safety_source": (s or {}).get("source", "baseline"), "incidents_lookback": (s or {}).get("incidents"),
                          "environment_index": (e or {}).get("environment_index"), "flood_zone": ((e or {}).get("flood") or {}).get("zone"),
                          "aqi_30d": (e or {}).get("aqi_30d"), "school_rating": h["school_rating"], "walk_score": h["walk_score"],
                          "jev_safety_text": (s or {}).get("jev_state") or f"{h['name']}: baseline safety index {h['safety_index']}."})
    factor_rows = []
    for r in T.LISTINGS:
        for f in M.METRICS:
            res = M.FUNCS[f](r["id"], profile)
            inputs = {k: v for k, v in res.items() if k != "score"}
            factor_rows.append({"listing_id": r["id"], "factor": f, "label": M.LABELS[f], "score": int(res["score"]),
                                "formula": M.FORMULAS[f], "inputs_json": json.dumps(inputs, default=str)})
    return hood_rows, factor_rows, live_dir


# ------------------------------------------------------------------ gold
def gold(factor_rows, hood_rows, live_dir, profile=None, weights=None):
    """One row per listing, ready to POST to Jev (state + questions) and to rank with the decision matrix."""
    profile = profile or DEFAULT_PROFILE
    M, T = _load_app(live_dir)
    from app import jev as J
    weights = weights or dict(M.DEFAULT_WEIGHTS)
    hoods = {h["neighborhood"]: h for h in hood_rows}
    by_listing = {}
    for fr in factor_rows:
        by_listing.setdefault(fr["listing_id"], {})[fr["factor"]] = fr["score"]
    tw = sum(weights.values()) or 1
    rows = []
    for lid, scores in by_listing.items():
        L = T.BY_ID[int(lid)]
        m = T.math_inputs(lid, profile)
        safety_text = hoods[L["neighborhood"]]["jev_safety_text"]
        rows.append({"listing_id": int(lid), "address": L["address"], "neighborhood": L["neighborhood"], "city": L["city"],
                     "price": L["price"], **{f"score_{k}": v for k, v in scores.items()},
                     "composite_score": round(sum(scores[k] * weights.get(k, 0) for k in scores) / tw, 1),
                     "monthly_cost": m["monthly"], "mortgage_rate_pct": m["rate"], "housing_ratio_pct": m["ratio"],
                     "price_vs_area_pct": m["vs_area"], "commute_min": m["commute_min"], "safety_index": m["safety_index"],
                     "jev_state": J.build_state(L, profile, m, safety_text),
                     "jev_questions_json": json.dumps(J.QUESTIONS), "buyer_profile_json": json.dumps(profile),
                     "weights_json": json.dumps(weights)})
    rows.sort(key=lambda x: -x["composite_score"])
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows
