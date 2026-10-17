"""The 10 homebuyer-matrix metrics: inputs, deterministic 0-100 scorers, and the tool each metric agent calls.

Every metric returns {"score": 0-100, ...inputs}. Scores are pure functions of listing + buyer profile + live
snapshots, so rankings are reproducible and can be used as NeMo Gym ground truth.
"""
import datetime as dt

from . import live
from . import tools as T

YEAR = dt.date.today().year

METRICS = ["lifestyle", "school", "safety", "environment", "urban", "commute", "forecast", "deal", "rooms", "maintenance"]
LABELS = {"lifestyle": "Lifestyle", "school": "School", "safety": "Safety", "environment": "Environment / Weather",
          "urban": "Urban", "commute": "Commute", "forecast": "Value / Forecast", "deal": "Deal Calculator",
          "rooms": "Rooms / Layout", "maintenance": "Maintenance"}
DEFAULT_WEIGHTS = {"lifestyle": 3, "school": 3, "safety": 4, "environment": 2, "urban": 2, "commute": 3,
                   "forecast": 3, "deal": 4, "rooms": 3, "maintenance": 2}


def _clamp(x):
    return int(max(0, min(100, round(x))))


def _L(lid):
    return T.BY_ID[int(lid)]


# ---------------------------------------------------------------- per-metric inputs + score
def lifestyle(lid, p, jev=None):
    r = _L(lid)
    wants = [f.lower() for f in p.get("must_have_features") or []]
    hit = sum(f in r["features"] for f in wants) / len(wants) if wants else None
    richness = min(1.0, len(r["features"]) / 7)
    rule = 100 * (0.6 * hit + 0.4 * richness) if hit is not None else 100 * richness
    score = jev["fit"] if jev and jev.get("fit") is not None else rule
    return {"score": _clamp(score), "must_haves_matched": f"{round((hit or 0) * len(wants))}/{len(wants)}" if wants else "n/a",
            "features": r["features"], "engine": "jev" if jev and jev.get("engine") == "jev" else "rules"}


def school(lid, p, jev=None):
    h = T.HOODS[_L(lid)["neighborhood"]]
    rating = h["school_rating"]  # raw 1-10 rating, no normalization (spec); x10 only for display scale
    ok = rating >= (p.get("min_school_rating") or 0)
    return {"score": _clamp(rating * 10 if ok else rating * 5), "school_rating": rating, "meets_threshold": ok,
            "source": "neighborhood rating (sample data)"}


def safety(lid, p, jev=None):
    n = T.neighborhood_report(lid)
    s = n["safety_index"]
    if jev and jev.get("safety") is not None:
        s = 0.5 * s + 0.5 * jev["safety"]
    return {"score": _clamp(s), "live_index": n["safety_index"], "source": n["safety_source"],
            "jev_safety": (jev or {}).get("safety")}


def environment(lid, p, jev=None):
    e = live.environment(_L(lid)["neighborhood"])
    if not e:
        return {"score": 70, "source": "baseline (no environment snapshot)"}
    return {"score": e["environment_index"], "flood_zone": e["flood"]["zone"], "sfha": e["flood"]["sfha"],
            "aqi_30d": e["aqi_30d"], "weather_12m": e["weather_12m"], "source": e["source"]}


def urban(lid, p, jev=None):
    r = _L(lid)
    target, (lat, lon) = T._dest(p.get("urban_target") or "downtown raleigh")
    miles = T._miles((r["lat"], r["lon"]), (lat, lon))
    walk = T.HOODS[r["neighborhood"]]["walk_score"]
    return {"score": _clamp(0.7 * (100 - miles * 3.5) + 0.3 * walk), "target": target, "miles": round(miles, 1),
            "walk_score": walk}


def commute(lid, p, jev=None):
    n = T.neighborhood_report(lid, p.get("commute_destination") or "downtown raleigh")
    s = 100 - (n["commute_minutes"] - 10) * 2.5
    cap = p.get("max_commute_min")
    if cap and n["commute_minutes"] > cap:
        s *= 0.5
    return {"score": _clamp(s), "minutes": n["commute_minutes"], "to": n["commute_to"], "over_cap": bool(cap and n["commute_minutes"] > cap)}


def forecast(lid, p, jev=None):
    r = _L(lid)
    a = T.affordability(lid)
    yoy, series = live.hpi_yoy(r["city"])
    yoy = yoy if yoy is not None else 3.0
    # 12-month view: 60% momentum from the metro HPI + mean reversion toward the neighborhood median price/sqft
    appreciation = 0.6 * yoy + (-a["price_vs_area_pct"]) * 0.15
    risk = p.get("risk_tolerance", "medium")
    spread = {"low": 4, "medium": 6, "high": 8}.get(risk, 6)
    return {"score": _clamp(50 + appreciation * spread), "expected_12m_pct": round(appreciation, 2),
            "forecast_12m_value": round(r["price"] * (1 + appreciation / 100)), "hpi_yoy_pct": yoy,
            "hpi_series": series or "default", "price_vs_area_pct": a["price_vs_area_pct"]}


def deal(lid, p, jev=None):
    r = _L(lid)
    a = T.affordability(lid, p.get("annual_income"), p.get("down_payment_pct", 20))
    budget = p.get("max_price") or r["price"]
    afford = 100 - (r["price"] - 0.75 * budget) / (0.30 * budget) * 100
    if a.get("housing_ratio_pct"):
        afford = min(afford, 100 - max(0, a["housing_ratio_pct"] - 28) * 6)
    discount = 60 - a["price_vs_area_pct"] * 2
    return {"score": _clamp(0.6 * max(0, min(100, afford)) + 0.4 * max(0, min(100, discount))),
            "monthly_total": a["monthly_total"], "rate_pct": a["rate_pct"], "housing_ratio_pct": a.get("housing_ratio_pct"),
            "price_vs_area_pct": a["price_vs_area_pct"]}


def rooms(lid, p, jev=None):
    r = _L(lid)
    want_beds, min_sqft = p.get("min_beds") or 3, p.get("min_sqft") or 1800
    s = 60 + (r["beds"] - want_beds) * 15 + (r["sqft"] - min_sqft) / 40
    return {"score": _clamp(s), "beds": r["beds"], "baths": r["baths"], "sqft": r["sqft"], "sqft_per_bed": round(r["sqft"] / r["beds"])}


def maintenance(lid, p, jev=None):
    r = _L(lid)
    age = max(0, YEAR - r["year_built"])
    s = 100 - min(50, age * 0.9) - min(30, r["hoa_monthly"] / 10)
    cap = p.get("max_hoa")
    if cap is not None and r["hoa_monthly"] > cap:
        s -= 20
    return {"score": _clamp(s), "age_years": age, "hoa_monthly": r["hoa_monthly"], "over_hoa_cap": bool(cap is not None and r["hoa_monthly"] > cap)}


FUNCS = {"lifestyle": lifestyle, "school": school, "safety": safety, "environment": environment, "urban": urban,
         "commute": commute, "forecast": forecast, "deal": deal, "rooms": rooms, "maintenance": maintenance}


def scores(lid, profile, jev=None):
    return {m: FUNCS[m](lid, profile, jev)["score"] for m in METRICS}


def tool_name(metric):
    return f"get_{metric}_inputs"


def run_tool(metric, listing_ids, profile, jev=None):
    out = []
    for lid in listing_ids or []:
        try:
            out.append({"listing_id": int(lid), **FUNCS[metric](lid, profile, (jev or {}).get(str(lid)))})
        except (KeyError, ValueError):
            out.append({"listing_id": lid, "error": "unknown listing"})
    return out


def schema(metric, description):
    return {"type": "function", "function": {
        "name": tool_name(metric), "description": description,
        "parameters": {"type": "object", "properties": {
            "listing_ids": {"type": "array", "items": {"type": "integer"}, "description": "Shortlisted listing ids"}},
            "required": ["listing_ids"]}}}
