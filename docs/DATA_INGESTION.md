# Data ingestion: every source, where it lands, how to refresh it

All sources are free public APIs or files that the user supplies. **Nothing is scraped.** Each live feed passes a data contract (`pipeline/validate.py`) before it replaces the last good snapshot, so a broken feed never blanks the app.

```
public APIs / user files ──▶ pipeline/ (fetch → validate → transform) ──▶ data/live/*.json, data/*.json
                                                                                  │
                                         python -m pipeline.databricks_sink ──▶ Databricks bronze → silver → gold
                                                                                  │
                                         app/gold.py (DATA_SOURCE=databricks) ◀───┘  6 agents + Jev read gold
```

## 1. Scheduled feeds: `python -m pipeline.run`

These run daily at 06:17 ET in `.github/workflows/data-refresh.yml`, or by hand.

| # | Source | Endpoint | Key | Code | Lands in | Used by |
|---|---|---|---|---|---|---|
| 1 | **FRED** (St. Louis Fed): 30-yr and 15-yr mortgage rates, FHFA house-price index for the Raleigh and Durham metros, Raleigh unemployment | `fred.stlouisfed.org/graph/fredgraph.csv?id=…` (keyless; FRED API if `FRED_API_KEY` is set) | none | `pipeline/sources/fred.py` | `data/live/fred.json` → `bronze.fred_observations` (10 years) → `silver.macro_latest` → `gold.jev_ready_macro`, `gold.fred_series` | Budget & Investment agent, Jev (live rate) |
| 2 | **Raleigh PD**: NIBRS incidents, last 90 days | City of Raleigh ArcGIS FeatureServer | none | `pipeline/sources/police.py` | `data/live/safety.json` → `bronze.neighborhood_snapshots` (police) → `silver.neighborhood_risk` → `gold.jev_ready_neighborhoods` | Safety & Environment agent, Jev |
| 3 | **Cary PD** incidents | `data.townofcary.org` (Opendatasoft) | none | `pipeline/sources/police.py` | same as #2 | same as #2 |
| 4 | **FEMA NFHL**: flood zone at each neighborhood center | `hazards.fema.gov/.../NFHL/MapServer/28` | none | `pipeline/sources/environment.py` | `data/live/environment.json` → `bronze.neighborhood_snapshots` (environment) → gold | Safety & Environment agent |
| 5 | **Open-Meteo**: 30-day air quality | `air-quality-api.open-meteo.com` | none | `pipeline/sources/environment.py` | same as #4 | same as #4 |
| 6 | **NOAA ACIS**: 12-month heat (≥95°F) and heavy-rain (≥2 in) days | `data.rcc-acis.org/GridData` (PRISM grid) | none | `pipeline/sources/free_data.py` | same as #4 | same as #4 |

## 2. Reference data: `python -m pipeline.reference` (refresh about monthly)

| # | Source | Endpoint | Key | Code | Lands in | Used by |
|---|---|---|---|---|---|---|
| 7 | **NC DPI** 2025–26 school assessments (% at grade level) | `dpi.nc.gov` xlsx download (~100 MB) | none | `pipeline/sources/free_data.py` | `data/reference/nc_schools.json`, `data/neighborhoods.json` → `bronze.neighborhood_snapshots` (profile) → gold | Lifestyle & Amenities agent (school rating) |
| 8 | **NCES EDGE** public school locations | `nces.ed.gov/opengis/.../EDGE_ADMINDATA_PUBLICSCH_2425` | none | `pipeline/sources/free_data.py` | joined with #7 (2,600 NC schools matched) | same as #7 |
| 9 | **EPA National Walkability Index** | `geodata.epa.gov/.../WalkabilityIndex` | none | `pipeline/sources/free_data.py` | `data/neighborhoods.json` (`walk_score`) → gold | Lifestyle & Amenities agent (urban score) |
| 10 | **Wake County parcels**: sales price per sq ft, year built, assessed value | `maps.wakegov.com/.../Property/Parcels` | none | `pipeline/sources/free_data.py` | `data/neighborhoods.json` (`median_ppsf`), listing enrichment | Budget & Investment agent (deal score), Property Condition agent |

## 3. Listings: `python -m pipeline.listings`, or the app's **Add real listings** upload

| # | Source | How | Key | Code | Lands in |
|---|---|---|---|---|---|
| 11 | **Zillow pages** the user saved from their own browser (search or home-detail pages) | parse the saved HTML; the app never contacts zillow.com | none | `pipeline/sources/zillow.py` | `data/zillow/listings.json` → app listing store |
| 12 | **Redfin "Download All" CSV** | parse the file the user downloaded | none | `pipeline/sources/redfin.py` | same as #11 |
| 13 | **RentCast**: live for-sale listings by city or ZIP | `api.rentcast.io/v1/listings/sale`, cached 24 h | `RENTCAST_API_KEY` (free: 50 requests a month) | `pipeline/sources/rentcast.py` | same as #11 |
| 14 | **Synthetic sample listings** (61 homes, published with their generator) | `data/make_data.py` | none | `data/listings.csv` | app listing store → `bronze.listings_raw` → `gold.jev_ready_listings` |

Every listing goes through the same data-quality steps (`pipeline/listings.py`):
- values like `$1.2M` or `2,300 sqft` are normalized;
- sold, pending, off-market and incomplete records are dropped;
- duplicates are merged;
- each home is mapped to its nearest neighborhood profile.

## 4. On demand while a search runs

| # | Source | Endpoint | Key | Code | Used by |
|---|---|---|---|---|---|
| 15 | **OpenRouteService**: drive time to the buyer's work location | `api.openrouteservice.org` geocode + matrix | `ORS_API_KEY` (free) | `app/tools.py` `ors_drive_minutes` | Commute & Location agent; without a key it uses a distance-based estimate |
| 16 | Google Maps Distance Matrix (optional alternative to #15) | `maps.googleapis.com` | `GOOGLE_MAPS_API_KEY` (paid) | `app/tools.py` | Commute & Location agent |

## 5. Live lookup for any US city: `python -m pipeline.city "Austin, TX"`

This runs automatically when a questionnaire names a city outside the Triangle. Results are cached for 7 days in `data/cities/`.

| # | Source | Key | What it provides |
|---|---|---|---|
| 17 | OpenStreetMap **Nominatim** | none | city center and boundary |
| 18 | US **Census geocoder** | none | county, metro area (CBSA), school district |
| 19 | Census **TIGERweb** | none | census tracts, used as the city's "neighborhoods" |
| 20 | Census **ACS** 5-year | `CENSUS_API_KEY` (free) | tract median home value (estimated price per sq ft) |
| 21 | Urban Institute **Education Data API** + NCES | none | school proficiency by district |
| 22 | **FBI Crime Data Explorer** | `FBI_API_KEY` (free; DEMO_KEY otherwise) | the city police agency's crime rate compared with the US |
| 23 | FRED **ATNHPIUS{CBSA}Q** | none | metro house-price trend |

Code: `pipeline/city.py`. Each city also gets #4–6 and #9 for every tract.

## 6. User input

| # | Source | Code | Lands in |
|---|---|---|---|
| 24 | **Home Match Toolkit** questionnaire (`/start`): budget, rooms, area, 10 priority weights | `app/intake.py`, `app/extract.py` (free-text filters) | `data/intake/` → `estatepulse_secure.client_intake` (row filter: clients see only their own) |
| 25 | Pre-approval letter or proof of funds | `app/intake.py` | `estatepulse_secure.client_documents` volume (advisors only; contents never copied into tables) |

## 7. Databricks publish and checks

| Command | What it does |
|---|---|
| `python -m pipeline.databricks_sink` | loads bronze and builds silver and gold (`jev_ready_neighborhoods`, `jev_ready_macro`, `jev_ready_listings`, `fred_series`), with tags and CHECK constraints |
| `python scripts/databricks_setup.py` | secure schema, documents volume, access-control grants (Databricks Free Edition: grants go to group members) |
| `python scripts/architecture_validate.py` | 41 workspace data-quality checks, gold vs local parity, all 6 agents on gold, evidence completeness → `gold.architecture_validation` |
| app at runtime | writes `gold.agent_decisions`, `gold.match_results`, `gold.agent_test_runs` (test lab) and `gold.agent_eval_runs` (`scripts/agent_eval.py`) |
