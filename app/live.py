"""Read-side of the pipeline: loads data/live/*.json snapshots (hot-reloads when files change)."""
import json, os

LIVE_DIR = os.getenv("DREAMHOME_LIVE_DIR", os.path.join(os.path.dirname(__file__), "..", "data", "live"))
DEFAULT_RATE = 6.5
_cache = {}


def _load(name):
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
        return s["value"], {"source": "FRED MORTGAGE30US", "as_of": s["date"]}
    return DEFAULT_RATE, {"source": "default", "as_of": None}


def safety(neighborhood):
    d = _load("safety.json") or {}
    return (d.get("neighborhoods") or {}).get(neighborhood)


def status():
    m = _load("manifest.json") or {}
    rate, src = mortgage_rate()
    hpi = fred_series("raleigh_hpi") or {}
    s = _load("safety.json") or {}
    return {"run_at": m.get("run_at"), "sources": m.get("sources", {}),
            "mortgage_30y": rate, "mortgage_source": src,
            "raleigh_hpi_yoy": hpi.get("yoy_pct"),
            "police_lookback_days": s.get("lookback_days"),
            "live_safety_neighborhoods": len(s.get("neighborhoods") or {})}
