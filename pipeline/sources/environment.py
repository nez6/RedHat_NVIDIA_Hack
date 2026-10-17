"""Environment / weather risk per neighborhood centroid (all keyless public APIs).

- FEMA National Flood Hazard Layer: flood zone at the point (A/AE/V = 1% annual chance "SFHA").
- Open-Meteo Air Quality: mean US AQI over the last 30 days.
- Open-Meteo Archive (ERA5): last 12 months of extreme heat, heavy rain and high-wind days.
"""
import datetime as dt

from .. import http

FEMA_NFHL_URL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
AQI_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

HEAT_C, RAIN_MM, GUST_KMH = 35.0, 50.0, 75.0


def flood_zone(lat, lon):
    p = http.get(FEMA_NFHL_URL, {"geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
                                 "spatialRel": "esriSpatialRelIntersects", "outFields": "FLD_ZONE,ZONE_SUBTY,SFHA_TF",
                                 "returnGeometry": "false", "f": "json"})
    feats = p.get("features") or []
    if not feats:
        return {"zone": "UNMAPPED", "subtype": None, "sfha": False}
    a = feats[0]["attributes"]
    return {"zone": a.get("FLD_ZONE"), "subtype": a.get("ZONE_SUBTY"), "sfha": a.get("SFHA_TF") == "T"}


def mean_aqi(lat, lon):
    p = http.get(AQI_URL, {"latitude": lat, "longitude": lon, "hourly": "us_aqi", "past_days": 30, "forecast_days": 1})
    vals = [v for v in (p.get("hourly") or {}).get("us_aqi", []) if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def extreme_days(lat, lon, today=None):
    today = today or dt.date.today()
    end = today - dt.timedelta(days=7)  # archive lags ~5 days
    start = end - dt.timedelta(days=365)
    p = http.get(ARCHIVE_URL, {"latitude": lat, "longitude": lon, "start_date": start.isoformat(), "end_date": end.isoformat(),
                               "daily": "temperature_2m_max,precipitation_sum,wind_gusts_10m_max", "timezone": "America/New_York"})
    d = p.get("daily") or {}
    count = lambda key, thr: sum(1 for v in d.get(key, []) if v is not None and v >= thr)
    return {"heat_days": count("temperature_2m_max", HEAT_C), "heavy_rain_days": count("precipitation_sum", RAIN_MM),
            "high_wind_days": count("wind_gusts_10m_max", GUST_KMH), "window": f"{start}..{end}"}


def fetch_neighborhood(h):
    return {"flood": flood_zone(h["lat"], h["lon"]), "aqi_30d": mean_aqi(h["lat"], h["lon"]),
            "weather_12m": extreme_days(h["lat"], h["lon"])}
