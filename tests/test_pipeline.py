"""Ingestion pipeline: parsers, transforms, contracts, and last-good-snapshot behaviour. No network."""
import json, os

import pytest

from conftest import fixture
from pipeline import run, transform, validate
from pipeline.sources import environment, fred, police

HOODS = json.load(open(os.path.join(os.path.dirname(__file__), "..", "data", "neighborhoods.json")))


def test_fred_csv_skips_missing_values():
    obs = fred.parse_csv(fixture("fred_mortgage.csv"))
    assert obs[-1] == ("2026-10-15", 7.40) and len(obs) == 3


def test_fred_summary_yoy():
    s = fred.summarize(fred.parse_csv(fixture("fred_mortgage.csv")), yoy_periods=2)
    assert s["value"] == 7.4 and s["yoy_pct"] == pytest.approx((7.40 / 6.27 - 1) * 100, abs=0.01)


def test_raleigh_normalize_drops_bad_coordinates():
    rows = [police.normalize_raleigh(f) for f in fixture("raleigh_incidents.json")["features"]]
    good = [r for r in rows if r]
    assert [r["case"] for r in good] == ["P1", "P2", "P3"]
    assert good[0]["date"] == "2026-10-16"
    assert police._ctype("NULL") == "UNCLASSIFIED" and police._ctype("crimes against persons") == "CRIMES AGAINST PERSONS"


def test_raleigh_profiles_count_incidents_in_radius():
    inc = [r for r in (police.normalize_raleigh(f) for f in fixture("raleigh_incidents.json")["features"]) if r]
    prof = transform.raleigh_profiles(HOODS, inc)
    assert prof["Oakwood"]["incidents"] == 2
    assert prof["Oakwood"]["by_type"]["CRIMES AGAINST PERSONS"] == 1
    assert prof["Brier Creek"]["incidents"] == 1
    assert "Downtown Cary" not in prof  # Raleigh feed only scores Raleigh neighborhoods


def test_cary_offense_classification_and_profiles():
    counts = {r["crime_type"]: r["n"] for r in fixture("cary_counts.json")["results"]}
    prof = transform.cary_profiles(HOODS, counts, window_days=90)  # same window -> no scaling
    p = prof["Downtown Cary"]
    assert p["incidents"] == 170 and p["by_type"]["CRIMES AGAINST PERSONS"] == 30
    yearly = transform.cary_profiles(HOODS, counts, window_days=360)["Downtown Cary"]
    assert yearly["weighted_density"] == pytest.approx(p["weighted_density"] / 4, rel=0.01)
    assert set(prof) == {"Downtown Cary", "West Cary"}


def test_density_to_index_is_monotonic_and_bounded():
    vals = [transform.density_to_index(d) for d in (0, 1, 10, 100, 1000, 1e6)]
    assert vals == sorted(vals, reverse=True) and all(5 <= v <= 98 for v in vals)


def test_contract_rejects_bad_mortgage_rate():
    with pytest.raises(validate.ContractError):
        validate.check_fred({"mortgage_30y": {"date": "2026-10-15", "value": 75.0}})
    with pytest.raises(validate.ContractError):
        validate.check_fred({})


def test_contract_rejects_dead_police_feed():
    dead = {"Oakwood": {"safety_index": 90, "incidents": 0, "source": "raleigh_pd"}}
    with pytest.raises(validate.ContractError):
        validate.check_safety(dead, ["Oakwood"])


def test_run_writes_snapshots_and_keeps_last_good_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "LIVE_DIR", str(tmp_path))
    good = {"mortgage_30y": {"series_id": "MORTGAGE30US", "date": "2026-10-15", "value": 7.4}}
    monkeypatch.setattr(fred, "fetch_all", lambda: (good, {}))
    inc = [r for r in (police.normalize_raleigh(f) for f in fixture("raleigh_incidents.json")["features"]) if r]
    monkeypatch.setattr(police, "fetch_raleigh", lambda: inc)
    monkeypatch.setattr(police, "fetch_cary", lambda: {"LARCENY": 10})
    monkeypatch.setattr(environment, "fetch_neighborhood", lambda h: {"flood": {"zone": "X", "subtype": None, "sfha": False},
                                                                      "aqi_30d": 35.0, "weather_12m": {"heat_days": 2}})
    assert run.main([]) == 0
    assert json.load(open(tmp_path / "environment.json"))["neighborhoods"]["Oakwood"]["environment_index"] == 96
    snap = json.load(open(tmp_path / "fred.json"))
    assert snap["series"]["mortgage_30y"]["value"] == 7.4
    safety = json.load(open(tmp_path / "safety.json"))["neighborhoods"]
    assert "jev_state" in safety["Oakwood"]

    # Next run: FRED returns garbage -> contract fails -> previous fred.json is kept, --strict exits 1
    monkeypatch.setattr(fred, "fetch_all", lambda: ({"mortgage_30y": {"date": "2026-10-15", "value": 99}}, {}))
    assert run.main(["--strict", "--only", "fred"]) == 1
    assert json.load(open(tmp_path / "fred.json"))["series"]["mortgage_30y"]["value"] == 7.4
    man = json.load(open(tmp_path / "manifest.json"))
    assert man["sources"]["fred"]["status"] == "failed" and man["sources"]["police"]["status"] == "ok"


def test_environment_index_penalizes_floodplain_air_and_weather():
    clean = {"flood": {"zone": "X", "subtype": None}, "aqi_30d": 20, "weather_12m": {}}
    assert transform.environment_index(clean) == 100
    assert transform.environment_index({**clean, "flood": {"zone": "AE"}}) == 60
    assert transform.environment_index({**clean, "flood": {"zone": "X", "subtype": "0.2 PCT ANNUAL CHANCE FLOOD HAZARD"}}) == 85
    assert transform.environment_index({**clean, "aqi_30d": 80}) == 65
    assert transform.environment_index({**clean, "weather_12m": {"heat_days": 100, "heavy_rain_days": 9}}) == 80


def test_environment_contract():
    with pytest.raises(validate.ContractError):
        validate.check_environment({}, ["Oakwood"])
    with pytest.raises(validate.ContractError):
        validate.check_environment({"Oakwood": {"environment_index": 50, "aqi_30d": 900}}, ["Oakwood"])
    assert validate.check_environment({"Oakwood": {"environment_index": 50, "aqi_30d": 40}}, ["Oakwood", "Garner"])
