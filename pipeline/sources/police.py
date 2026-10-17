"""Live police incident feeds.

Raleigh: City of Raleigh NIBRS incidents (ArcGIS FeatureServer, has lat/lon) -> per-neighborhood radius counts.
Cary:    Town of Cary CPD incidents (Opendatasoft, no coordinates)       -> town-level density.
"""
import datetime as dt

from .. import http
from ..config import CARY_INCIDENTS_URL, CARY_LOOKBACK_DAYS, LOOKBACK_DAYS, RALEIGH_INCIDENTS_URL

PAGE = 2000


def _since(days):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days))


def normalize_raleigh(feature):
    a = feature.get("attributes", {})
    lat, lon = a.get("latitude"), a.get("longitude")
    if lat is None or lon is None or not (34 < lat < 37) or not (-80 < lon < -77):
        return None
    ts = a.get("reported_date")
    return {
        "source": "raleigh_pd",
        "case": a.get("case_number"),
        "date": dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc).date().isoformat() if ts else None,
        "crime_type": _ctype(a.get("crime_type")),
        "category": (a.get("crime_category") or "").upper(),
        "description": a.get("crime_description") or "",
        "lat": float(lat), "lon": float(lon),
    }


def _ctype(v):
    v = (v or "").strip().upper()
    return v if v.startswith("CRIMES AGAINST") else "UNCLASSIFIED"


def fetch_raleigh(days=LOOKBACK_DAYS, max_records=20000):
    since = _since(days).strftime("%Y-%m-%d %H:%M:%S")
    out, offset = [], 0
    while offset < max_records:
        payload = http.get(RALEIGH_INCIDENTS_URL, {
            "where": f"reported_date >= TIMESTAMP '{since}'",
            "outFields": "case_number,crime_type,crime_category,crime_description,reported_date,latitude,longitude",
            "returnGeometry": "false", "orderByFields": "reported_date DESC",
            "resultOffset": offset, "resultRecordCount": PAGE, "f": "json"})
        if "error" in payload:
            raise RuntimeError(f"ArcGIS error: {payload['error']}")
        feats = payload.get("features", [])
        out.extend(r for r in (normalize_raleigh(f) for f in feats) if r)
        if len(feats) < PAGE and not payload.get("exceededTransferLimit"):
            break
        offset += PAGE
    return out


# Cary uses offense names, map them to the same 3 NIBRS buckets
_PERSON = ("ASSAULT", "ROBBERY", "HOMICIDE", "KIDNAP", "SEX", "RAPE", "INTIMIDATION")
_SOCIETY = ("DRUG", "WEAPON", "DUI", "DRIVING", "LIQUOR", "DISORDERLY", "TRESPASS", "PORNOGRAPHY", "GAMBLING")


def classify_offense(name):
    n = (name or "").upper()
    if any(k in n for k in _PERSON):
        return "CRIMES AGAINST PERSONS"
    if any(k in n for k in _SOCIETY):
        return "CRIMES AGAINST SOCIETY"
    return "CRIMES AGAINST PROPERTY"


def fetch_cary(days=CARY_LOOKBACK_DAYS):
    """Server-side aggregation: one call returns counts per offense for the lookback window."""
    since = _since(days).strftime("%Y-%m-%d")
    payload = http.get(CARY_INCIDENTS_URL, {"where": f"date_from >= date'{since}'",
                                            "select": "crime_type, count(*) as n", "group_by": "crime_type",
                                            "limit": 100})
    counts = {}
    for r in payload.get("results", []):
        counts[r.get("crime_type") or "UNKNOWN"] = int(r.get("n") or 0)
    return counts
