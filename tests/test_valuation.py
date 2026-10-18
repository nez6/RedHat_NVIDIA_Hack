"""Free value calculator math."""
import pytest

from app import valuation

HOOD = {"median_ppsf": 300}


def test_baseline_home_is_sqft_times_median():
    est = valuation.estimate({"sqft": 2000, "beds": 3, "year_built": 2020, "property_type": "Single Family"}, HOOD, 2.0, year=2026)
    assert est["estimated_value"] == 600000 and est["total_adjustment_pct"] == 0
    assert est["low"] == 552000 and est["high"] == 648000
    assert est["forecast_12m"] == round(600000 * 1.012, -3)


def test_adjustments_are_capped_and_explained():
    old = valuation.estimate({"sqft": 1000, "beds": 9, "year_built": 1900, "property_type": "Condo",
                              "features": sorted(valuation.PREMIUM)}, HOOD, None, year=2026)
    pct = {a["name"]: a["pct"] for a in old["adjustments"]}
    assert pct == {"Age": -15.0, "Premium features": 8.0, "Property type": -10.0, "Bedrooms": 6.0}
    assert old["hpi_yoy_pct"] == 3.0  # default when no HPI


@pytest.mark.parametrize("price,verdict", [(500000, "below estimate"), (600000, "in line with estimate"), (700000, "above estimate")])
def test_asking_price_verdict(price, verdict):
    est = valuation.estimate({"sqft": 2000, "beds": 3, "year_built": 2020, "price": price}, HOOD, 0, year=2026)
    assert est["asking_verdict"] == verdict
