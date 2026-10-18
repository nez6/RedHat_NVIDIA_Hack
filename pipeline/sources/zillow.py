"""Zillow listing parser.

Reads Zillow pages a person saved from their own browser (search-results pages and home-detail pages) and pulls out
every property record embedded in the page's JSON. Nothing here sends requests to Zillow: Zillow's Terms of Use
prohibit automated queries and scraping, so the app never crawls zillow.com. Instead:

  * a buyer (or our team) opens the Zillow search for an area, saves the page (Ctrl/Cmd+S, "Webpage, HTML only")
    and drops it in data/zillow/saved/ or uploads it at /api/zillow/import;
  * `search_url(location)` builds the Zillow search link the UI shows the buyer for that location;
  * live listings by location come from a licensed feed (pipeline/sources/rentcast.py today, MLS / Zillow Bridge
    later; see README "Data roadmap").

Zillow embeds its data as JSON in <script id="__NEXT_DATA__"> (search + detail pages), sometimes as a JSON string
inside that JSON (gdpClientCache / apiCache), and in older pages in HTML comments. The parser walks every JSON blob
it can find and keeps any object that looks like a property (has a zpid plus a price or address), so it keeps
working when Zillow reshuffles its page layout.
"""
import html as _html
import json
import os
import re
import urllib.parse

_SCRIPT_RE = re.compile(r"<script[^>]*?(?:id=\"(?P<id>[^\"]+)\")?[^>]*>(?P<body>.*?)</script>", re.S | re.I)
_COMMENT_JSON_RE = re.compile(r"<!--\s*(\{.*?\})\s*-->", re.S)
OFF_MARKET = {"SOLD", "RECENTLY_SOLD", "OFF_MARKET", "OTHER", "FOR_RENT", "PENDING", "UNDER_CONTRACT"}
HOME_TYPES = {"SINGLE_FAMILY": "Single Family", "TOWNHOUSE": "Townhouse", "CONDO": "Condo", "APARTMENT": "Condo",
              "MULTI_FAMILY": "Multi-Family", "MANUFACTURED": "Manufactured", "LOT": "Land"}


def search_url(location):
    """Zillow's own search link for a city / ZIP / neighborhood, opened by the buyer in their browser."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", (location or "").strip()).strip("-")
    return f"https://www.zillow.com/homes/{urllib.parse.quote(slug)}_rb/" if slug else "https://www.zillow.com/homes/"


def _json_blobs(page):
    for m in _SCRIPT_RE.finditer(page):
        body = m.group("body").strip()
        if not body or body[0] not in "{[":
            # hdpApolloPreloadedData etc. may be HTML-escaped
            body = _html.unescape(body)
            if not body or body[0] not in "{[":
                continue
        try:
            yield json.loads(body)
        except ValueError:
            continue
    for m in _COMMENT_JSON_RE.finditer(page):
        try:
            yield json.loads(m.group(1))
        except ValueError:
            continue


def _walk(obj, depth=0):
    """Yield every dict in a JSON tree, descending into JSON-encoded strings (Zillow nests caches as strings)."""
    if depth > 40:
        return
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v, depth + 1)
    elif isinstance(obj, str) and len(obj) > 50 and obj[:1] in "{[" and '"zpid"' in obj:
        try:
            yield from _walk(json.loads(obj), depth + 1)
        except ValueError:
            return


def _num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return v
    s = re.sub(r"[^0-9.]", "", str(v))
    try:
        return float(s) if s else None
    except ValueError:
        return None


def _looks_like_property(d):
    if "zpid" not in d:
        return False
    keys = set(d)
    return bool(keys & {"price", "unformattedPrice", "listPrice"}) or bool(keys & {"address", "streetAddress", "addressStreet"})


def _merge(a, b):
    """Merge two partial records for the same zpid; fields already set win unless empty."""
    out = dict(a)
    for k, v in b.items():
        if out.get(k) in (None, "", [], {}):
            out[k] = v
    return out


def normalize(d, source_file=None):
    """One Zillow property object (search card, hdpData.homeInfo or detail `property`) -> flat record."""
    home = (d.get("hdpData") or {}).get("homeInfo") or {}
    d = _merge(d, home)
    addr = d.get("address") if isinstance(d.get("address"), dict) else {}
    lat = d.get("latitude") or (d.get("latLong") or {}).get("latitude")
    lon = d.get("longitude") or (d.get("latLong") or {}).get("longitude")
    street = addr.get("streetAddress") or d.get("streetAddress") or d.get("addressStreet")
    if not street and isinstance(d.get("address"), str):
        street = d["address"].split(",")[0]
    lot = _num(d.get("lotAreaValue") or d.get("lotSize"))
    lot_unit = (d.get("lotAreaUnit") or ("sqft" if d.get("lotSize") else "")).lower()
    lot_acres = (lot if lot_unit.startswith("acre") else lot / 43560) if lot else None
    facts = d.get("resoFacts") or {}
    schools = [{"name": s.get("name"), "rating": _num(s.get("rating")), "level": s.get("level"),
                "distance_mi": _num(s.get("distance"))} for s in (d.get("schools") or []) if isinstance(s, dict)]
    status = (d.get("homeStatus") or d.get("statusType") or "").upper()
    return {
        "zpid": str(d.get("zpid")),
        "address": street,
        "city": addr.get("city") or d.get("city") or d.get("addressCity"),
        "state": addr.get("state") or d.get("state") or d.get("addressState"),
        "zip": addr.get("zipcode") or d.get("zipcode") or d.get("addressZipcode"),
        "lat": _num(lat), "lon": _num(lon),
        "price": _num(d.get("unformattedPrice") or d.get("price") or d.get("listPrice")),
        "beds": _num(d.get("bedrooms") or d.get("beds")),
        "baths": _num(d.get("bathrooms") or d.get("baths")),
        "sqft": _num(d.get("livingArea") or d.get("area") or d.get("livingAreaValue")),
        "year_built": _num(d.get("yearBuilt") or facts.get("yearBuilt")),
        "lot_acres": round(lot_acres, 3) if lot_acres else None,
        "hoa_monthly": _num(d.get("monthlyHoaFee") or facts.get("associationFee")),
        "home_type": HOME_TYPES.get((d.get("homeType") or "").upper(), (d.get("homeType") or "").title() or None),
        "status": status or None,
        "days_on_market": _num(d.get("daysOnZillow") or facts.get("daysOnMarket")),
        "description": d.get("description"),
        "facts": {k: facts.get(k) for k in ("hasGarage", "hasPrivatePool", "hasView", "fireplaces", "hasCooling",
                                              "parkingFeatures", "atAGlanceFacts", "interiorFeatures", "exteriorFeatures")
                  if facts.get(k) not in (None, "", [])},
        "schools": schools,
        "url": urllib.parse.urljoin("https://www.zillow.com", d.get("detailUrl") or d.get("hdpUrl") or "") or None,
        "source": "zillow_saved_page",
        "source_file": os.path.basename(source_file) if source_file else None,
    }


def parse_html(page, source_file=None):
    """All properties found in one saved Zillow page, merged per zpid."""
    found = {}
    for blob in _json_blobs(page):
        for d in _walk(blob):
            if _looks_like_property(d):
                z = str(d["zpid"])
                found[z] = _merge(found.get(z, {}), d)
    return [normalize(d, source_file) for d in found.values()]


def parse_dir(path):
    """Parse every saved page (*.html / *.htm) in a folder. Returns (records, per-file log)."""
    records, log = [], {}
    if not os.path.isdir(path):
        return records, log
    for name in sorted(os.listdir(path)):
        if not name.lower().endswith((".html", ".htm")):
            continue
        with open(os.path.join(path, name), encoding="utf-8", errors="replace") as f:
            page = f.read()
        recs = parse_html(page, name)
        if not recs and ("px-captcha" in page or "Press &amp; Hold" in page or "Press & Hold" in page):
            log[name] = "captcha page saved instead of listings; open the search in a normal browser tab and save again"
        else:
            log[name] = f"{len(recs)} properties"
        records.extend(recs)
    return records, log
