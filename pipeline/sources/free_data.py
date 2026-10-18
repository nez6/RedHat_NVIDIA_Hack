"""Free, keyless public data that replaces the paid / sample pieces (no scraping, all official APIs or downloads).

  NOAA ACIS (data.rcc-acis.org)       12-month heat and heavy-rain days at a point (PRISM grid, free, no key)
  EPA National Walkability Index      block-group walkability 1-20 at a point (geodata.epa.gov ArcGIS, free)
  Wake County tax parcels             year built, heated sq ft, assessed value, recent sale price (maps.wakegov.com)
  NCES EDGE school locations          every public school's coordinates and district (nces.ed.gov)
  NC DPI assessment data              % of students at grade level (GLP) per school (dpi.nc.gov download)
"""
import datetime as dt
import io
import json
import math
import re
import statistics
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET

from .. import http
from ..config import HTTP_TIMEOUT, USER_AGENT

ACIS_URL = "https://data.rcc-acis.org/GridData"
EPA_WALK_URL = "https://geodata.epa.gov/arcgis/rest/services/OA/WalkabilityIndex/MapServer/0/query"
WAKE_PARCELS_URL = "https://maps.wakegov.com/arcgis/rest/services/Property/Parcels/MapServer/0/query"
NCES_SCHOOLS_URL = "https://nces.ed.gov/opengis/rest/services/K12_School_Locations/EDGE_ADMINDATA_PUBLICSCH_2425/MapServer/1/query"
NCDPI_ASSESSMENT_URL = "https://www.dpi.nc.gov/2025-26-school-assessment-and-other-indicator-data-0/download?attachment"
HEAT_F, RAIN_IN = 95.0, 2.0  # same thresholds as before (35 C, ~50 mm)


# ------------------------------------------------------------------ NOAA ACIS weather
def acis_extreme_days(lat, lon, today=None, get=None):
    today = today or dt.date.today()
    end = today - dt.timedelta(days=3)
    start = end - dt.timedelta(days=365)
    params = {"loc": f"{lon},{lat}", "sdate": start.isoformat(), "edate": end.isoformat(), "grid": "21", "elems": "maxt,pcpn"}
    d = (get or http.get)(ACIS_URL, {"params": json.dumps(params)})
    rows = d.get("data") or []

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None  # ACIS uses "M" for missing and "T" for trace
    heat = sum(1 for r in rows if (num(r[1]) or -999) >= HEAT_F)
    rain = sum(1 for r in rows if (num(r[2]) or 0) >= RAIN_IN)
    return {"heat_days": heat, "heavy_rain_days": rain, "high_wind_days": 0, "window": f"{start}..{end}",
            "source": "NOAA ACIS PRISM grid (wind not available; counted as 0)"}


# ------------------------------------------------------------------ EPA walkability
def epa_walkability(lat, lon, get=None):
    """National Walkability Index (1-20) for the block group at the point -> also as a 0-100 'walk score'."""
    d = (get or http.get)(EPA_WALK_URL, {"geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
                                         "spatialRel": "esriSpatialRelIntersects", "outFields": "NatWalkInd,GEOID20",
                                         "returnGeometry": "false", "f": "json"})
    feats = d.get("features") or []
    if not feats:
        return None
    a = feats[0]["attributes"]
    idx = a.get("NatWalkInd")
    return None if idx is None else {"walk_index": round(idx, 2), "walk_score": int(round(idx * 5)), "block_group": a.get("GEOID20")}


# ------------------------------------------------------------------ Wake County parcels
def _street_key(address):
    m = re.match(r"\s*(\d+)\s+(.*)", address or "")
    if not m:
        return None, None
    words = re.sub(r"[^A-Za-z0-9 ]", " ", m.group(2)).upper().split()
    words = [w for w in words if w not in {"N", "S", "E", "W", "ST", "STREET", "RD", "ROAD", "DR", "DRIVE", "AVE", "AVENUE", "LN", "LANE",
                                           "CT", "COURT", "WAY", "PL", "PLACE", "CIR", "CIRCLE", "BLVD", "TRL", "PKWY", "UNIT", "APT"}]
    return m.group(1), (words[0] if words else None)


def wake_parcel(address, zip_code=None, get=None):
    """Best match in Wake County tax records: year built, heated sq ft, assessed value, last sale."""
    num, word = _street_key(address)
    if not num or not word:
        return None
    where = f"STNUM = {int(num)} AND UPPER(SITE_ADDRESS) LIKE '%{word.replace(chr(39), '')}%'"
    if zip_code and str(zip_code)[:5].isdigit():
        where += f" AND ZIPNUM = {int(str(zip_code)[:5])}"
    d = (get or http.get)(WAKE_PARCELS_URL, {"where": where, "returnGeometry": "false", "resultRecordCount": 3, "f": "json",
                                             "outFields": "SITE_ADDRESS,YEAR_BUILT,HEATEDAREA,TOTAL_VALUE_ASSD,TOTSALPRICE,SALE_DATE,ZIPNUM"})
    feats = [f["attributes"] for f in d.get("features") or []]
    if not feats:
        return None
    a = max(feats, key=lambda x: x.get("HEATEDAREA") or 0)
    sale = a.get("SALE_DATE")
    return {"site_address": a.get("SITE_ADDRESS"), "year_built": a.get("YEAR_BUILT") or None, "heated_sqft": a.get("HEATEDAREA") or None,
            "assessed_value": a.get("TOTAL_VALUE_ASSD"), "last_sale_price": a.get("TOTSALPRICE") or None,
            "last_sale_date": dt.datetime.fromtimestamp(sale / 1000, dt.timezone.utc).date().isoformat() if isinstance(sale, (int, float)) else None,
            "source": "Wake County tax parcels"}


def wake_median_ppsf(lat, lon, radius_m=1609, years=2, get=None, today=None):
    """Median sale price per heated sq ft of homes sold within radius_m in the last `years` (Wake County only)."""
    since = (today or dt.date.today()) - dt.timedelta(days=365 * years)
    d = (get or http.get)(WAKE_PARCELS_URL, {
        "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326, "distance": radius_m, "units": "esriSRUnit_Meter",
        "spatialRel": "esriSpatialRelIntersects", "where": f"SALE_DATE >= DATE '{since.isoformat()}' AND TOTSALPRICE > 50000 AND HEATEDAREA > 500",
        "outFields": "TOTSALPRICE,HEATEDAREA", "returnGeometry": "false", "resultRecordCount": 2000, "f": "json"})
    ppsf = [f["attributes"]["TOTSALPRICE"] / f["attributes"]["HEATEDAREA"] for f in d.get("features") or []
            if f["attributes"].get("HEATEDAREA")]
    ppsf = [p for p in ppsf if 40 <= p <= 1500]  # drop land-only / non-arm's-length outliers
    return {"median_ppsf": round(statistics.median(ppsf)), "sales": len(ppsf), "since": since.isoformat()} if len(ppsf) >= 10 else None


# ------------------------------------------------------------------ schools: NCES locations + NC DPI proficiency
def nces_nc_schools(get=None, page=2000):
    """All open NC public schools with coordinates and their district code (NCES ST_LEAID, e.g. NC-920 -> 920)."""
    out, offset = [], 0
    while True:
        d = (get or http.get)(NCES_SCHOOLS_URL, {"where": "LSTATE LIKE 'NC%' AND SY_STATUS_TEXT='Open'", "returnGeometry": "true", "outSR": 4326,
                                                 "outFields": "NCESSCH,ST_LEAID,SCH_NAME,SCHOOL_LEVEL,LCITY", "resultOffset": offset,
                                                 "resultRecordCount": page, "f": "json"})
        feats = d.get("features") or []
        for f in feats:
            a, g = f["attributes"], f.get("geometry") or {}
            lea = (a.get("ST_LEAID") or "").split("-")[-1]
            if lea and g.get("y"):
                out.append({"nces_id": a.get("NCESSCH"), "lea": lea, "name": a.get("SCH_NAME"), "level": a.get("SCHOOL_LEVEL"),
                            "city": a.get("LCITY"), "lat": round(g["y"], 5), "lon": round(g["x"], 5)})
        if len(feats) < page:
            return out
        offset += page


_NAME_STOP = {"elementary", "elem", "es", "middle", "ms", "high", "hs", "school", "schools", "the", "of", "magnet", "academy", "center",
              "for", "and", "k8", "k-8", "intermediate", "primary", "early", "college", "global"}


def school_key(lea, name):
    """District code + school name without punctuation or school-type words: ('920', 'apex friendship')."""
    words = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split()
    return (str(lea), " ".join(w for w in words if w not in _NAME_STOP))


def join_schools(locations, proficiency):
    """NCES locations x NC DPI proficiency, matched on district code + normalized school name."""
    by_key = {school_key(code[:3], v["name"]): v["glp"] for code, v in proficiency.items() if len(code) == 6}
    out = []
    for s in locations:
        glp = by_key.get(school_key(s["lea"], s["name"]))
        if glp is not None:
            out.append({**s, "glp": glp})
    return out


def _xlsx_rows(data, sheet_name):
    """Stream rows of one sheet from an .xlsx (stdlib only; the NC DPI workbook is ~100 MB)."""
    z = zipfile.ZipFile(io.BytesIO(data))
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rid = next(s.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
               for s in wb.find("m:sheets", ns) if s.get("name").strip() == sheet_name.strip())
    target = next(r.get("Target") for r in rels if r.get("Id") == rid)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for _, el in ET.iterparse(z.open("xl/sharedStrings.xml")):
            if el.tag.endswith("}si"):
                shared.append("".join(t.text or "" for t in el.iter() if t.tag.endswith("}t")))
                el.clear()
    path = "xl/" + target.lstrip("/").replace("xl/", "")
    for _, el in ET.iterparse(z.open(path)):
        if el.tag.endswith("}row"):
            row = {}
            for c in el:
                ref, t = c.get("r"), c.get("t")
                v = c.find("m:v", ns)
                text = (v.text if v is not None else None) if t != "inlineStr" else "".join(x.text or "" for x in c.iter() if x.tag.endswith("}t"))
                if t == "s" and text is not None:
                    text = shared[int(text)]
                col = re.match(r"[A-Z]+", ref).group(0)
                row[col] = text
            yield row
            el.clear()


def ncdpi_proficiency(data):
    """{dpi_school_code: {"glp": % of all students at grade level, all subjects, "name": school name}} from NC DPI."""
    header, out = None, {}
    for row in _xlsx_rows(data, "Combined Test Results"):
        vals = {k: (v or "").replace("\n", " ").strip() for k, v in row.items()}
        if header is None:
            if "School Code" in vals.values() and "Subgroup" in vals.values():
                header = {v: k for k, v in vals.items()}
            continue
        code, sub = vals.get(header["School Code"], ""), vals.get(header["Subgroup"], "")
        subject = vals.get(header.get("Subject", ""), "")
        glp_col = next((k for v, k in header.items() if "GLP" in v), None)
        if sub in ("All Students", "ALL") and subject.lower().startswith("all") and glp_col:
            try:
                out[code] = {"glp": float(str(vals.get(glp_col)).replace("%", "").replace(">", "").replace("<", "")),
                             "name": vals.get(header.get("School Name", ""), "")}
            except ValueError:
                continue
    return out


def download(url, timeout=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout or max(HTTP_TIMEOUT, 180)) as r:
        return r.read()


def _miles(a, b):
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    return 2 * 3958.8 * math.asin(math.sqrt(h))


def nearby_school_rating(lat, lon, schools):
    """1-10 rating from the nearest elementary, middle and high school's % at grade level (GLP / 10).
    Nearest school is a proxy for assignment; families should confirm the assigned school with the district."""
    picks = []
    for level in ("Elementary", "Middle", "High"):
        cands = [s for s in schools if s.get("glp") is not None and (s.get("level") or "").startswith(level)]
        if cands:
            s = min(cands, key=lambda s: _miles((lat, lon), (s["lat"], s["lon"])))
            picks.append({**s, "miles": round(_miles((lat, lon), (s["lat"], s["lon"])), 1)})
    if not picks:
        return None
    glp = sum(p["glp"] for p in picks) / len(picks)
    return {"school_rating": max(1, min(10, round(glp / 10))), "glp_pct": round(glp, 1),
            "schools": [{"name": p["name"], "level": p["level"], "glp": p["glp"], "miles": p["miles"]} for p in picks],
            "source": "NC DPI % at grade level, nearest elementary/middle/high (NCES locations)"}


def url_with(url, params):
    return url + "?" + urllib.parse.urlencode(params)
