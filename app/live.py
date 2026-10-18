"""Read-side of the pipeline: loads data/live/*.json snapshots (hot-reloads when files change)."""
import json, os

LIVE_DIR = os.getenv("DREAMHOME_LIVE_DIR", os.path.join(os.path.dirname(__file__), "..", "data", "live"))
DEFAULT_RATE = 6.5
_cache = {}


def _gold(name):
    """DATA_SOURCE=databricks: serve safety / environment / FRED from the Unity Catalog gold layer (app/gold.py)."""
    if name not in ("safety.json", "environment.json", "fred.json"):
        return None
    try:
        from . import gold
        snap = gold.active()
    except Exception:
        return None
    return snap.get(name) if snap else None


def _load(name):
    g = _gold(name)
    if g is not None:
        return g
    path = os.path.join(LIVE_DIR, name)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    hit = _cache.get(name)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    _cache[name] = (mtime, data)
    return data


def fred_series(key):
    d = _load("fred.json") or {}
    return (d.get("series") or {}).get(key)


def mortgage_rate():
    s = fred_series("mortgage_30y")
    if s:
        return s["value"], {"source": "FRED MORTGAGE30US" + (" via Databricks gold" if _gold("fred.json") else ""), "as_of": s["date"]}
    return DEFAULT_RATE, {"source": "default", "as_of": None}


def safety(neighborhood):
    d = _load("safety.json") or {}
    return (d.get("neighborhoods") or {}).get(neighborhood)


def environment(neighborhood):
    d = _load("environment.json") or {}
    return (d.get("neighborhoods") or {}).get(neighborhood)


DURHAM_MSA = {"Durham", "Chapel Hill", "Carrboro"}


def hpi_yoy(city):
    """FHFA house-price index YoY % for the listing's metro (Raleigh-Cary or Durham-Chapel Hill MSA)."""
    s = fred_series("durham_hpi" if city in DURHAM_MSA else "raleigh_hpi")
    return (s or {}).get("yoy_pct"), (s or {}).get("series_id")


def status():
    m = _load("manifest.json") or {}
    rate, src = mortgage_rate()
    hpi = fred_series("raleigh_hpi") or {}
    s = _load("safety.json") or {}
    return {"run_at": m.get("run_at"), "sources": m.get("sources", {}),
            "mortgage_30y": rate, "mortgage_source": src,
            "raleigh_hpi_yoy": hpi.get("yoy_pct"),
            "police_lookback_days": s.get("lookback_days"),
            "live_safety_neighborhoods": len(s.get("neighborhoods") or {}),
            "live_environment_neighborhoods": len((_load("environment.json") or {}).get("neighborhoods") or {}),
            "databricks_configured": all(os.getenv(k) for k in ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_WAREHOUSE_ID")),
            "databricks_catalog": os.getenv("DATABRICKS_CATALOG", "workspace"),
            "databricks_last_sync": (m.get("sources", {}).get("databricks") or {}).get("run_id"), **_source()}


def _source():
    try:
        from . import gold
        return gold.status()
    except Exception:
        return {"data_source": "local"}
