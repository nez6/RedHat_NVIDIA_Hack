"""Databricks gold layer as the live app's data source (DATA_SOURCE=databricks).

The 5 scoring agents and Jev read neighborhood risk, school / walkability / price-per-sq-ft profiles and FRED macro
data from Unity Catalog gold tables built by pipeline/databricks_sink.py:

    gold.jev_ready_neighborhoods   profile + latest police + environment snapshot per neighborhood
    gold.jev_ready_macro           latest FRED value + year-over-year change per series

Requests never wait on the warehouse: the snapshot is fetched in the background at startup and refreshed every
GOLD_TTL_SECS (default 600). Until the first fetch lands (or if Databricks is unreachable / the free-tier warehouse
is asleep) the app keeps using its local snapshot and reports data_source=local, so a search never fails because of it.
Listings stay in the app's own store (uploads and live feeds land there first); gold supplies the features.
"""
import json
import os
import threading
import time

_LOCK = threading.Lock()
_STATE = {"snapshot": None, "loaded_at": 0.0, "error": None, "loading": False, "forced": None, "saved_hoods": None}
HOOD_FIELDS = ("school_rating", "walk_score", "median_ppsf", "school_source", "walk_source", "ppsf_source", "safety_index")


def wanted():
    return os.getenv("DATA_SOURCE", "local").lower() == "databricks"


def _client():
    from pipeline import databricks_sink as D
    if not D.configured():
        raise RuntimeError("Databricks not configured")
    return D, D.Client()


def fetch(client=None):
    """-> snapshot in the same shapes as data/live/*.json plus neighborhood profile overrides."""
    from pipeline import databricks_sink as D
    c = client or _client()[1]
    hoods = D.rows(c, f"SELECT * FROM {D.fq('gold', 'jev_ready_neighborhoods')}")
    macro = D.rows(c, f"SELECT * FROM {D.fq('gold', 'jev_ready_macro')}")
    safety, env, profiles, as_of, run_id = {}, {}, {}, None, None
    for h in hoods:
        name = h["neighborhood"]
        if h.get("safety_json"):
            safety[name] = json.loads(h["safety_json"])
        if h.get("environment_json"):
            env[name] = json.loads(h["environment_json"])
        if h.get("profile_json"):
            profiles[name] = json.loads(h["profile_json"])
        as_of = max(as_of or "", str(h.get("as_of") or ""))
        run_id = run_id or h.get("run_id")
    series = {}
    for m in macro:
        series[m["name"]] = {"series_id": m["series_id"], "date": str(m["obs_date"])[:10], "value": float(m["value"]),
                             "yoy_pct": float(m["yoy_pct"]) if m.get("yoy_pct") is not None else None, "history": []}
    if not profiles or not series:
        raise RuntimeError("gold tables are empty - run python -m pipeline.databricks_sink")
    return {"safety.json": {"neighborhoods": safety, "lookback_days": (next(iter(safety.values()), {}) or {}).get("lookback_days")},
            "environment.json": {"neighborhoods": env}, "fred.json": {"series": series}, "profiles": profiles,
            "as_of": as_of, "run_id": run_id, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def _apply_profiles(snap):
    """Overlay gold neighborhood profiles onto the in-memory HOODS the scorers read (restorable)."""
    from . import tools as T
    with _LOCK:
        if _STATE["saved_hoods"] is None:
            _STATE["saved_hoods"] = {n: {k: h.get(k) for k in HOOD_FIELDS} for n, h in T.HOODS.items()}
        for name, prof in (snap or {}).get("profiles", {}).items():
            if name in T.HOODS:
                T.HOODS[name].update({k: prof[k] for k in HOOD_FIELDS if k in prof})


def _restore_profiles():
    from . import tools as T
    with _LOCK:
        saved, _STATE["saved_hoods"] = _STATE["saved_hoods"], None
    for name, vals in (saved or {}).items():
        if name in T.HOODS:
            T.HOODS[name].update(vals)


def refresh(wait=False):
    def run():
        try:
            snap = fetch()
            _apply_profiles(snap)
            with _LOCK:
                _STATE.update(snapshot=snap, loaded_at=time.time(), error=None)
        except Exception as e:
            with _LOCK:
                _STATE["error"] = str(e)[:200]
                _STATE["loaded_at"] = time.time()  # back off for a TTL before retrying
        finally:
            with _LOCK:
                _STATE["loading"] = False
    with _LOCK:
        if _STATE["loading"]:
            return
        _STATE["loading"] = True
    if wait:
        run()
    else:
        threading.Thread(target=run, daemon=True).start()


def active():
    """The gold snapshot to serve from right now, or None (= use local files)."""
    if _STATE["forced"] is not None:
        return _STATE["forced"] or None
    if not wanted():
        return None
    if time.time() - _STATE["loaded_at"] > float(os.getenv("GOLD_TTL_SECS", "600")) and not _STATE["loading"]:
        refresh()
    return _STATE["snapshot"]


def force(snapshot):
    """Validation hook. force(snap) serves exactly that snapshot; force(False) forces local files; force(None) resets."""
    _restore_profiles()
    _STATE["forced"] = snapshot
    if snapshot:
        _apply_profiles(snapshot)
    elif snapshot is None and wanted() and _STATE["snapshot"]:
        _apply_profiles(_STATE["snapshot"])


def status():
    snap = active()
    return {"data_source": "databricks gold" if snap else "local", "wanted": "databricks" if wanted() else "local",
            "gold_as_of": (snap or {}).get("as_of"), "gold_fetched_at": (snap or {}).get("fetched_at"),
            "gold_run_id": (snap or {}).get("run_id"), "gold_error": _STATE["error"]}
