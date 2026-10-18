"""Live city lookup (pipeline/city.py): parse a new city's public data into neighborhood profiles the agents can score."""
import time

from app import metrics as M, tools as T
from pipeline import city, listings as PL

US = {"01-2023": 30.0, "02-2023": 30.0}


def fake_get(url, params):
    if url == city.NOMINATIM:
        return [{"name": "Austin", "display_name": "Austin, Travis County, Texas", "lat": "30.27", "lon": "-97.74",
                 "boundingbox": ["30.10", "30.50", "-97.95", "-97.55"], "address": {"ISO3166-2-lvl4": "US-TX"}}]
    if url == city.CENSUS_GEO:
        return {"result": {"geographies": {"States": [{"GEOID": "48"}], "Counties": [{"GEOID": "48453", "NAME": "Travis County"}],
                                           "Metropolitan Statistical Areas": [{"GEOID": "12420", "NAME": "Austin-Round Rock"}],
                                           "Unified School Districts": [{"GEOID": "4808130", "NAME": "Austin ISD"}]}}}
    if url == city.TRACTS:
        return {"features": [{"attributes": {"GEOID": f"48453000{i}00", "NAME": f"Census Tract {i}", "CENTLAT": str(30.2 + i / 100),
                                             "CENTLON": str(-97.7 - i / 100)}} for i in range(1, 6)]}
    if url == city.ACS:
        return [["B25077_001E", "state", "county", "tract"]] + [[str(360000 + i * 36000), "48", "453", f"000{i}00"] for i in range(1, 6)]
    if "byStateAbbr/TX" in url:
        return {"TRAVIS": [{"ori": "TX2270100", "agency_name": "Austin Police Department", "agency_type_name": "City", "counties": "TRAVIS"}]}
    if "/summarized/agency/TX2270100/" in url:
        local = {k: v * 1.5 for k, v in US.items()}  # 1.5x the US rate
        return {"offenses": {"rates": {"Austin Police Department Offenses": local, "United States Offenses": US}}}
    if "educationdata" in url:
        return {"results": [{"ncessch_num": 480813000001, "race": 99, "sex": 99, "lep": 99, "disability": 99, "econ_disadvantaged": 99,
                             "read_test_pct_prof_midpt": 60, "math_test_pct_prof_midpt": 70}]}
    if "EDGE_ADMINDATA" in url:
        return {"features": [{"attributes": {"NCESSCH": "480813000001", "SCH_NAME": "Oak Hill El", "SCHOOL_LEVEL": "Elementary"},
                              "geometry": {"x": -97.72, "y": 30.22}}]}
    if "Walkability" in url:
        return {"features": [{"attributes": {"NatWalkInd": 12, "GEOID20": "1"}}]}
    if url == "fema":
        return {"zone": "X", "subtype": None, "sfha": False}
    if url == "aqi_30d":
        return 40
    if url == "weather_12m":
        return {"heat_days": 20, "heavy_rain_days": 1, "high_wind_days": 0}
    if url == "fred":
        return [("2024-01-01", 100.0), ("2024-04-01", 101.0), ("2024-07-01", 102.0), ("2024-10-01", 102.5), ("2025-01-01", 103.0)]
    raise AssertionError(f"unexpected call {url}")


def test_locations_and_which_cities_need_a_lookup():
    assert city.locations("Cary; Austin, TX, Boise, ID / 27513") == [("Cary", None), ("Austin", "TX"), ("Boise", "ID"), ("27513", None)]
    assert city.needs_lookup("Cary, 27513, Apex, NC") == []
    assert city.needs_lookup("Five Points, Austin, TX", known={"Five Points"}) == ["Austin, TX"]
    assert city.needs_lookup("Raleigh, NC") == [] and city.needs_lookup("Portland, OR") == ["Portland, OR"]


def test_build_city_parses_public_data_into_neighborhood_profiles():
    doc = city.build_city("Austin, TX", get=fake_get, max_areas=4, workers=2)
    assert (doc["city"], doc["state"], doc["context"]["cbsa"], doc["context"]["leaid"]) == ("Austin", "TX", "12420", "4808130")
    assert len(doc["areas"]) == 3 and doc["crime"]["ratio_to_us"] == 1.5  # 5 tracts, spread evenly -> every 2nd
    assert doc["crime"]["safety_index"] == 70 - round(25 * 0.585) and "city-wide" in doc["crime"]["source"]
    a = doc["areas"][0]
    assert a["name"].startswith("Austin · Tract ") and a["generated"] and a["state"] == "TX" and a["center"] == [30.27, -97.74]
    assert a["walk_score"] == 60 and a["school_rating"] == 6 and a["median_ppsf"] in {round((360000 + i * 36000) / 1800) for i in range(1, 6)}
    assert a["environment"]["environment_index"] == 100 - round(0.7 * 10 + 12)
    assert a["hpi_yoy"] == 3.0 and a["hpi_series"] == "ATNHPIUS12420Q"
    assert city.cached("Austin, TX")["key"] == "austin-tx" and city.status("austin, tx")["status"] == "ready"


def test_city_areas_feed_listing_cleanup_and_metrics(monkeypatch):
    doc = city.build_city("Austin, TX", get=fake_get, max_areas=4, workers=2)
    T.register_city(doc)
    rec = {"address": "1 Congress Ave", "city": "Austin", "state": "TX", "zip": "78701", "lat": 30.215, "lon": -97.715, "price": 650000,
           "beds": 3, "baths": 2, "sqft": 1800, "status": "FOR_SALE", "source": "redfin_csv"}
    rows, rep = PL.clean([rec])
    assert rep["kept"] == 1 and rows[0]["neighborhood"].startswith("Austin · Tract")
    T.add_listings(rows)
    lid = rows[0]["id"]
    try:
        n = T.neighborhood_report(lid)
        assert n["commute_to"] == "downtown Austin" and n["commute_minutes"] < 30 and n["safety_source"].startswith("FBI")
        assert M.environment(lid, {})["score"] == T.HOODS[rows[0]["neighborhood"]]["environment"]["environment_index"] == 81
        assert M.forecast(lid, {})["hpi_series"] == "ATNHPIUS12420Q"
        assert M.urban(lid, {})["target"] == "downtown Austin"
    finally:  # keep the shared in-memory store as other tests expect it
        T.LISTINGS[:] = [r for r in T.LISTINGS if r["id"] != lid]
        T.BY_ID.pop(lid, None)
        for a in doc["areas"]:
            T.HOODS.pop(a["name"], None)


def test_ensure_async_builds_once_in_background(monkeypatch):
    calls = []

    def builder(text):
        calls.append(text)
        time.sleep(0.1)
        return city.build_city("Boise, ID", get=lambda u, p: _boise(u, p), max_areas=2, workers=1)

    monkeypatch.setattr(city, "CITY_DIR", city.CITY_DIR + "-async")
    first = city.ensure_async("Boise, ID", builder=builder)
    second = city.ensure_async("Boise, ID", builder=builder)
    assert first["status"] == second["status"] == "loading"
    doc = city.ensure("Boise, ID", timeout=5, builder=builder)
    assert doc["city"] == "Boise" and calls == ["Boise, ID"]


def _boise(url, params):
    if url == city.NOMINATIM:
        return [{"name": "Boise", "display_name": "Boise, Idaho", "lat": "43.6", "lon": "-116.2", "boundingbox": ["43.5", "43.7", "-116.3", "-116.1"],
                 "address": {"ISO3166-2-lvl4": "US-ID"}}]
    if url == city.CENSUS_GEO:
        return {"result": {"geographies": {}}}
    if url == city.TRACTS:
        return {"features": [{"attributes": {"GEOID": "1", "NAME": "Census Tract 1", "CENTLAT": "43.6", "CENTLON": "-116.2"}}]}
    if "byStateAbbr" in url:
        return {}
    if url in ("fema", "aqi_30d", "weather_12m", "fred"):
        raise RuntimeError("offline")
    return {"features": []}


def test_city_api_routes(monkeypatch):
    from fastapi.testclient import TestClient
    from app.server import app
    from conftest import login
    c = TestClient(app)
    assert c.get("/api/city/status", params={"q": "Cary, 27513"}).json()["status"] == "ready"
    assert c.post("/api/city/load", json={"location": "Denver, CO"}).status_code == 401
    login(c, "realtor")
    monkeypatch.setenv("CITY_LOOKUP", "on")
    monkeypatch.setattr(city, "build_city", lambda text: (_ for _ in ()).throw(LookupError("offline in tests")))
    r = c.post("/api/city/load", json={"location": "Denver, CO"}).json()
    assert r["cities"][0]["city"] == "Denver, CO" and r["cities"][0]["status"] in ("loading", "failed")
    for _ in range(50):
        if c.get("/api/city/status", params={"q": "Denver, CO"}).json()["status"] == "failed":
            break
        time.sleep(0.05)
    assert c.get("/api/city/status", params={"q": "Denver, CO"}).json()["status"] == "failed"
