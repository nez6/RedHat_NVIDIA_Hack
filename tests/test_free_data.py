"""Free public-data replacements: NOAA ACIS, EPA walkability, Wake County parcels, NCES + NC DPI schools, Redfin CSV, ORS."""
import io
import json
import shutil
import zipfile

from pipeline import listings as PL, reference
from pipeline.sources import environment, free_data as F, redfin

REDFIN_CSV = (
    "SALE TYPE,SOLD DATE,PROPERTY TYPE,ADDRESS,CITY,STATE OR PROVINCE,ZIP OR POSTAL CODE,PRICE,BEDS,BATHS,LOCATION,SQUARE FEET,"
    "LOT SIZE,YEAR BUILT,DAYS ON MARKET,$/SQUARE FEET,HOA/MONTH,STATUS,URL (SEE https://www.redfin.com/buy-a-home/comparative-market-analysis "
    "FOR INFO ON PRICING),SOURCE,MLS#,FAVORITE,INTERESTED,LATITUDE,LONGITUDE\n"
    "MLS Listing,,Single Family Residential,412 Oak Grove Dr,Cary,NC,27513,515000,4,2.5,West Cary,2300,8712,2008,9,224,40,Active,"
    "https://www.redfin.com/NC/Cary/412-Oak-Grove-Dr-27513/home/1,TMLS,10012345,N,Y,35.7801,-78.8120\n"
    "PAST SALE,January-5-2026,Townhouse,9 Sold Way,Cary,NC,27513,300000,2,2,,1200,,1999,,,,Sold,,TMLS,10000001,N,Y,35.78,-78.81\n"
    "MLS Listing,,Condo/Co-op,77 Main St Unit 4,Raleigh,NC,27601,289000,2,2,Downtown,1100,,2015,30,262,310,Active,,TMLS,10055555,N,Y,35.7790,-78.6390\n"
)


def test_acis_counts_heat_and_heavy_rain_days():
    seen = {}

    def get(url, params):
        seen.update(json.loads(params["params"]))
        return {"data": [["2026-07-01", 97, "0.10"], ["2026-07-02", 94, "2.40"], ["2026-07-03", "M", "T"], ["2026-07-04", 99, "2.00"]]}

    w = F.acis_extreme_days(35.78, -78.64, get=get)
    assert (w["heat_days"], w["heavy_rain_days"], w["high_wind_days"]) == (2, 2, 0)
    assert seen["grid"] == "21" and seen["elems"] == "maxt,pcpn" and seen["loc"] == "-78.64,35.78"


def test_environment_defaults_to_free_noaa_weather(monkeypatch):
    monkeypatch.setattr(F, "acis_extreme_days", lambda lat, lon: {"heat_days": 3, "source": "acis"})
    assert environment.weather_12m(35.7, -78.6)["source"] == "acis"


def test_epa_walkability_maps_index_to_score():
    w = F.epa_walkability(35.8, -78.6, get=lambda u, p: {"features": [{"attributes": {"NatWalkInd": 14.83, "GEOID20": "371830523011"}}]})
    assert w == {"walk_index": 14.83, "walk_score": 74, "block_group": "371830523011"}
    assert F.epa_walkability(0, 0, get=lambda u, p: {"features": []}) is None


def test_wake_parcel_and_median_price_per_sqft():
    seen = {}

    def get(url, params):
        seen.update(params)
        return {"features": [{"attributes": {"SITE_ADDRESS": "412 OAK GROVE DR", "YEAR_BUILT": 2008, "HEATEDAREA": 2290,
                                             "TOTAL_VALUE_ASSD": 498000, "TOTSALPRICE": 455000, "SALE_DATE": 1609459200000}}]}

    p = F.wake_parcel("412 Oak Grove Drive", "27513", get=get)
    assert p["year_built"] == 2008 and p["assessed_value"] == 498000 and p["last_sale_date"] == "2021-01-01"
    assert "STNUM = 412" in seen["where"] and "OAK" in seen["where"] and "ZIPNUM = 27513" in seen["where"]
    assert F.wake_parcel("no number street", get=get) is None
    sales = {"features": [{"attributes": {"TOTSALPRICE": 300000 + i * 10000, "HEATEDAREA": 1500}} for i in range(12)]
             + [{"attributes": {"TOTSALPRICE": 60000, "HEATEDAREA": 5000}}]}  # 12/sqft outlier is dropped
    assert F.wake_median_ppsf(35.8, -78.6, get=lambda u, p: sales)["median_ppsf"] == round((350000 + 360000) / 2 / 1500)
    assert F.wake_median_ppsf(35.8, -78.6, get=lambda u, p: {"features": sales["features"][:5]}) is None


def _xlsx(rows):
    """Tiny workbook with a 'Combined Test Results' sheet (inline strings) for the NC DPI parser."""
    def cell(ref, v):
        return f'<c r="{ref}" t="inlineStr"><is><t>{v}</t></is></c>' if isinstance(v, str) else f'<c r="{ref}"><v>{v}</v></c>'
    body = "".join(f'<row r="{i + 1}">' + "".join(cell(f"{chr(65 + j)}{i + 1}", v) for j, v in enumerate(r)) + "</row>" for i, r in enumerate(rows))
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook {ns} {rns}><sheets><sheet name="Combined Test Results" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", f"<worksheet {ns}><sheetData>{body}</sheetData></worksheet>")
    return buf.getvalue()


def test_ncdpi_proficiency_reads_all_students_all_subjects():
    data = _xlsx([["Back to Introduction"], ["District Name", "School\nCode", "School Name", "Subgroup", "Subject", "Percent\nLevel 3 and Above\n(GLP)"],
                  ["Wake", "920312", "Oak ES", "All Students", "All EOG/EOC Subjects", "71.2"],
                  ["Wake", "920312", "Oak ES", "Female", "All EOG/EOC Subjects", "75"],
                  ["Wake", "920400", "Pine MS", "All Students", "All EOG/EOC Subjects", ">95"]])
    assert F.ncdpi_proficiency(data) == {"920312": {"glp": 71.2, "name": "Oak ES"}, "920400": {"glp": 95.0, "name": "Pine MS"}}


def test_nces_schools_and_nearest_school_rating():
    feats = {"features": [{"attributes": {"NCESSCH": "370472001", "ST_LEAID": "NC-920", "SCH_NAME": "Oak Elementary", "SCHOOL_LEVEL": "Elementary",
                                          "LCITY": "Cary"}, "geometry": {"x": -78.80, "y": 35.78}},
                          {"attributes": {"ST_LEAID": None, "SCH_NAME": "X"}, "geometry": {"x": 0, "y": 0}}]}
    rows = F.nces_nc_schools(get=lambda u, p: feats)
    assert rows == [{"nces_id": "370472001", "lea": "920", "name": "Oak Elementary", "level": "Elementary", "city": "Cary", "lat": 35.78, "lon": -78.8}]
    joined = F.join_schools(rows, {"920312": {"glp": 71.2, "name": "Oak ES"}, "320312": {"glp": 10.0, "name": "Oak Elementary"}})
    assert [(s["name"], s["glp"]) for s in joined] == [("Oak Elementary", 71.2)]  # same district, name matches without "ES"
    schools = [{"name": "Near ES", "level": "Elementary", "lat": 35.78, "lon": -78.80, "glp": 80},
               {"name": "Far ES", "level": "Elementary", "lat": 36.5, "lon": -79.5, "glp": 20},
               {"name": "MS", "level": "Middle", "lat": 35.79, "lon": -78.81, "glp": 70},
               {"name": "HS", "level": "High", "lat": 35.77, "lon": -78.79, "glp": 60}]
    r = F.nearby_school_rating(35.781, -78.801, schools)
    assert r["school_rating"] == 7 and [s["name"] for s in r["schools"]] == ["Near ES", "MS", "HS"] and r["glp_pct"] == 70.0


def test_redfin_csv_parses_active_listings_and_skips_sales():
    assert redfin.looks_like_redfin(REDFIN_CSV)
    recs = redfin.parse_csv(REDFIN_CSV, "cary.csv")
    assert [r["address"] for r in recs] == ["412 Oak Grove Dr", "77 Main St Unit 4"]
    r = recs[0]
    assert (r["price"], r["beds"], r["baths"], r["sqft"], r["year_built"], r["hoa_monthly"], r["home_type"]) == (515000, 4, 2.5, 2300, 2008, 40, "Single Family")
    assert (r["lat"], r["lon"], r["lot_acres"], r["status"]) == (35.7801, -78.812, 0.2, "FOR_SALE")
    rows, rep = PL.clean(recs)
    assert rep["kept"] == 2 and {x["source"] for x in rows} == {"redfin_csv"}


def test_enrich_adds_county_record_walkability_and_schools():
    rows, _ = PL.clean(redfin.parse_csv(REDFIN_CSV))

    def get(url, params):
        if "wakegov" in url:
            return {"features": [{"attributes": {"SITE_ADDRESS": "x", "YEAR_BUILT": 2007, "HEATEDAREA": 2300, "TOTAL_VALUE_ASSD": 480000}}]}
        return {"features": [{"attributes": {"NatWalkInd": 10, "GEOID20": "1"}}]}

    schools = [{"name": "ES", "level": "Elementary", "lat": 35.78, "lon": -78.8, "glp": 64}]
    counts = PL.enrich(rows, get=get, schools=schools)
    assert counts == {"wake_parcel": 2, "walkability": 2, "schools": 2}
    assert rows[0]["walk_score"] == 50 and rows[0]["school_rating"] == 6 and rows[0]["assessed_value"] == 480000


def test_ors_commute_uses_known_hubs_and_geocodes_addresses(monkeypatch):
    from app import tools as T
    calls = []

    def transport(url, body=None):
        calls.append(url)
        if "geocode" in url:
            return {"features": [{"geometry": {"coordinates": [-78.9, 35.9]}}]}
        return {"durations": [[1260.0]]}

    assert T.ors_drive_minutes((35.70, -78.60), "rtp", transport=transport) == 21
    assert T.ors_drive_minutes((35.71, -78.61), "100 Main St, Durham NC", transport=transport) == 21
    assert sum("geocode" in c for c in calls) == 1


def test_reference_updates_neighborhoods_with_real_sources(tmp_path):
    path = tmp_path / "neighborhoods.json"
    shutil.copy(PL.DATA_DIR + "/neighborhoods.json", path)
    schools = [{"name": n, "level": lvl, "lat": 35.81, "lon": -78.65, "glp": 55} for n, lvl in (("ES", "Elementary"), ("MS", "Middle"), ("HS", "High"))]

    def get(url, params):
        if "Walkability" in url:
            return {"features": [{"attributes": {"NatWalkInd": 16, "GEOID20": "1"}}]}
        return {"features": [{"attributes": {"TOTSALPRICE": 400000, "HEATEDAREA": 1600}} for _ in range(12)]}

    log = reference.update_neighborhoods(schools, get=get, hoods_path=str(path))
    hoods = {h["name"]: h for h in json.loads(path.read_text())}
    fp = hoods["Five Points"]
    assert (fp["school_rating"], fp["walk_score"], fp["median_ppsf"]) == (6, 80, 250) and fp["walk_source"].startswith("EPA")
    assert "price/sqft" not in log["Durham Downtown"] and hoods["Durham Downtown"]["ppsf_source"] == "sample"
