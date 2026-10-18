"""Bronze -> silver -> gold logic used by the Databricks job (no Spark needed)."""
import json
import os
import sys

import pytest

from conftest import fixture
from pipeline import medallion
from pipeline.sources import police

INC = [r for r in (police.normalize_raleigh(f) for f in fixture("raleigh_incidents.json")["features"]) if r]
FRED = {"mortgage_30y": {"series_id": "MORTGAGE30US", "date": "2026-10-15", "value": 7.4,
                         "history": [["2025-10-16", 6.27], ["2026-10-15", 7.4]]}}
ENV = {"flood": {"zone": "X", "subtype": None, "sfha": False}, "aqi_30d": 35.0, "weather_12m": {"heat_days": 2}}


@pytest.fixture(autouse=True)
def restore_app_modules():
    """medallion re-imports app.* against a temp live dir; put the test-session modules back afterwards."""
    saved = {k: v for k, v in sys.modules.items() if k == "app" or k.startswith("app.")}
    env = os.environ.get("DREAMHOME_LIVE_DIR")
    yield
    for k in [k for k in sys.modules if k == "app" or k.startswith("app.")]:
        del sys.modules[k]
    sys.modules.update(saved)
    os.environ["DREAMHOME_LIVE_DIR"] = env


def boom(*_):
    raise TimeoutError("offline")


def build(**over):
    f = dict(fetch_fred=lambda: (FRED, {}), fetch_raleigh=lambda: INC, fetch_cary=lambda: {"LARCENY": 40},
             fetch_env=lambda h: ENV, snapshot_dir="tests/fixtures/live")
    f.update(over)
    return medallion.bronze(**f)


def test_bronze_keeps_raw_rows_and_flags_source_mode():
    b, log = build()
    assert len(b["police_incidents"]) == 3 and b["police_incidents"][0]["source_mode"] == "live_api"
    assert len(b["listings_raw"]) == 61 and len(b["neighborhoods_raw"]) == 18
    assert {r["name"] for r in b["fred_observations"]} == {"mortgage_30y"} and log["fred"] == "live_api"


def test_bronze_falls_back_to_snapshots_when_sources_are_down():
    b, log = build(fetch_fred=boom, fetch_raleigh=boom, fetch_env=boom)
    assert log["fred"] == "snapshot" and log["raleigh"] == "unavailable"
    assert any(r["name"] == "mortgage_30y" and r["value"] == 7.4 for r in b["fred_observations"])
    assert [r["neighborhood"] for r in b["environment_raw"]] == ["Oakwood"]  # only one in the fixture snapshot


def test_silver_has_every_factor_for_every_listing_with_formula_and_inputs():
    b, _ = build()
    hoods, factors, live_dir = medallion.silver(b)
    assert len(factors) == 61 * 10 and len(hoods) == 18
    assert all(0 <= r["score"] <= 100 and r["formula"] and json.loads(r["inputs_json"]) is not None for r in factors)
    oak = next(h for h in hoods if h["neighborhood"] == "Oakwood")
    assert oak["safety_source"] == "raleigh_pd" and oak["incidents_lookback"] == 2
    deal = json.loads(next(r for r in factors if r["factor"] == "deal")["inputs_json"])
    assert deal["rate_pct"] == 7.4  # silver math uses the bronze FRED rate


def test_gold_is_jev_ready_and_ranked():
    b, _ = build()
    hoods, factors, live_dir = medallion.silver(b)
    rows = medallion.gold(factors, hoods, live_dir)
    assert len(rows) == 61 and [r["rank"] for r in rows] == list(range(1, 62))
    top = rows[0]
    assert all(f"score_{k}" in top for k in ("lifestyle", "deal", "maintenance"))
    assert "7.4%" in top["jev_state"] and set(json.loads(top["jev_questions_json"])) == {"safety", "fit", "verdict"}
    assert rows[0]["composite_score"] >= rows[-1]["composite_score"]
