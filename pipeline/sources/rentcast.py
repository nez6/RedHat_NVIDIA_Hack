"""Live for-sale listings by location from the RentCast API (licensed, MLS-sourced; free plan = 50 requests/month).

    GET https://api.rentcast.io/v1/listings/sale?city=Cary&state=NC&status=Active&limit=100   (header X-Api-Key)

Set RENTCAST_API_KEY to enable. Responses are cached per location for RENTCAST_CACHE_HOURS (default 24) under
data/listings_cache/ so a demo or a busy day never burns the monthly quota.
"""
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request

from ..config import DATA_DIR, HTTP_TIMEOUT, USER_AGENT

URL = "https://api.rentcast.io/v1/listings/sale"
CACHE_DIR = os.path.join(DATA_DIR, "listings_cache")
TYPES = {"Single Family": "Single Family", "Condo": "Condo", "Townhouse": "Townhouse", "Multi-Family": "Multi-Family",
         "Manufactured": "Manufactured", "Apartment": "Condo", "Land": "Land"}


def configured():
    return bool(os.getenv("RENTCAST_API_KEY"))


def location_params(location, default_state="NC"):
    """'27513' -> zipCode; 'Cary, NC' / 'Cary' -> city + state."""
    loc = (location or "").strip()
    if re.fullmatch(r"\d{5}", loc):
        return {"zipCode": loc}
    parts = [p.strip() for p in loc.split(",") if p.strip()]
    if not parts:
        raise ValueError("location is empty")
    state = parts[1][:2].upper() if len(parts) > 1 else default_state
    return {"city": parts[0].title(), "state": state}


def _get(params, transport=None):
    if transport:
        return transport(params)
    req = urllib.request.Request(URL + "?" + urllib.parse.urlencode(params),
                                 headers={"X-Api-Key": os.environ["RENTCAST_API_KEY"], "Accept": "application/json",
                                          "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read().decode())


def fetch(location, limit=100, transport=None, cache_dir=CACHE_DIR, max_age_h=None):
    """Active sale listings for a city/ZIP. Returns (records, meta)."""
    params = {**location_params(location), "status": "Active", "limit": int(limit)}
    key = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]
    path = os.path.join(cache_dir, f"rentcast_{key}.json")
    max_age = 3600 * float(max_age_h if max_age_h is not None else os.getenv("RENTCAST_CACHE_HOURS", "24"))
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age:
        with open(path) as f:
            raw = json.load(f)
        meta = {"cached": True}
    else:
        raw = _get(params, transport)
        os.makedirs(cache_dir, exist_ok=True)
        with open(path + ".tmp", "w") as f:
            json.dump(raw, f)
        os.replace(path + ".tmp", path)
        meta = {"cached": False}
    return [normalize(r) for r in (raw if isinstance(raw, list) else raw.get("data", []))], {**meta, "params": params}


def normalize(r):
    return {
        "zpid": None, "external_id": r.get("id"), "mls_number": r.get("mlsNumber"),
        "address": r.get("addressLine1") or (r.get("formattedAddress") or "").split(",")[0],
        "city": r.get("city"), "state": r.get("state"), "zip": r.get("zipCode"),
        "lat": r.get("latitude"), "lon": r.get("longitude"),
        "price": r.get("price"), "beds": r.get("bedrooms"), "baths": r.get("bathrooms"),
        "sqft": r.get("squareFootage"), "year_built": r.get("yearBuilt"),
        "lot_acres": round(r["lotSize"] / 43560, 3) if r.get("lotSize") else None,
        "hoa_monthly": (r.get("hoa") or {}).get("fee"),
        "home_type": TYPES.get(r.get("propertyType"), r.get("propertyType")),
        "status": "FOR_SALE" if (r.get("status") or "").lower() == "active" else (r.get("status") or "").upper(),
        "days_on_market": r.get("daysOnMarket"), "description": None, "facts": {}, "schools": [], "url": None,
        "source": "rentcast_api", "source_file": None,
    }
