"""Free home value calculator: a transparent comparable-$/sqft estimate (not an appraisal).

value = sqft x neighborhood median $/sqft x (1 + age + features + type + bedroom adjustments)
range = value +/- 8%;  12-month outlook = value x (1 + 0.6 x metro HPI YoY)
Every adjustment is returned so the UI can show the math.
"""
import datetime as dt

PREMIUM = {"updated kitchen", "pool", "solar panels", "finished basement", "garage", "screened porch", "smart home",
           "energy efficient", "EV charger"}
TYPE_ADJ = {"Single Family": 0.0, "Townhouse": -0.05, "Condo": -0.10}
RANGE = 0.08


def estimate(home, hood, hpi_yoy=None, year=None):
    year = year or dt.date.today().year
    age = max(0, year - int(home["year_built"]))
    premium = sorted(f for f in home.get("features", []) if f in PREMIUM)
    adj = [
        ("Age", -min(0.15, max(0, age - 15) * 0.004), f"{age} yrs old: -0.4% per year beyond 15, max -15%"),
        ("Premium features", min(0.08, 0.012 * len(premium)), f"{len(premium)} premium features x 1.2%, max +8%" + (f" ({', '.join(premium)})" if premium else "")),
        ("Property type", TYPE_ADJ.get(home.get("property_type", "Single Family"), 0.0), home.get("property_type", "Single Family")),
        ("Bedrooms", max(-0.06, min(0.06, 0.02 * (int(home["beds"]) - 3))), f"{home['beds']} beds vs 3-bed baseline: 2% each, max +/-6%"),
    ]
    base = int(home["sqft"]) * hood["median_ppsf"]
    factor = 1 + sum(a for _, a, _ in adj)
    value = round(base * factor, -3)
    yoy = 3.0 if hpi_yoy is None else hpi_yoy
    out = {
        "estimated_value": value, "low": round(value * (1 - RANGE), -3), "high": round(value * (1 + RANGE), -3),
        "base_value": round(base, -3), "median_ppsf": hood["median_ppsf"], "sqft": int(home["sqft"]),
        "adjustments": [{"name": n, "pct": round(a * 100, 1), "why": w} for n, a, w in adj],
        "total_adjustment_pct": round((factor - 1) * 100, 1),
        "hpi_yoy_pct": yoy, "forecast_12m": round(value * (1 + 0.6 * yoy / 100), -3),
        "method": "neighborhood median $/sqft (sample data) with transparent adjustments; an estimate, not an appraisal",
    }
    if home.get("price"):
        gap = (int(home["price"]) - value) / value * 100
        out["asking_vs_estimate_pct"] = round(gap, 1)
        out["asking_verdict"] = "below estimate" if gap < -3 else "above estimate" if gap > 3 else "in line with estimate"
    return out
