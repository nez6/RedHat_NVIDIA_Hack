"""Deterministic tools the Nemotron agents call. No LLM in here — pure data + math."""
import csv, json, math, os, re, threading

from . import live

DATA = os.path.join(os.path.dirname(__file__), "..", "data")

# Listing sources (LISTINGS_SOURCE): "sample" = the 61 synthetic demo homes, "zillow" = parsed Zillow pages + live
# feeds only (data/zillow/listings.json, built by `python -m pipeline.listings`), "both" (default) = Zillow first,
# sample homes as a fallback so the demo never runs empty.
LISTINGS_SOURCE = os.getenv("LISTINGS_SOURCE", "both").lower()
ZILLOW_LISTINGS = os.getenv("ZILLOW_LISTINGS_PATH", os.path.join(DATA, "zillow", "listings.json"))
_LOCK = threading.Lock()


def _sample():
    out = []
    with open(os.path.join(DATA, "listings.csv")) as f:
        for r in csv.DictReader(f):
            for k in ("id", "price", "beds", "sqft", "year_built", "hoa_monthly", "days_on_market"):
                r[k] = int(r[k])
            for k in ("lat", "lon", "baths", "lot_acres"):
                r[k] = float(r[k])
            r["features"] = r["features"].split("|")
            r["source"] = "synthetic_sample"
            out.append(r)
    return out


def _zillow():
    try:
        with open(ZILLOW_LISTINGS) as f:
            return json.load(f).get("listings", [])
    except (OSError, ValueError):
        return []


_Z = _zillow() if LISTINGS_SOURCE in ("zillow", "both") else []
LISTINGS = _Z + (_sample() if LISTINGS_SOURCE in ("sample", "both") or not _Z else [])  # never start empty
BY_ID = {r["id"]: r for r in LISTINGS}


def add_listings(rows):
    """Register freshly parsed / fetched listings in the in-memory store (new ids only). Returns ids added."""
    added = []
    with _LOCK:
        for r in rows:
            if r["id"] not in BY_ID:
                LISTINGS.append(r)
                BY_ID[r["id"]] = r
                added.append(r["id"])
    return added


with open(os.path.join(DATA, "neighborhoods.json")) as f:
    HOODS = {h["name"]: h for h in json.load(f)}

DESTINATIONS = {
    "downtown raleigh": (35.7796, -78.6382),
    "rtp": (35.8992, -78.8636),
    "research triangle park": (35.8992, -78.8636),
    "duke": (36.0014, -78.9382),
    "unc": (35.9049, -79.0469),
    "nc state": (35.7847, -78.6821),
    "downtown durham": (35.9940, -78.8986),
    "rdu airport": (35.8801, -78.7880),
    "cary": (35.7915, -78.7811),
}
CITIES = sorted({r["city"] for r in LISTINGS})
try:  # the Zillow parser's feature vocabulary (pipeline/ may be absent when NeMo Gym loads app/ on its own)
    from pipeline.listings import FEATURE_PATTERNS as _VOCAB
except ImportError:
    _VOCAB = {}
FEATURES = sorted({f for r in LISTINGS for f in r["features"]} | set(_VOCAB))


def _miles(a, b):
    R = 3958.8
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def _dest(name):
    n = (name or "").lower().strip()
    for k, v in DESTINATIONS.items():
        if k in n or n in k:
            return k, v
    return "downtown raleigh", DESTINATIONS["downtown raleigh"]


# ---------------- tool implementations ----------------
def area_terms(location):
    """'Cary, NC; 27513, Apex' -> ['cary', '27513', 'apex'] (2-letter state codes dropped)."""
    return [t for t in (x.strip().lower() for x in re.split(r"[,;/|]", location or "")) if t and not re.fullmatch(r"[a-z]{2}", t)]


def _in_area(r, location):
    """True when a listing matches any of the areas typed: city, ZIP, neighborhood or street."""
    terms = area_terms(location)
    if not terms:
        return True
    hay = " ".join(str(r.get(k) or "") for k in ("city", "zip", "neighborhood", "address")).lower()
    return any(t in hay for t in terms)


def search_listings(max_price=None, min_beds=None, min_baths=None, cities=None,
                    property_types=None, must_have_features=None, limit=15, location=None):
    out = []
    for r in LISTINGS:
        if location and not _in_area(r, location):
            continue
        if max_price and r["price"] > max_price * 1.05:  # 5% stretch
            continue
        if min_beds and r["beds"] < min_beds:
            continue
        if min_baths and r["baths"] < min_baths:
            continue
        if cities and r["city"].lower() not in [c.lower() for c in cities]:
            continue
        if property_types and r["property_type"].lower() not in [p.lower() for p in property_types]:
            continue
        out.append(r)
    feats = [f.lower() for f in (must_have_features or [])]
    # real listings (Zillow / live feed) rank ahead of the synthetic sample, then must-have matches, then price
    out.sort(key=lambda r: (r.get("source") == "synthetic_sample", -sum(f in r["features"] for f in feats), r["price"]))
    return [{k: r.get(k) for k in ("id", "address", "neighborhood", "city", "price", "beds", "baths",
                                   "sqft", "property_type", "features", "source")} for r in out[:limit]]


_MAPS_CACHE = {}


def google_drive_minutes(origin, destination, transport=None):
    """Drive time from Google Maps Distance Matrix (needs GOOGLE_MAPS_API_KEY). None when unavailable."""
    key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not key and not transport:
        return None
    ck = (round(origin[0], 4), round(origin[1], 4), destination.lower())
    if ck in _MAPS_CACHE:
        return _MAPS_CACHE[ck]
    import urllib.parse
    import urllib.request
    params = {"origins": f"{origin[0]},{origin[1]}", "destinations": destination, "mode": "driving",
              "departure_time": "now", "key": key or "test"}
    try:
        if transport:
            data = transport(params)
        else:
            url = "https://maps.googleapis.com/maps/api/distancematrix/json?" + urllib.parse.urlencode(params)
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read().decode())
        el = data["rows"][0]["elements"][0]
        secs = (el.get("duration_in_traffic") or el["duration"])["value"]
        _MAPS_CACHE[ck] = round(secs / 60)
    except Exception:
        _MAPS_CACHE[ck] = None
    return _MAPS_CACHE[ck]


def neighborhood_report(listing_id, commute_destination="downtown raleigh"):
    r = BY_ID.get(int(listing_id))
    if not r:
        return {"error": f"listing {listing_id} not found"}
    h = HOODS[r["neighborhood"]]
    dname, d = _dest(commute_destination)
    gm = google_drive_minutes((r["lat"], r["lon"]), commute_destination) if commute_destination else None
    if gm is not None:
        minutes, dname, src = gm, commute_destination, "google_maps_distance_matrix"
    else:
        minutes, src = round(6 + _miles((r["lat"], r["lon"]), d) * 1.9), "estimate"  # rough Triangle drive time incl. traffic
    lv = live.safety(h["name"])
    return {"listing_id": r["id"], "neighborhood": h["name"],
            "safety_index": lv["safety_index"] if lv else h["safety_index"],
            "safety_source": f"{lv['source']} ({lv['scope']}, {lv['incidents']} incidents)" if lv else "baseline",
            "school_rating": r.get("school_rating") or h["school_rating"], "walk_score": h["walk_score"],
            "commute_to": dname, "commute_minutes": minutes, "commute_source": src}


def find_listings(location=None, max_price=None, min_beds=None, min_baths=None, property_types=None,
                  must_have_features=None, limit=12, live_fetch=None):
    """Zillow Listing Agent tool: live listings for the area (when a feed is configured) + parsed Zillow pages,
    cleaned and de-duplicated, then filtered to the buyer's must-haves."""
    from pipeline import listings as PL
    from pipeline.sources import rentcast, zillow
    feed = {"zillow_search_url": zillow.search_url(location) if location else None, "live_feed": "not configured"}
    if location and (live_fetch or rentcast.configured()):
        try:
            recs, meta = (live_fetch or rentcast.fetch)(location)
            rows, report = PL.clean(recs)
            feed.update(live_feed="rentcast_api" + (" (cached)" if meta.get("cached") else ""), live_added=len(add_listings(rows)),
                        quality={k: report[k] for k in ("input", "dropped_not_for_sale", "dropped_incomplete", "dropped_duplicate", "kept")})
        except Exception as e:
            feed["live_feed"] = f"unavailable: {str(e)[:120]}"
    kw = dict(max_price=max_price, min_beds=min_beds, min_baths=min_baths, property_types=property_types,
              must_have_features=must_have_features, limit=limit)
    found = search_listings(location=location, **kw)
    feed["matched_area"] = bool(found) or not location
    if location and len(found) < 4:  # widen to the whole Triangle rather than return nothing
        seen = {r["id"] for r in found}
        found += [r for r in search_listings(**kw) if r["id"] not in seen][: max(0, limit - len(found))]
        feed["widened"] = True
    feed["sources"] = sorted({r.get("source") or "unknown" for r in found})
    return {"listings": found, **feed}


def affordability(listing_id, annual_income=None, down_payment_pct=20, rate_pct=None,
                  property_tax_pct=1.0):
    r = BY_ID.get(int(listing_id))
    if not r:
        return {"error": f"listing {listing_id} not found"}
    rate_src = {"source": "user"}
    if rate_pct is None:
        rate_pct, rate_src = live.mortgage_rate()
    loan = r["price"] * (1 - down_payment_pct / 100)
    m = rate_pct / 100 / 12
    pi = loan * m / (1 - (1 + m) ** -360)
    tax = r["price"] * property_tax_pct / 100 / 12
    ins = r["price"] * 0.0035 / 12
    total = pi + tax + ins + r["hoa_monthly"]
    res = {"listing_id": r["id"], "price": r["price"], "down_payment": round(r["price"] * down_payment_pct / 100),
           "monthly_total": round(total), "principal_interest": round(pi), "tax_ins_hoa": round(tax + ins + r["hoa_monthly"]),
           "rate_pct": rate_pct, "rate_source": rate_src.get("source"), "rate_as_of": rate_src.get("as_of")}
    if annual_income:
        res["housing_ratio_pct"] = round(total / (annual_income / 12) * 100, 1)
    hood = HOODS[r["neighborhood"]]
    res["price_vs_area_pct"] = round((r["price"] / r["sqft"]) / hood["median_ppsf"] * 100 - 100, 1)
    return res


# ---------------- decision matrix ----------------
# 10 homebuyer-matrix criteria, one per metric agent (see app/metrics.py and app/metric_agents.py).
# Math criteria are deterministic; "safety" and "lifestyle" blend in Jev judgements when available.
CRITERIA = ["lifestyle", "school", "safety", "environment", "urban", "commute", "forecast", "deal", "rooms", "maintenance"]


def math_inputs(listing_id, profile):
    """Numbers handed to Jev as pre-computed facts (Jev never does arithmetic)."""
    n = neighborhood_report(listing_id, profile.get("commute_destination") or "downtown raleigh")
    a = affordability(listing_id, profile.get("annual_income"), profile.get("down_payment_pct", 20))
    return {"monthly": a["monthly_total"], "rate": a["rate_pct"], "ratio": a.get("housing_ratio_pct"),
            "vs_area": a["price_vs_area_pct"], "commute_min": n["commute_minutes"], "safety_index": n["safety_index"]}


def criterion_scores(listing_id, profile, jev=None):
    from . import metrics  # lazy: metrics imports this module
    return metrics.scores(listing_id, profile, jev)


def score_matrix(listing_ids, profile, weights, jev=None):
    w = {c: float(weights.get(c, 1)) for c in CRITERIA}
    tw = sum(w.values()) or 1
    rows = []
    for lid in listing_ids:
        if int(lid) not in BY_ID:
            continue
        j = (jev or {}).get(str(lid)) or (jev or {}).get(int(lid))
        s = criterion_scores(lid, profile, j)
        total = sum(s[c] * w[c] for c in CRITERIA) / tw
        rows.append({"listing": BY_ID[int(lid)], "scores": s, "total": round(total, 1),
                     "neighborhood": neighborhood_report(lid, profile.get("commute_destination") or "downtown raleigh"),
                     "finance": affordability(lid, profile.get("annual_income"), profile.get("down_payment_pct", 20)),
                     "jev": j})
    rows.sort(key=lambda x: -x["total"])
    return rows


# ---------------- OpenAI-style tool schemas ----------------
def _fn(name, desc, props, req=()):
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": list(req)}}}

SCHEMAS = {
    "set_buyer_profile": _fn("set_buyer_profile", "Record the buyer's structured dream-home profile and criterion weights (0-5).", {
        "max_price": {"type": "integer", "description": "Max purchase price in USD"},
        "annual_income": {"type": "integer"},
        "down_payment_pct": {"type": "number"},
        "min_beds": {"type": "integer"}, "min_baths": {"type": "number"},
        "cities": {"type": "array", "items": {"type": "string", "enum": CITIES}},
        "property_types": {"type": "array", "items": {"type": "string", "enum": ["Single Family", "Townhouse", "Condo"]}},
        "must_have_features": {"type": "array", "items": {"type": "string", "enum": FEATURES}},
        "commute_destination": {"type": "string", "enum": sorted(set(DESTINATIONS))},
        "max_commute_min": {"type": "integer", "description": "Longest acceptable commute in minutes"},
        "urban_target": {"type": "string", "enum": sorted(set(DESTINATIONS)), "description": "Urban hub they want to be near"},
        "min_school_rating": {"type": "integer", "description": "Minimum school rating 1-10"},
        "min_sqft": {"type": "integer"},
        "max_hoa": {"type": "integer", "description": "Max acceptable monthly HOA in USD"},
        "risk_tolerance": {"type": "string", "enum": ["low", "medium", "high"]},
        "weights": {"type": "object", "description": "Importance 0-5 for each criterion",
                    "properties": {c: {"type": "number"} for c in CRITERIA}},
        "summary": {"type": "string", "description": "One-sentence recap of the dream home"}},
        ["max_price", "weights", "summary"]),
    "search_listings": _fn("search_listings", "Search the open housing listings database.", {
        "max_price": {"type": "integer"}, "min_beds": {"type": "integer"}, "min_baths": {"type": "number"},
        "cities": {"type": "array", "items": {"type": "string", "enum": CITIES}},
        "property_types": {"type": "array", "items": {"type": "string"}},
        "must_have_features": {"type": "array", "items": {"type": "string", "enum": FEATURES}},
        "limit": {"type": "integer"}}),
    "find_listings": _fn("find_listings", "Find for-sale homes in the buyer's target area: live listings feed + parsed Zillow pages, "
                         "de-duplicated, off-market and incomplete records removed, then filtered to the buyer's must-haves.", {
        "location": {"type": "string", "description": "Target neighborhoods / cities / ZIP codes, comma separated"},
        "max_price": {"type": "integer"}, "min_beds": {"type": "integer"}, "min_baths": {"type": "number"},
        "property_types": {"type": "array", "items": {"type": "string", "enum": ["Single Family", "Townhouse", "Condo"]}},
        "must_have_features": {"type": "array", "items": {"type": "string", "enum": FEATURES}},
        "limit": {"type": "integer"}}),
    "neighborhood_report": _fn("neighborhood_report", "Safety index, school rating, walk score and commute minutes for a listing.", {
        "listing_id": {"type": "integer"}, "commute_destination": {"type": "string"}}, ["listing_id"]),
    "affordability": _fn("affordability", "Monthly payment estimate (PITI + HOA) at the live FRED 30-yr rate, housing ratio and price vs neighborhood median.", {
        "listing_id": {"type": "integer"}, "annual_income": {"type": "integer"},
        "down_payment_pct": {"type": "number"}}, ["listing_id"]),
}
IMPLS = {"search_listings": search_listings, "find_listings": find_listings, "neighborhood_report": neighborhood_report,
         "affordability": affordability}
