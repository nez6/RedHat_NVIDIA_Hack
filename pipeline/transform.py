"""Turn raw incidents into a per-neighborhood safety profile (numbers for the matrix, text for Jev)."""
import math
from collections import Counter

from .config import CARY_AREA_SQMI, CARY_LOOKBACK_DAYS, DEFAULT_SEVERITY, LOOKBACK_DAYS, RADIUS_MILES, SEVERITY


def miles(a_lat, a_lon, b_lat, b_lon):
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 3958.8 * math.asin(math.sqrt(h))


def weight(crime_type):
    return SEVERITY.get(crime_type, DEFAULT_SEVERITY)


def density_to_index(density):
    """Severity-weighted incidents per sq mi (lookback window) -> 0-100 safety index (higher = safer)."""
    return int(max(5, min(98, round(100 - 9 * math.log1p(max(0.0, density))))))


def _profile(counter_types, counter_cats, weighted, area, source, scope):
    density = weighted / area
    return {
        "safety_index": density_to_index(density),
        "weighted_density": round(density, 2),
        "incidents": sum(counter_types.values()),
        "by_type": dict(counter_types),
        "top_categories": counter_cats.most_common(5),
        "source": source, "scope": scope,
    }


def raleigh_profiles(hoods, incidents, radius=RADIUS_MILES):
    area = math.pi * radius ** 2
    out = {}
    for h in hoods:
        if h["city"] != "Raleigh":
            continue
        types, cats, w = Counter(), Counter(), 0.0
        for inc in incidents:
            if inc.get("lat") is None:
                continue
            if miles(h["lat"], h["lon"], inc["lat"], inc["lon"]) <= radius:
                types[inc["crime_type"]] += 1
                cats[inc["category"]] += 1
                w += weight(inc["crime_type"])
        out[h["name"]] = _profile(types, cats, w, area, "raleigh_pd", f"{radius:g} mi radius")
    return out


def cary_profiles(hoods, offense_counts, window_days=CARY_LOOKBACK_DAYS):
    """Town-wide counts over window_days, scaled to the LOOKBACK_DAYS window so densities are comparable."""
    scale = LOOKBACK_DAYS / window_days
    from .sources.police import classify_offense
    types, cats, w = Counter(), Counter(), 0.0
    for name, n in offense_counts.items():
        t = classify_offense(name)
        types[t] += round(n * scale)
        cats[(name or "").upper()] += round(n * scale)
        w += weight(t) * n * scale
    prof = _profile(types, cats, w, CARY_AREA_SQMI, "cary_pd", f"town-wide, {window_days}d avg")
    return {h["name"]: dict(prof) for h in hoods if h["city"] == "Cary"}


def jev_state(name, prof, days):
    """Plain-text summary handed to Jev for a functional safety judgement."""
    if not prof:
        return f"{name}: no live police feed for this area; baseline index only."
    bt = prof["by_type"]
    top = ", ".join(f"{c.title()} {n}" for c, n in prof["top_categories"])
    return (f"{name} ({prof['scope']}), last {days} days: {prof['incidents']} reported incidents — "
            f"{bt.get('CRIMES AGAINST PERSONS', 0)} against persons, {bt.get('CRIMES AGAINST PROPERTY', 0)} property, "
            f"{bt.get('CRIMES AGAINST SOCIETY', 0)} society. Most common: {top}. "
            f"Severity-weighted density {prof['weighted_density']}/sq mi.")


FLOOD_PENALTY = {"A": 40, "AE": 40, "AH": 35, "AO": 35, "AR": 30, "A99": 30, "V": 45, "VE": 45}


def environment_index(env):
    """0-100 (higher = lower environmental risk): flood zone, 30-day AQI, extreme-weather days."""
    flood = env.get("flood") or {}
    zone = (flood.get("zone") or "").upper()
    pen = FLOOD_PENALTY.get(zone, 0)
    if not pen and "0.2 PCT" in (flood.get("subtype") or "").upper():
        pen = 15  # 500-year floodplain
    aqi = env.get("aqi_30d")
    pen += max(0.0, (aqi or 0) - 30) * 0.7
    w = env.get("weather_12m") or {}
    pen += min(20, 0.5 * (w.get("heat_days") or 0) + 2 * (w.get("heavy_rain_days") or 0) + 1 * (w.get("high_wind_days") or 0))
    return int(max(0, min(100, round(100 - pen))))


def environment_profiles(hoods, fetch):
    """fetch(h) -> raw env dict. Failures are recorded per neighborhood, never fatal."""
    out, errors = {}, {}
    for h in hoods:
        try:
            env = fetch(h)
            out[h["name"]] = {**env, "environment_index": environment_index(env), "source": "fema_nfhl+open_meteo"}
        except Exception as e:
            errors[h["name"]] = str(e)[:160]
    return out, errors
