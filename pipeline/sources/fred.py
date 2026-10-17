"""FRED macro data: mortgage rates, Triangle house-price indices, unemployment."""
import csv, io, os

from .. import http
from ..config import FRED_API_URL, FRED_CSV_URL, FRED_SERIES


def parse_csv(text):
    """fredgraph.csv -> [(date, float)] oldest..newest, skipping '.' (missing) values."""
    rows = list(csv.reader(io.StringIO(text)))
    out = []
    for r in rows[1:]:
        if len(r) >= 2 and r[1] not in ("", "."):
            try:
                out.append((r[0], float(r[1])))
            except ValueError:
                continue
    return out


def parse_api(payload):
    obs = payload.get("observations", [])
    out = [(o["date"], float(o["value"])) for o in obs if o.get("value") not in (None, "", ".")]
    return sorted(out)


def fetch_series(series_id, api_key=None):
    if api_key:
        return parse_api(http.get(FRED_API_URL.format(series=series_id, key=api_key)))
    return parse_csv(http.get(FRED_CSV_URL.format(series=series_id), as_json=False))


def summarize(obs, yoy_periods=None):
    if not obs:
        raise ValueError("empty series")
    date, value = obs[-1]
    s = {"date": date, "value": round(value, 4), "history": [[d, v] for d, v in obs[-12:]]}
    if yoy_periods and len(obs) > yoy_periods:
        prev = obs[-1 - yoy_periods][1]
        s["yoy_pct"] = round((value / prev - 1) * 100, 2)
    return s


YOY = {"raleigh_hpi": 4, "durham_hpi": 4, "mortgage_30y": 52, "mortgage_15y": 52, "raleigh_unemployment": 12}


def fetch_all(api_key=None):
    api_key = api_key or os.getenv("FRED_API_KEY") or None
    out, errors = {}, {}
    for name, sid in FRED_SERIES.items():
        try:
            out[name] = {"series_id": sid, **summarize(fetch_series(sid, api_key), YOY.get(name))}
        except Exception as e:
            errors[name] = str(e)[:200]
    return out, errors
