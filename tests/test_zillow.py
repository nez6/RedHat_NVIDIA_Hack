"""Zillow parser, live-feed adapter and the data-quality step (dedupe, off-market, incomplete, out of area)."""
import json
import os

from conftest import FIX
from pipeline import listings as PL
from pipeline.sources import rentcast, zillow

ZDIR = os.path.join(FIX, "zillow")


def page(name):
    with open(os.path.join(ZDIR, name)) as f:
        return f.read()


def test_search_page_yields_every_card_with_flattened_fields():
    recs = {r["zpid"]: r for r in zillow.parse_html(page("search_cary.html"), "search_cary.html")}
    assert len(recs) == 5
    r = recs["2061111112"]
    assert (r["address"], r["city"], r["zip"], r["price"], r["beds"], r["baths"], r["sqft"]) == \
           ("22 Kildaire Farm Rd", "Cary", "27511", 415000, 3, 2.5, 1820)
    assert r["home_type"] == "Townhouse" and r["lot_acres"] == 0.25 and r["status"] == "FOR_SALE"
    assert r["url"].startswith("https://www.zillow.com/homedetails/")


def test_detail_page_reads_nested_json_string_cache():
    [r] = zillow.parse_html(page("detail_101_maple.html"))
    assert r["year_built"] == 2012 and r["hoa_monthly"] == 45 and len(r["schools"]) == 3
    assert "fenced backyard" in r["description"] and r["lot_acres"] == 0.25


def test_clean_drops_sold_incomplete_out_of_area_and_merges_duplicates():
    raw, log = PL.load_saved(ZDIR)
    assert log["captcha.html"].startswith("captcha page")
    rows, rep = PL.clean(raw)
    assert rep["dropped_not_for_sale"] == 1 and rep["dropped_incomplete"] == 1 and rep["dropped_out_of_area"] == 1
    assert rep["dropped_duplicate"] == 1 and rep["kept"] == 2
    maple = next(r for r in rows if r["id"] == 2061111111)
    # richer detail-page record won the dedupe: real year built, HOA, GreatSchools average, parsed features
    assert maple["year_built"] == 2012 and maple["hoa_monthly"] == 45 and maple["school_rating"] == 8
    assert {"fenced yard", "home office", "garage", "updated kitchen", "first-floor primary", "near greenway"} <= set(maple["features"])
    assert maple["neighborhood"] in ("West Cary", "Downtown Cary") and maple["source"] == "zillow_saved_page"


def test_build_writes_listings_json(tmp_path):
    out = tmp_path / "listings.json"
    rows, rep = PL.build(saved_dir=ZDIR, out_path=str(out))
    doc = json.loads(out.read_text())
    assert doc["quality"]["kept"] == len(doc["listings"]) == len(rows) == 2


def test_search_url_is_a_zillow_link_for_the_area():
    assert zillow.search_url("Cary, NC") == "https://www.zillow.com/homes/Cary-NC_rb/"
    assert zillow.search_url("27513") == "https://www.zillow.com/homes/27513_rb/"


def test_rentcast_params_normalize_and_cache(tmp_path):
    assert rentcast.location_params("27513") == {"zipCode": "27513"}
    assert rentcast.location_params("cary, nc") == {"city": "Cary", "state": "NC"}
    calls = []

    def fake(params):
        calls.append(params)
        return [{"id": "1-Elm-St,-Apex,-NC-27502", "addressLine1": "1 Elm St", "city": "Apex", "state": "NC", "zipCode": "27502",
                 "latitude": 35.73, "longitude": -78.85, "price": 450000, "bedrooms": 4, "bathrooms": 2.5, "squareFootage": 2200,
                 "yearBuilt": 2015, "lotSize": 8712, "hoa": {"fee": 60}, "propertyType": "Single Family", "status": "Active",
                 "daysOnMarket": 12}]

    recs, meta = rentcast.fetch("Apex, NC", transport=fake, cache_dir=str(tmp_path))
    again, meta2 = rentcast.fetch("Apex, NC", transport=fake, cache_dir=str(tmp_path))
    assert len(calls) == 1 and meta2["cached"] and not meta["cached"]
    rows, rep = PL.clean(recs)
    assert rep["kept"] == 1 and rows[0]["hoa_monthly"] == 60 and rows[0]["lot_acres"] == 0.2 and str(rows[0]["id"]).startswith("9")


def test_find_listings_tool_merges_live_feed_and_widens(monkeypatch):
    from app import tools as T
    before = set(T.BY_ID)

    def live(location):
        return [{"zpid": None, "external_id": "x1", "address": "8 Test Way", "city": "Apex", "state": "NC", "zip": "27502",
                 "lat": 35.733, "lon": -78.850, "price": 430000, "beds": 4, "baths": 3, "sqft": 2100, "status": "FOR_SALE",
                 "source": "rentcast_api"}], {"cached": False}

    try:
        out = T.find_listings(location="Apex", max_price=500000, min_beds=3, live_fetch=live)
        assert out["live_added"] == 1 and out["listings"][0]["address"] == "8 Test Way"
        assert out["zillow_search_url"].endswith("/Apex_rb/")
    finally:
        for lid in set(T.BY_ID) - before:
            T.LISTINGS.remove(T.BY_ID.pop(lid))
