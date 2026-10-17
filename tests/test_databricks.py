"""Unity Catalog sink: DDL, governance metadata, lineage-producing SQL, audit trail — against a fake warehouse."""
import json

import pytest

from pipeline import databricks_sink as D

q = D.q


class FakeWarehouse:
    def __init__(self, fail_on=None, pending_first=False):
        self.calls, self.fail_on, self.pending_first = [], fail_on, pending_first

    def __call__(self, method, path, body):
        if method == "GET":
            return {"statement_id": "s1", "status": {"state": "SUCCEEDED"}}
        self.calls.append(body)
        if self.fail_on and self.fail_on[0] in body["statement"]:
            return {"status": {"state": "FAILED", "error": {"message": self.fail_on[1]}}}
        if self.pending_first:
            self.pending_first = False
            return {"statement_id": "s1", "status": {"state": "PENDING"}}
        return {"statement_id": "s0", "status": {"state": "SUCCEEDED"}}


def stmts(wh):
    return [c["statement"] for c in wh.calls]


def test_sync_builds_medallion_with_governance(monkeypatch):
    monkeypatch.setenv("DATABRICKS_CATALOG", "hack")
    wh = FakeWarehouse()
    out = D.sync(client=D.Client(transport=wh), actor="tester")
    sql = "\n".join(stmts(wh))
    for schema in ("estatepulse_bronze", "estatepulse_silver", "estatepulse_gold"):
        assert f"CREATE SCHEMA IF NOT EXISTS {q('hack')}.{q(schema)}" in sql
    assert f"CREATE OR REPLACE TABLE {q('hack')}.{q('estatepulse_silver')}.{q('neighborhood_risk')}" in sql
    assert f"FROM {q('hack')}.{q('estatepulse_bronze')}.{q('neighborhood_snapshots')}" in sql  # silver built from bronze -> UC lineage
    assert f"CREATE OR REPLACE VIEW {q('hack')}.{q('estatepulse_gold')}.{q('listing_scorecard')}" in sql
    assert "SET TAGS" in sql and "'pii' = 'none'" in sql
    assert "CHECK (score BETWEEN 0 AND 100)" in sql
    assert out["counts"]["listings_raw"] == 61 and out["counts"]["listing_scores"] == 610
    payloads = [c for c in wh.calls if c.get("parameters")]
    assert all(p["parameters"][0]["name"] == "payload" for p in payloads)
    audit = next(c for c in wh.calls if "pipeline_audit" in c["statement"] and c["statement"].startswith("INSERT"))
    assert "'tester'" in audit["statement"] and json.loads(audit["parameters"][0]["value"])[-1]["source"] == "databricks_sync"


def test_existing_constraint_is_ignored_but_real_errors_raise():
    D.sync(client=D.Client(transport=FakeWarehouse(fail_on=("ADD CONSTRAINT", "CONSTRAINT_ALREADY_EXISTS: already exists"))))
    with pytest.raises(RuntimeError, match="PERMISSION_DENIED"):
        D.sync(client=D.Client(transport=FakeWarehouse(fail_on=("CREATE SCHEMA", "PERMISSION_DENIED: no USE CATALOG"))))


def test_pending_statements_are_polled():
    r = D.Client(transport=FakeWarehouse(pending_first=True)).sql("SELECT 1")
    assert r["status"]["state"] == "SUCCEEDED"


def test_decision_audit_is_best_effort(monkeypatch):
    for k in ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_WAREHOUSE_ID"):
        monkeypatch.delenv(k, raising=False)
    assert D.log_decision("nemotron", "rules", {}, []) is None  # not configured -> no-op
    wh = FakeWarehouse()
    rows = [{"listing": {"id": 1001}, "jev": {"verdict": "tour_now"}}]
    assert D.log_decision("nemotron", "rules", {"summary": "s", "weights": {"deal": 4}}, rows, client=D.Client(transport=wh))
    assert "agent_decisions" in wh.calls[0]["statement"]
    assert D.log_decision("m", "rules", {}, rows, client=D.Client(transport=FakeWarehouse(fail_on=("INSERT", "boom")))) is None


def test_dry_run_cli_prints_sql(capsys):
    assert D.main(["--dry-run"]) == 0
    assert "CREATE SCHEMA IF NOT EXISTS" in capsys.readouterr().out
