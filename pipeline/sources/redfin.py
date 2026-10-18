"""Redfin "Download All" CSV parser.

On any Redfin search results page a person can click "Download All" to get a CSV of every listing in view. That is a
feature Redfin offers users; the app only parses the file the person downloaded (it never contacts redfin.com).
Column names are matched case-insensitively by prefix so small header changes don't break the import.
"""
import csv
import io

COLS = {  # our field -> Redfin header prefix
    "sale_type": "SALE TYPE", "home_type": "PROPERTY TYPE", "address": "ADDRESS", "city": "CITY", "state": "STATE",
    "zip": "ZIP", "price": "PRICE", "beds": "BEDS", "baths": "BATHS", "sqft": "SQUARE FEET", "lot_sqft": "LOT SIZE",
    "year_built": "YEAR BUILT", "days_on_market": "DAYS ON MARKET", "hoa_monthly": "HOA", "status": "STATUS", "url": "URL",
    "mls_number": "MLS", "lat": "LATITUDE", "lon": "LONGITUDE",
}
TYPES = {"single family residential": "Single Family", "townhouse": "Townhouse", "condo/co-op": "Condo",
         "multi-family (2-4 unit)": "Multi-Family", "mobile/manufactured home": "Manufactured", "vacant land": "Land"}


def _num(v):
    s = "".join(ch for ch in str(v or "") if ch.isdigit() or ch == ".")
    try:
        return float(s) if s else None
    except ValueError:
        return None


def looks_like_redfin(text):
    head = (text or "").lstrip("﻿")[:400].upper()
    return "SALE TYPE" in head and "ADDRESS" in head and "PRICE" in head


def parse_csv(text, source_file=None):
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    headers = reader.fieldnames or []

    def col(prefix):
        return next((h for h in headers if h and h.strip().upper().startswith(prefix)), None)
    cols = {k: col(p) for k, p in COLS.items()}
    out = []
    for r in reader:
        g = lambda k: (r.get(cols[k]) if cols[k] else None)  # noqa: E731
        status = (g("status") or "Active").strip().lower()
        sale_type = (g("sale_type") or "").lower()
        if not g("address") or "sold" in sale_type or "past sale" in sale_type or status == "sold":
            continue
        out.append({
            "zpid": None, "external_id": g("mls_number") or None, "mls_number": g("mls_number") or None,
            "address": g("address").strip(), "city": (g("city") or "").strip() or None, "state": (g("state") or "").strip() or None,
            "zip": (g("zip") or "").strip()[:5] or None, "lat": _num(g("lat")), "lon": _num(g("lon")) and -abs(_num(g("lon"))),
            "price": _num(g("price")), "beds": _num(g("beds")), "baths": _num(g("baths")), "sqft": _num(g("sqft")),
            "year_built": _num(g("year_built")), "lot_acres": round(_num(g("lot_sqft")) / 43560, 3) if _num(g("lot_sqft")) else None,
            "hoa_monthly": _num(g("hoa_monthly")), "home_type": TYPES.get((g("home_type") or "").strip().lower(), (g("home_type") or "").strip() or None),
            "status": "FOR_SALE" if status in ("active", "coming soon", "for sale") else status.upper(),
            "days_on_market": _num(g("days_on_market")), "description": None, "facts": {}, "schools": [],
            "url": (g("url") or "").strip() or None, "source": "redfin_csv", "source_file": source_file,
        })
    return out
