"""Single source of truth for live data sources and output paths."""
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(ROOT, "data")
LIVE_DIR = os.getenv("DREAMHOME_LIVE_DIR", os.path.join(DATA_DIR, "live"))

# ---- FRED (St. Louis Fed). Keyless CSV endpoint by default; uses the JSON API if FRED_API_KEY is set.
FRED_SERIES = {
    "mortgage_30y": "MORTGAGE30US",       # 30-yr fixed mortgage rate, weekly, %
    "mortgage_15y": "MORTGAGE15US",       # 15-yr fixed mortgage rate, weekly, %
    "raleigh_hpi": "ATNHPIUS39580Q",      # FHFA house price index, Raleigh-Cary MSA, quarterly
    "durham_hpi": "ATNHPIUS20500Q",       # FHFA house price index, Durham-Chapel Hill MSA, quarterly
    "raleigh_unemployment": "RALE537URN",  # Raleigh MSA unemployment rate, monthly, %
}
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
FRED_API_URL = ("https://api.stlouisfed.org/fred/series/observations"
                "?series_id={series}&api_key={key}&file_type=json&sort_order=desc&limit=12")

# ---- Live police data
LOOKBACK_DAYS = int(os.getenv("DREAMHOME_LOOKBACK_DAYS", "90"))
RALEIGH_INCIDENTS_URL = ("https://services.arcgis.com/v400IkDOw1ad7Yad/arcgis/rest/services/"
                         "Police_Incidents/FeatureServer/0/query")
# Cary publishes with a lag, so use a 12-month window and scale it to LOOKBACK_DAYS
CARY_LOOKBACK_DAYS = 365
CARY_INCIDENTS_URL = "https://data.townofcary.org/api/explore/v2.1/catalog/datasets/cpd-incidents/records"

# Severity weights used for the safety index (crimes against persons count most)
SEVERITY = {"CRIMES AGAINST PERSONS": 3.0, "CRIMES AGAINST PROPERTY": 1.0, "CRIMES AGAINST SOCIETY": 0.5}
DEFAULT_SEVERITY = 0.75
RADIUS_MILES = 1.0
CARY_AREA_SQMI = 59.6  # Town of Cary land area, used for town-level density

HTTP_TIMEOUT = float(os.getenv("DREAMHOME_HTTP_TIMEOUT", "30"))
USER_AGENT = "DreamHomeAI/1.0 (hackathon data pipeline)"
