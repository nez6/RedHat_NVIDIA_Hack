"""Live smoke tests against the real FRED + police endpoints. Run with: pytest -m live"""
import pytest

from pipeline.sources import fred, police

pytestmark = pytest.mark.live


def test_fred_mortgage_rate_live():
    obs = fred.fetch_series("MORTGAGE30US")
    assert obs and 1 < obs[-1][1] < 20


def test_raleigh_police_live():
    rows = police.fetch_raleigh(days=7, max_records=2000)
    assert len(rows) > 50 and all(r["lat"] for r in rows)


def test_cary_police_live():
    assert sum(police.fetch_cary(days=30).values()) > 0
