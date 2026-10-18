"""DATA_SOURCE=databricks: the agents read Databricks gold; validated against the local path."""
import importlib.util
import json
import os

from app import gold as G, live, tools as T
from pipeline import databricks_sink as D

HERE = os.path.dirname(__file__)


class GoldWarehouse:
    """Answers the two gold queries app/gold.py makes."""
    dry_run = False

    def __init__(self, hoods, macro):
        self.hoods, self.macro = hoods, macro

    def sql(self, statement, params=None):
        rows = self.hoods if "jev_ready_neighborhoods" in statement else self.macro
        cols = list(rows[0])
        return {"manifest": {"schema": {"columns": [{"name": c} for c in cols]}}, "result": {"data_array": [[r[c] for c in cols] for r in rows]}}


def gold_rows(change=None):
    name = next(iter(T.HOODS))
    prof = {**T.HOODS[name], **(change or {})}
    hoods = [{"neighborhood": name, "profile_json": json.dumps(prof), "as_of": "2026-10-18 10:00:00", "run_id": "run-x",
              "safety_json": json.dumps({"safety_index": 12, "jev_state": "gold says unsafe", "source": "gold"}), "environment_json": None}]
    macro = [{"name": "mortgage_30y", "series_id": "MORTGAGE30US", "obs_date": "2026-10-15", "value": "5.55", "yoy_pct": "-3.1"},
             {"name": "raleigh_hpi", "series_id": "ATNHPIUS39580Q", "obs_date": "2026-04-01", "value": "370.0", "yoy_pct": "2.4"}]
    return name, hoods, macro


def test_gold_snapshot_feeds_the_scorers_and_restores():
    name, hoods, macro = gold_rows({"school_rating": 2})
    before = T.HOODS[name]["school_rating"]
    snap = G.fetch(GoldWarehouse(hoods, macro))
    assert snap["fred.json"]["series"]["mortgage_30y"] == {"series_id": "MORTGAGE30US", "date": "2026-10-15", "value": 5.55, "yoy_pct": -3.1, "history": []}
    G.force(snap)
    try:
        assert live.mortgage_rate()[0] == 5.55 and "Databricks gold" in live.mortgage_rate()[1]["source"]
        assert live.safety(name)["safety_index"] == 12 and live.hpi_yoy("Raleigh") == (2.4, "ATNHPIUS39580Q")
        assert T.HOODS[name]["school_rating"] == 2 and live.status()["data_source"] == "databricks gold"
    finally:
        G.force(None)
    assert T.HOODS[name]["school_rating"] == before and live.status()["data_source"] == "local" and live.safety(name) != {"safety_index": 12}


def test_empty_gold_is_an_error_not_a_silent_blank():
    import pytest
    with pytest.raises(RuntimeError, match="gold tables are empty"):
        G.fetch(GoldWarehouse([{"neighborhood": "x", "profile_json": None, "as_of": None, "run_id": None, "safety_json": None, "environment_json": None}],
                              [{"name": "m", "series_id": "M", "obs_date": "2026-01-01", "value": "1", "yoy_pct": None}]))


def test_unconfigured_or_unwanted_gold_falls_back_to_local(monkeypatch):
    monkeypatch.setenv("DATA_SOURCE", "databricks")
    for k in ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_WAREHOUSE_ID"):
        monkeypatch.delenv(k, raising=False)
    G.refresh(wait=True)  # fails (not configured) -> error recorded, app keeps serving local data
    assert G.status()["data_source"] == "local" and "not configured" in G.status()["gold_error"]
    assert live.mortgage_rate()[1]["source"].startswith("FRED") or live.mortgage_rate()[1]["source"] == "default"


def test_scores_from_gold_equal_local_when_gold_mirrors_local():
    spec = importlib.util.spec_from_file_location("archval", os.path.join(HERE, "..", "scripts", "architecture_validate.py"))
    V = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(V)
    local = V.local_snapshot()
    ids = [r["id"] for r in T.LISTINGS[:6]]
    prof = {"max_price": 600000, "weights": {c: 1 for c in T.CRITERIA}}
    G.force(False)
    a = T.score_matrix(ids, prof, prof["weights"])
    G.force(local)
    try:
        b = T.score_matrix(ids, prof, prof["weights"])
    finally:
        G.force(None)
    assert [r["scores"] for r in a] == [r["scores"] for r in b]
    V.CHECKS.clear()
    V.parity(local, local)
    assert all(c["status"] == "pass" for c in V.CHECKS)


def test_sync_builds_the_gold_tables_the_app_reads(monkeypatch):
    from test_databricks import FakeWarehouse, stmts
    wh = FakeWarehouse()
    D.sync(client=D.Client(transport=wh))
    sql = "\n".join(stmts(wh))
    for t in ("jev_ready_neighborhoods", "jev_ready_macro", "jev_ready_listings", "architecture_validation"):
        assert t in sql
    payloads = [p["value"] for c in wh.calls for p in (c.get("parameters") or [])]
    assert any('"source": "profile"' in v for v in payloads)  # neighborhood reference profiles reach bronze
