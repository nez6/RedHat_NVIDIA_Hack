"""Live city lookup: when a buyer names a city we don't have yet, fetch and parse its public data on the spot.

    python -m pipeline.city "Austin, TX"     # build + cache data/cities/austin-tx.json

Everything is a free, official source (no scraping); results are cached per city for CITY_CACHE_DAYS (default 7):
  OpenStreetMap Nominatim      city -> center + bounding box (1 request, usage-policy compliant User-Agent)
  US Census geocoder           county, metro area (CBSA), unified school district at the center
  Census TIGERweb              census tracts inside the city -> up to CITY_MAX_AREAS "neighborhoods"
  Census ACS 5-year            tract median home value (-> estimated price/sq ft; needs a free CENSUS_API_KEY)
  FEMA, Open-Meteo, NOAA ACIS, EPA walkability   per-area flood, air quality, weather extremes, walkability
  NCES + Urban Institute Education Data API      school locations + % proficient (NC uses the NC DPI file)
  FBI Crime Data Explorer      city police agency's yearly violent + property crime rate vs the US (FBI_API_KEY,
                               falls back to the rate-limited DEMO_KEY); city-level, not per neighborhood
  FRED                         FHFA house-price index for the metro (ATNHPIUS<CBSA>Q)
The Triangle keeps its richer local feeds (Raleigh / Cary police incidents, Wake County parcels, NC DPI scores).
"""
import datetime as dt
import json
import math
import os
import re
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import http, transform
from .config import DATA_DIR
from .sources import environment as ENV, free_data as F, fred

CITY_DIR = os.getenv("CITY_DIR", os.path.join(DATA_DIR, "cities"))
NOMINATIM = "https://nominatim.openstreetmap.org/search"
CENSUS_GEO = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"
TRACTS = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Tracts_Blocks/MapServer/0/query"
ACS = "https://api.census.gov/data/2023/acs/acs5"
EDU = "https://educationdata.urban.org/api/v1/schools/edfacts/assessments/{year}/grade-99/"
FBI = "https://api.usa.gov/crime/fbi/cde"
TYPICAL_SQFT = 1800  # ACS has values but no square footage; used only for the estimated price/sq ft
_JOBS, _LOCK = {}, threading.Lock()


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def parse_location(text):
    """'Austin, TX' -> ('Austin', 'TX'); 'Boise' -> ('Boise', None)."""
    m = re.match(r"\s*([A-Za-z .'-]+?)\s*,?\s+([A-Za-z]{2})\s*$", text or "")
    if m and m.group(2).upper() in _STATES:
        return m.group(1).strip().title(), m.group(2).upper()
    return (text or "").strip().title(), None


_STATES = set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA "
              "RI SC SD TN TX UT VT VA WA WV WI WY".split())


TRIANGLE = {"raleigh", "cary", "apex", "durham", "chapel hill", "carrboro", "wake forest", "holly springs", "garner", "morrisville",
            "knightdale", "fuquay-varina", "rolesville", "wendell", "zebulon", "hillsborough", "rtp", "research triangle park"}


def locations(text):
    """'Cary; Austin, TX, Boise, ID / 27513' -> [('Cary', None), ('Austin', 'TX'), ('Boise', 'ID'), ('27513', None)]."""
    out = []
    for tok in (t.strip() for t in re.split(r"[,;/|]", text or "")):
        if not tok:
            continue
        if out and tok.upper() in _STATES and len(tok) == 2 and out[-1][1] is None:
            out[-1] = (out[-1][0], tok.upper())
            continue
        city, state = parse_location(tok)
        out.append((city, state))
    return out


def needs_lookup(text, known=()):
    """Cities in the text that aren't covered by the Triangle data (ZIPs and known neighborhoods are skipped)."""
    known = {k.lower() for k in known} | TRIANGLE
    todo = []
    for city, state in locations(text):
        c = city.lower()
        if not re.search(r"[a-z]", c) or (c in known and state in (None, "NC")):
            continue
        if state is None and any(c in k or k in c for k in known):
            continue
        todo.append(f"{city}, {state}" if state else city)
    return todo


def _get(get, url, params=None, as_json=True):
    if get:
        return get(url, params or {})
    return http.get(url, params, retries=2, as_json=as_json)


# ------------------------------------------------------------------ geography
def geocode(city, state=None, get=None):
    params = {"city": city, "country": "USA", "format": "json", "limit": 1, "addressdetails": 1}
    if state:
        params["state"] = state
    hits = _get(get, NOMINATIM, params)
    if not hits:
        raise LookupError(f"could not find a US city called {city}{', ' + state if state else ''}")
    h = hits[0]
    s, n, w, e = (float(x) for x in h["boundingbox"])
    iso = (h.get("address") or {}).get("ISO3166-2-lvl4", "")
    return {"name": h.get("name") or city, "display": h.get("display_name"), "lat": float(h["lat"]), "lon": float(h["lon"]),
            "bbox": [w, s, e, n], "state": iso.split("-")[-1] if iso.startswith("US-") else state}


def census_context(lat, lon, get=None):
    d = _get(get, CENSUS_GEO, {"x": lon, "y": lat, "benchmark": "Public_AR_Current", "vintage": "Current_Current", "format": "json",
                                "layers": "all"})
    g = d["result"]["geographies"]
    first = lambda k: (g.get(k) or [{}])[0]  # noqa: E731
    county, cbsa, usd = first("Counties"), first("Metropolitan Statistical Areas"), first("Unified School Districts")
    return {"state_fips": first("States").get("GEOID"), "county_fips": county.get("GEOID"), "county": county.get("NAME"),
            "cbsa": cbsa.get("GEOID"), "metro": cbsa.get("NAME"), "leaid": usd.get("GEOID"), "school_district": usd.get("NAME")}


def tracts(bbox, limit, get=None):
    d = _get(get, TRACTS, {"geometry": ",".join(str(x) for x in bbox), "geometryType": "esriGeometryEnvelope", "inSR": 4326,
                           "spatialRel": "esriSpatialRelIntersects", "outFields": "GEOID,NAME,CENTLAT,CENTLON", "returnGeometry": "false",
                           "f": "json"})
    rows = []
    for f in d.get("features") or []:
        a = f["attributes"]
        try:
            rows.append({"geoid": a["GEOID"], "name": a["NAME"], "lat": float(a["CENTLAT"]), "lon": float(a["CENTLON"])})
        except (KeyError, TypeError, ValueError):
            continue
    rows.sort(key=lambda r: (round(r["lat"], 2), r["lon"]))
    step = max(1, math.ceil(len(rows) / limit))
    return rows[::step][:limit]  # spread evenly across the city


def acs_home_values(state_fips, county_fips, get=None):
    """{tract GEOID: median home value} for one county (ACS 5-year B25077)."""
    params = {"get": "B25077_001E", "for": "tract:*", "in": f"state:{state_fips} county:{county_fips[-3:]}"}
    if os.getenv("CENSUS_API_KEY"):  # the Census API now asks for a (free) key: api.census.gov/data/key_signup.html
        params["key"] = os.getenv("CENSUS_API_KEY")
    rows = _get(get, ACS, params)
    if not isinstance(rows, list):
        raise RuntimeError("Census ACS needs CENSUS_API_KEY")
    out = {}
    for r in rows[1:]:
        try:
            v = int(r[0])
        except (TypeError, ValueError):
            continue
        if v > 0:
            out[r[1] + r[2] + r[3]] = v
    return out


# ------------------------------------------------------------------ schools, crime, prices
def school_scores(leaid, get=None, years=(2020, 2019, 2018)):
    """Schools in the district with location + % proficient (mean of reading and math, all students)."""
    if not leaid:
        return [], None
    for year in years:
        try:
            d = _get(get, EDU.format(year=year), {"leaid": int(leaid)})
        except Exception:
            continue
        prof = {}
        for r in d.get("results") or []:
            if any(r.get(k) not in (99, None) for k in ("race", "sex", "lep", "disability", "econ_disadvantaged")):
                continue
            vals = [r.get(k) for k in ("read_test_pct_prof_midpt", "math_test_pct_prof_midpt") if isinstance(r.get(k), (int, float))]
            if vals:
                prof[str(r["ncessch_num"])] = sum(vals) / len(vals)
        if prof:
            locs = _get(get, F.NCES_SCHOOLS_URL, {"where": f"LEAID='{leaid}'", "outFields": "NCESSCH,SCH_NAME,SCHOOL_LEVEL",
                                                 "returnGeometry": "true", "outSR": 4326, "f": "json"})
            out = []
            for f in locs.get("features") or []:
                a, g = f["attributes"], f.get("geometry") or {}
                key = str(int(a["NCESSCH"])) if str(a.get("NCESSCH", "")).isdigit() else None
                if key in prof and g.get("y"):
                    out.append({"name": a.get("SCH_NAME"), "level": a.get("SCHOOL_LEVEL"), "lat": g["y"], "lon": g["x"], "glp": round(prof[key], 1)})
            return out, year
    return [], None


def fbi_safety(state, county, city, get=None, year=None):
    """City police agency's violent + property crime rate vs the US -> 0-100 safety index (70 = US average)."""
    key = os.getenv("FBI_API_KEY", "DEMO_KEY")
    agencies = _get(get, f"{FBI}/agency/byStateAbbr/{state}", {"API_KEY": key})
    pool = [a for v in (agencies.values() if isinstance(agencies, dict) else [agencies]) for a in v]
    c = city.lower()
    pick = next((a for a in pool if a.get("agency_type_name") == "City" and a.get("agency_name", "").lower().startswith(c)), None)
    cname = (county or "").upper().replace(" COUNTY", "")
    pick = pick or next((a for a in pool if a.get("agency_type_name") == "County" and cname and cname in (a.get("counties") or "")), None)
    if not pick:
        return None
    year = year or dt.date.today().year - 2  # FBI publishes with a lag
    rates = {}
    for offense in ("violent-crime", "property-crime"):
        d = _get(get, f"{FBI}/summarized/agency/{pick['ori']}/{offense}", {"from": f"01-{year}", "to": f"12-{year}", "API_KEY": key})
        r = (d.get("offenses") or {}).get("rates") or {}
        local = next((v for k, v in r.items() if k.endswith("Offenses") and not k.startswith("United States")
                      and pick["agency_name"].split()[0].lower() in k.lower()), None)
        us = r.get("United States Offenses")
        if local and us:
            rates[offense] = (sum(local.values()), sum(us.values()))
    if not rates:
        return None
    weighted = sum((3 if o == "violent-crime" else 1) * loc for o, (loc, _) in rates.items())
    us_weighted = sum((3 if o == "violent-crime" else 1) * us for o, (_, us) in rates.items())
    ratio = weighted / us_weighted if us_weighted else 1
    index = int(max(5, min(98, round(70 - 25 * math.log2(max(ratio, 0.05))))))
    return {"safety_index": index, "ratio_to_us": round(ratio, 2), "agency": pick["agency_name"], "ori": pick["ori"], "year": year,
            "rates_per_100k": {o: round(loc, 1) for o, (loc, _) in rates.items()},
            "source": f"FBI Crime Data Explorer, {pick['agency_name']} {year} (city-wide)"}


def metro_hpi(cbsa, get=None):
    if not cbsa:
        return None
    try:
        obs = fred.parse_csv(_get(get, f"https://fred.stlouisfed.org/graph/fredgraph.csv?id=ATNHPIUS{cbsa}Q", as_json=False)) if not get \
            else get("fred", {"id": f"ATNHPIUS{cbsa}Q"})
        s = fred.summarize(obs, 4)
        return {"yoy_pct": s.get("yoy_pct"), "series_id": f"ATNHPIUS{cbsa}Q", "as_of": s["date"]}
    except Exception:
        return None


# ------------------------------------------------------------------ build
def _area(t, ctx, values, crime, schools, city, get):
    env = {}
    try:
        env["flood"] = ENV.flood_zone(t["lat"], t["lon"]) if not get else get("fema", t)
    except Exception:
        env["flood"] = {"zone": "UNMAPPED", "subtype": None, "sfha": False}
    for k, fn in (("aqi_30d", ENV.mean_aqi), ("weather_12m", F.acis_extreme_days)):
        try:
            env[k] = fn(t["lat"], t["lon"]) if not get else get(k, t)
        except Exception:
            env[k] = None
    try:
        walk = F.epa_walkability(t["lat"], t["lon"], get=get)
    except Exception:
        walk = None
    sr = F.nearby_school_rating(t["lat"], t["lon"], schools) if schools else None
    value = values.get(t["geoid"])
    return {"name": f"{city} · Tract {t['name'].replace('Census Tract ', '')}", "city": city, "lat": round(t["lat"], 5), "lon": round(t["lon"], 5),
            "tract": t["geoid"], "safety_index": (crime or {}).get("safety_index", 65),
            "safety_source": (crime or {}).get("source", "no crime data found (US-average baseline)"),
            "school_rating": (sr or {}).get("school_rating", 6), "school_source": (sr or {}).get("source", "no school data (default 6)"),
            "schools": (sr or {}).get("schools", []),
            "walk_score": (walk or {}).get("walk_score", 40), "walk_source": "EPA National Walkability Index" if walk else "default",
            "median_ppsf": round(value / TYPICAL_SQFT) if value else 200,
            "ppsf_source": f"ACS median home value ÷ {TYPICAL_SQFT} sq ft (estimate)" if value else "default",
            "environment": {**env, "environment_index": transform.environment_index(env), "source": "fema_nfhl+open_meteo+noaa_acis"},
            "center": ctx.get("_center"),
            "generated": True}


def build_city(text, get=None, max_areas=None, workers=8):
    city, state = parse_location(text)
    t0 = time.time()
    geo = geocode(city, state, get=get)
    ctx = census_context(geo["lat"], geo["lon"], get=get)
    ctx["_center"] = [geo["lat"], geo["lon"]]
    areas = tracts(geo["bbox"], int(max_areas or os.getenv("CITY_MAX_AREAS", "24")), get=get)
    if not areas:
        raise LookupError(f"no census tracts found for {geo['name']}")
    try:
        values = acs_home_values(ctx["state_fips"], ctx["county_fips"], get=get) if ctx.get("county_fips") else {}
    except Exception:
        values = {}
    try:
        crime = fbi_safety(geo["state"], ctx.get("county"), geo["name"], get=get) if geo.get("state") else None
    except Exception:
        crime = None
    schools, school_year = [], None
    if geo.get("state") == "NC":
        from . import reference
        schools, school_year = reference.load_schools(), "NC DPI 2025-26"
    if not schools:
        try:
            schools, school_year = school_scores(ctx.get("leaid"), get=get)
        except Exception:
            schools = []
    with ThreadPoolExecutor(workers) as ex:
        rows = list(ex.map(lambda t: _area(t, ctx, values, crime, schools, geo["name"], get), areas))
    hpi = metro_hpi(ctx.get("cbsa"), get=get)
    for r in rows:
        r["hpi_yoy"] = (hpi or {}).get("yoy_pct")
        r["hpi_series"] = (hpi or {}).get("series_id")
        r["state"] = geo.get("state")
    doc = {"key": slug(f"{geo['name']} {geo.get('state') or ''}"), "city": geo["name"], "state": geo.get("state"), "display": geo["display"],
           "center": [geo["lat"], geo["lon"]], "bbox": geo["bbox"], "context": ctx, "crime": crime, "hpi": hpi,
           "school_year": school_year, "areas": rows, "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "secs": round(time.time() - t0, 1)}
    os.makedirs(CITY_DIR, exist_ok=True)
    path = os.path.join(CITY_DIR, doc["key"] + ".json")
    with open(path + ".tmp", "w") as f:
        json.dump(doc, f, indent=1)
    os.replace(path + ".tmp", path)
    return doc


# ------------------------------------------------------------------ cache + background jobs
def cached(text, max_age_days=None):
    city, state = parse_location(text)
    max_age = 86400 * float(max_age_days if max_age_days is not None else os.getenv("CITY_CACHE_DAYS", "7"))
    for name in os.listdir(CITY_DIR) if os.path.isdir(CITY_DIR) else []:
        if name.endswith(".json") and name.startswith(slug(city)) and (not state or name[:-5].endswith("-" + state.lower())):
            path = os.path.join(CITY_DIR, name)
            if time.time() - os.path.getmtime(path) < max_age:
                with open(path) as f:
                    return json.load(f)
    return None


def all_cached():
    out = []
    for name in sorted(os.listdir(CITY_DIR)) if os.path.isdir(CITY_DIR) else []:
        if name.endswith(".json"):
            try:
                with open(os.path.join(CITY_DIR, name)) as f:
                    out.append(json.load(f))
            except (OSError, ValueError):
                continue
    return out


def status(text):
    doc = cached(text)
    if doc:
        return {"status": "ready", "city": doc["city"], "state": doc["state"], "areas": len(doc["areas"]), "built_at": doc["built_at"]}
    with _LOCK:
        job = _JOBS.get(slug(text))
    return job or {"status": "unknown"}


def ensure_async(text, on_ready=None, builder=None):
    """Start a background build unless the city is cached or already loading. Returns the current status."""
    key = slug(text)
    if cached(text):
        return status(text)
    with _LOCK:
        if _JOBS.get(key, {}).get("status") == "loading":
            return _JOBS[key]
        _JOBS[key] = {"status": "loading", "city": text, "started": time.time()}

    def run():
        try:
            doc = (builder or build_city)(text)
            if on_ready:
                on_ready(doc)
            with _LOCK:
                _JOBS[key] = {"status": "ready", "city": doc["city"], "state": doc["state"], "areas": len(doc["areas"])}
        except Exception as e:
            with _LOCK:
                _JOBS[key] = {"status": "failed", "city": text, "error": str(e)[:200]}
    threading.Thread(target=run, daemon=True).start()
    return _JOBS[key]


def ensure(text, timeout=None, on_ready=None, builder=None):
    """Blocking variant for the listing agent: cached doc, or build now (waits up to CITY_WAIT_SECS, default 120)."""
    doc = cached(text)
    if doc:
        return doc
    ensure_async(text, on_ready=on_ready, builder=builder)
    deadline = time.time() + float(timeout if timeout is not None else os.getenv("CITY_WAIT_SECS", "120"))
    while time.time() < deadline:
        st = status(text)
        if st.get("status") == "ready":
            return cached(text)
        if st.get("status") == "failed":
            raise LookupError(st.get("error") or "city lookup failed")
        time.sleep(0.2)
    raise TimeoutError(f"still loading public data for {text}")


def main(argv=None):
    text = " ".join(argv if argv is not None else sys.argv[1:]) or "Austin, TX"
    doc = build_city(text)
    print(json.dumps({k: doc[k] for k in ("city", "state", "context", "crime", "hpi", "school_year", "secs")}, indent=1))
    print(f"{len(doc['areas'])} areas, median price/sqft {statistics.median(a['median_ppsf'] for a in doc['areas'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
