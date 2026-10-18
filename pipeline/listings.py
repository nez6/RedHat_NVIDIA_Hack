"""Listing ingestion + data quality: Zillow saved pages and live feeds -> one clean, de-duplicated listing table.

    python -m pipeline.listings                      # parse data/zillow/saved/*.html + data/redfin/*.csv -> data/zillow/listings.json
    python -m pipeline.listings --location "Cary, NC"  # also pull live listings for that area (RENTCAST_API_KEY)

Data-quality rules (the "delete duplicate / non-existent data" step on the architecture slide):
  1. drop records that are not for sale (sold, pending, off-market, rentals)
  2. drop records missing what scoring needs (price, beds, sqft, coordinates) or with impossible values
  3. de-duplicate on normalized street address + ZIP (falls back to zpid / MLS id); richer record wins
  4. map each home to the nearest Triangle neighborhood profile (school, walk score, price/sqft, safety)
  5. extract the feature vocabulary the Lifestyle scorer uses from the description and Zillow facts
Each step is counted in the quality report written next to the listings.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys

from .config import DATA_DIR
from .sources import free_data, redfin, rentcast, zillow

ZILLOW_DIR = os.path.join(DATA_DIR, "zillow")
SAVED_DIR = os.getenv("ZILLOW_SAVED_DIR", os.path.join(ZILLOW_DIR, "saved"))
OUT_PATH = os.getenv("ZILLOW_LISTINGS_PATH", os.path.join(ZILLOW_DIR, "listings.json"))
REDFIN_DIR = os.getenv("REDFIN_SAVED_DIR", os.path.join(DATA_DIR, "redfin"))
MAX_NEIGHBORHOOD_MILES = 8.0
SOURCE_RANK = {"zillow_saved_page": 3, "zillow_upload": 3, "redfin_csv": 3, "rentcast_api": 2}

FEATURE_PATTERNS = {
    "fenced yard": r"fenced (back)?yard|fully fenced|privacy fence", "home office": r"home office|\boffice\b|\bstudy\b|\bden\b",
    "garage": r"garage", "pool": r"\bpool\b", "hardwood floors": r"hardwood", "updated kitchen": r"(updated|renovated|remodeled|new) kitchen|quartz|granite",
    "open floor plan": r"open (floor ?plan|concept|layout)", "screened porch": r"screened( in)? porch",
    "first-floor primary": r"(first|main)[- ]floor (primary|owner|master)", "finished basement": r"finished basement",
    "walk-in closet": r"walk[- ]in closet", "solar panels": r"solar", "EV charger": r"\bev charg|electric vehicle",
    "smart home": r"smart home|smart thermostat|nest", "energy efficient": r"energy (efficient|star)|spray foam",
    "mature trees": r"mature trees|wooded|tree[- ]lined", "near greenway": r"greenway|trail", "sidewalks": r"sidewalk",
}


def _miles(a, b):
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 3958.8 * math.asin(math.sqrt(h))


def _hoods():
    """Triangle neighborhood profiles + areas of every city looked up live (pipeline/city.py)."""
    from . import city
    with open(os.path.join(DATA_DIR, "neighborhoods.json")) as f:
        hoods = json.load(f)
    return hoods + [a for doc in city.all_cached() for a in doc.get("areas") or []]


def features(rec):
    text = " ".join([rec.get("description") or "", json.dumps(rec.get("facts") or {})]).lower()
    found = [f for f, pat in FEATURE_PATTERNS.items() if re.search(pat, text)]
    facts = rec.get("facts") or {}
    if facts.get("hasGarage") and "garage" not in found:
        found.append("garage")
    if facts.get("hasPrivatePool") and "pool" not in found:
        found.append("pool")
    return found


def _addr_key(r):
    a = re.sub(r"[^a-z0-9 ]", "", (r.get("address") or "").lower())
    a = re.sub(r"\b(street|st|road|rd|drive|dr|avenue|ave|lane|ln|court|ct|way|place|pl|circle|cir|boulevard|blvd)\b", "", a)
    a = re.sub(r"\s+", " ", a).strip()
    return f"{a}|{(r.get('zip') or '')[:5]}" if a else None


def listing_id(r):
    """Stable integer id: the zpid when there is one, else a hash of the source id / address (9-prefixed)."""
    if r.get("zpid") and str(r["zpid"]).isdigit():
        return int(r["zpid"])
    basis = r.get("external_id") or r.get("mls_number") or _addr_key(r) or json.dumps(r, sort_keys=True, default=str)
    return int("9" + str(int(hashlib.sha1(str(basis).encode()).hexdigest()[:12], 16))[:11])


def _richness(r):
    return SOURCE_RANK.get(r.get("source"), 1) * 100 + sum(v not in (None, "", [], {}) for v in r.values())


def clean(records, hoods=None):
    """Raw records from any source -> (listings in the app's schema, quality report)."""
    hoods = hoods or _hoods()
    report = {"input": len(records), "dropped_not_for_sale": 0, "dropped_incomplete": 0, "dropped_duplicate": 0,
              "dropped_out_of_area": 0, "kept": 0, "by_source": {}}
    best = {}
    for r in records:
        status = (r.get("status") or "FOR_SALE").upper()
        if status in zillow.OFF_MARKET or "SOLD" in status or "RENT" in status:
            report["dropped_not_for_sale"] += 1
            continue
        price, beds, sqft = r.get("price"), r.get("beds"), r.get("sqft")
        if not (price and 25_000 <= price <= 20_000_000 and beds is not None and 0 <= beds <= 15 and sqft and 200 <= sqft <= 20_000
                and r.get("lat") and r.get("lon") and r.get("address")):
            report["dropped_incomplete"] += 1
            continue
        key = _addr_key(r) or f"id:{r.get('zpid') or r.get('external_id')}"
        if key in best:
            report["dropped_duplicate"] += 1
            if _richness(r) <= _richness(best[key]):
                continue
        best[key] = r
    out = []
    year = dt.date.today().year
    for r in best.values():
        hood, miles = min(((h, _miles((r["lat"], r["lon"]), (h["lat"], h["lon"]))) for h in hoods), key=lambda x: x[1])
        if miles > MAX_NEIGHBORHOOD_MILES:
            report["dropped_out_of_area"] += 1
            continue
        ratings = [s["rating"] for s in r.get("schools") or [] if s.get("rating")]
        out.append({
            "id": listing_id(r), "address": r["address"], "neighborhood": hood["name"], "city": r.get("city") or hood["city"],
            "lat": round(float(r["lat"]), 6), "lon": round(float(r["lon"]), 6), "price": int(r["price"]), "beds": int(r["beds"]),
            "baths": float(r.get("baths") or max(1, r["beds"] - 1)), "sqft": int(r["sqft"]),
            "year_built": int(r.get("year_built") or year - 25), "lot_acres": float(r.get("lot_acres") or 0),
            "hoa_monthly": int(r.get("hoa_monthly") or 0), "property_type": r.get("home_type") or "Single Family",
            "features": features(r), "days_on_market": int(r.get("days_on_market") or 0),
            # extra provenance + Zillow fields the scorers and UI can use
            "zip": r.get("zip"), "state": r.get("state"), "url": r.get("url"), "source": r.get("source"),
            "description": (r.get("description") or "")[:2000] or None,
            "school_rating": round(sum(ratings) / len(ratings)) if ratings else None,
            "schools": r.get("schools") or [], "neighborhood_match_miles": round(miles, 2),
            "year_built_estimated": not r.get("year_built"), "hoa_known": r.get("hoa_monthly") is not None,
        })
        report["by_source"][r.get("source")] = report["by_source"].get(r.get("source"), 0) + 1
    report["kept"] = len(out)
    out.sort(key=lambda x: x["price"])
    return out, report


def load_saved(saved_dir=None):
    return zillow.parse_dir(saved_dir or SAVED_DIR)


def load_redfin(redfin_dir=None):
    """Every Redfin "Download All" CSV in data/redfin/. Returns (records, per-file log)."""
    d, recs, log = redfin_dir or REDFIN_DIR, [], {}
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if name.lower().endswith(".csv"):
            with open(os.path.join(d, name), encoding="utf-8", errors="replace") as f:
                rows = redfin.parse_csv(f.read(), name)
            log[name] = f"{len(rows)} listings"
            recs.extend(rows)
    return recs, log


def _in_wake(r):
    return (r.get("state") or "NC").upper() == "NC" and str(r.get("zip") or "")[:3] in ("275", "276")


def enrich(rows, get=None, schools=None):
    """Best-effort free enrichment per listing: Wake County tax record, EPA walkability, nearest-school proficiency.
    Network failures never drop a listing; each added field names its source."""
    if schools is None:
        from . import reference
        schools = reference.load_schools()
    counts = {"wake_parcel": 0, "walkability": 0, "schools": 0}
    for r in rows:
        if _in_wake(r):
            try:
                p = free_data.wake_parcel(r["address"], r.get("zip"), get=get)
            except Exception:
                p = None
            if p:
                if r.get("year_built_estimated") and p.get("year_built"):
                    r["year_built"], r["year_built_estimated"] = int(p["year_built"]), False
                r.update(assessed_value=p.get("assessed_value"), last_sale_price=p.get("last_sale_price"),
                         last_sale_date=p.get("last_sale_date"), county_heated_sqft=p.get("heated_sqft"), tax_record_source=p["source"])
                counts["wake_parcel"] += 1
        try:
            w = free_data.epa_walkability(r["lat"], r["lon"], get=get)
        except Exception:
            w = None
        if w:
            r.update(walk_score=w["walk_score"], walk_source="EPA National Walkability Index")
            counts["walkability"] += 1
        if not r.get("school_rating") and schools:
            sr = free_data.nearby_school_rating(r["lat"], r["lon"], schools)
            if sr:
                r.update(school_rating=sr["school_rating"], school_source=sr["source"], schools=sr["schools"])
                counts["schools"] += 1
    return counts


def write(listings, report, path=None):
    path = path or OUT_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    doc = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "quality": report, "listings": listings}
    with open(path + ".tmp", "w") as f:
        json.dump(doc, f, indent=1, default=str)
    os.replace(path + ".tmp", path)
    return path


def build(location=None, saved_dir=None, out_path=None, live_fetch=None):
    """Saved Zillow pages (+ live listings for `location` when a feed is configured) -> data/zillow/listings.json."""
    raw, files = load_saved(saved_dir)
    rf, rf_files = load_redfin()
    raw.extend(rf)
    files.update(rf_files)
    live_meta = None
    if location and (live_fetch or rentcast.configured()):
        recs, live_meta = (live_fetch or rentcast.fetch)(location)
        raw.extend(recs)
    listings, report = clean(raw)
    if os.getenv("LISTINGS_ENRICH", "1") == "1" and listings:
        report["enriched"] = enrich(listings)
    report.update({"saved_pages": files, "live": live_meta})
    write(listings, report, out_path)
    return listings, report


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--location", help="city, 'City, ST' or ZIP for a live pull (needs RENTCAST_API_KEY)")
    a = ap.parse_args(argv)
    _, report = build(a.location)
    print(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
