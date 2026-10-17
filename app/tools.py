"""Deterministic tools the Nemotron agents call. No LLM in here — pure data + math."""
import csv, json, math, os

from . import live

DATA = os.path.join(os.path.dirname(__file__), "..", "data")

with open(os.path.join(DATA, "listings.csv")) as f:
    LISTINGS = []
    for r in csv.DictReader(f):
        for k in ("id", "price", "beds", "sqft", "year_built", "hoa_monthly", "days_on_market"):
            r[k] = int(r[k])
        for k in ("lat", "lon", "baths", "lot_acres"):
            r[k] = float(r[k])
        r["features"] = r["features"].split("|")
        LISTINGS.append(r)
BY_ID = {r["id"]: r for r in LISTINGS}

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
FEATURES = sorted({f for r in LISTINGS for f in r["features"]})


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
def search_listings(max_price=None, min_beds=None, min_baths=None, cities=None,
                    property_types=None, must_have_features=None, limit=15):
    out = []
    for r in LISTINGS:
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
    out.sort(key=lambda r: (-sum(f in r["features"] for f in feats), r["price"]))
    return [{k: r[k] for k in ("id", "address", "neighborhood", "city", "price", "beds", "baths",
                               "sqft", "property_type", "features")} for r in out[:limit]]


def neighborhood_report(listing_id, commute_destination="downtown raleigh"):
    r = BY_ID.get(int(listing_id))
    if not r:
        return {"error": f"listing {listing_id} not found"}
    h = HOODS[r["neighborhood"]]
    dname, d = _dest(commute_destination)
    miles = _miles((r["lat"], r["lon"]), d)
    minutes = round(6 + miles * 1.9)  # rough Triangle drive time incl. traffic
    lv = live.safety(h["name"])
    return {"listing_id": r["id"], "neighborhood": h["name"],
            "safety_index": lv["safety_index"] if lv else h["safety_index"],
            "safety_source": f"{lv['source']} ({lv['scope']}, {lv['incidents']} incidents)" if lv else "baseline",
            "school_rating": h["school_rating"], "walk_score": h["walk_score"],
            "commute_to": dname, "commute_minutes": minutes}


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
# Math criteria are deterministic formulas. "safety" and "fit" are functional judgements from Jev
# (app/jev.py) when a Jev decision is supplied, otherwise live index / rules.
CRITERIA = ["affordability", "safety", "schools", "commute", "space", "features", "value", "fit"]


def math_inputs(listing_id, profile):
    """Numbers handed to Jev as pre-computed facts (Jev never does arithmetic)."""
    n = neighborhood_report(listing_id, profile.get("commute_destination") or "downtown raleigh")
    a = affordability(listing_id, profile.get("annual_income"), profile.get("down_payment_pct", 20))
    return {"monthly": a["monthly_total"], "rate": a["rate_pct"], "ratio": a.get("housing_ratio_pct"),
            "vs_area": a["price_vs_area_pct"], "commute_min": n["commute_minutes"], "safety_index": n["safety_index"]}


def criterion_scores(listing_id, profile, jev=None):
    r = BY_ID[int(listing_id)]
    n = neighborhood_report(listing_id, profile.get("commute_destination") or "downtown raleigh")
    a = affordability(listing_id, profile.get("annual_income"), profile.get("down_payment_pct", 20))
    budget = profile.get("max_price") or r["price"]
    afford = max(0, min(100, 100 - (r["price"] - 0.75 * budget) / (0.30 * budget) * 100)) if budget else 50
    if a.get("housing_ratio_pct"):
        afford = min(afford, max(0, 100 - max(0, a["housing_ratio_pct"] - 28) * 6))
    commute = max(0, min(100, 100 - (n["commute_minutes"] - 10) * 2.5))
    want_beds = profile.get("min_beds") or 3
    space = max(0, min(100, 60 + (r["beds"] - want_beds) * 15 + (r["sqft"] - 1800) / 40))
    wants = [f.lower() for f in profile.get("must_have_features") or []]
    features = 100 * sum(f in r["features"] for f in wants) / len(wants) if wants else min(100, len(r["features"]) * 14)
    value = max(0, min(100, 60 - a["price_vs_area_pct"] * 2))
    safety = n["safety_index"]
    fit = round(0.6 * features + 0.4 * space)
    if jev:
        safety = round(0.5 * safety + 0.5 * jev["safety"]) if jev.get("safety") is not None else safety
        fit = jev["fit"] if jev.get("fit") is not None else fit
    return {"affordability": round(afford), "safety": safety, "schools": n["school_rating"] * 10,
            "commute": round(commute), "space": round(space), "features": round(features), "value": round(value),
            "fit": fit}


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
    "neighborhood_report": _fn("neighborhood_report", "Safety index, school rating, walk score and commute minutes for a listing.", {
        "listing_id": {"type": "integer"}, "commute_destination": {"type": "string"}}, ["listing_id"]),
    "affordability": _fn("affordability", "Monthly payment estimate (PITI + HOA) at the live FRED 30-yr rate, housing ratio and price vs neighborhood median.", {
        "listing_id": {"type": "integer"}, "annual_income": {"type": "integer"},
        "down_payment_pct": {"type": "number"}}, ["listing_id"]),
}
IMPLS = {"search_listings": search_listings, "neighborhood_report": neighborhood_report,
         "affordability": affordability}
