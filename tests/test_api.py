"""End-to-end API: the whole crew must finish (via fallbacks) even when Nemotron is down."""
import json

from fastapi.testclient import TestClient

from app.server import app

client = TestClient(app)


def m_criteria():
    return client.get("/api/meta").json()["criteria"]


def test_meta_and_data_status():
    m = client.get("/api/meta").json()
    assert len(m["criteria"]) == 10 and "lifestyle" in m["criteria"] and m["listings"] == 61
    d = client.get("/api/data-status").json()
    assert d["mortgage_30y"] == 7.4 and d["live_safety_neighborhoods"] == 2 and d["jev_enabled"] is False


def test_rescore_with_jev_payload():
    ids = [1001, 1002, 1003]
    weights = {c: 0 for c in m_criteria()} | {"lifestyle": 5}
    rows = client.post("/api/rescore", json={"ids": ids, "profile": {"max_price": 500000}, "weights": weights,
                                             "jev": {"1001": {"fit": 100, "safety": 90}}}).json()
    assert rows[0]["listing"]["id"] == 1001 and rows[0]["scores"]["lifestyle"] == 100


def test_match_stream_completes_with_nemotron_down():
    r = client.post("/api/match", json={"dream": "safe home near RTP with a yard", "form": {"max_price": 500000}})
    events = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    types = [e["type"] for e in events]
    assert types[0] == "start" and types[-1] == "done"
    for t in ("profile", "shortlist", "jev", "matrix", "recommendation"):
        assert t in types
    assert any(e["type"] == "fallback" for e in events)
    matrix = next(e for e in events if e["type"] == "matrix")
    assert matrix["rows"] and all(row["listing"]["price"] <= 500000 * 1.05 for row in matrix["rows"])


def test_architecture_dashboard_is_served():
    r = client.get("/architecture")
    assert r.status_code == 200 and "EstatePulse AI" in r.text


def test_fallback_demo_recovers_from_injected_failures():
    d = client.get("/api/fallback-demo").json()
    assert [e["level"] for e in d["log"]] == ["warn", "ok", "ok"]
    assert d["decision"]["engine"] == "rules" and "injected Jev timeout" in d["decision"]["error"]


def test_narrate_reports_error_when_nemotron_down():
    d = client.post("/api/narrate", json={"id": 1001, "weights": {}}).json()
    assert "error" in d


def test_agent_registry_endpoint():
    d = client.get("/api/agents").json()
    assert d["problems"] == [] and len(d["agents"]) == 10
    assert {a["key"] for a in d["agents"] if a["gym_mode"] == "eval+rl"} == {"forecast", "deal"}


def test_match_stream_reports_all_ten_metric_agents():
    r = client.post("/api/match", json={"dream": "condo near downtown", "form": {"max_price": 400000}})
    events = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    assert len({e["key"] for e in events if e["type"] == "metric_agent"}) == 10


def test_gym_summary_endpoint_never_fails():
    d = client.get("/api/gym").json()
    assert "agents" in d


def test_pages_are_served():
    for path, marker in (("/value", "Free home value calculator"), ("/how", "How we score a home"), ("/static/nav.js", "Free value calculator")):
        r = client.get(path)
        assert r.status_code == 200 and marker in r.text, path


def test_explain_shows_every_factor_with_formula_and_inputs():
    d = client.get("/api/explain/1001?max_price=500000").json()
    assert [f["factor"] for f in d["factors"]] == m_criteria()
    assert all(f["formula"] and isinstance(f["inputs"], dict) for f in d["factors"])
    assert client.get("/api/explain/999999").status_code == 404


def test_value_calculator_scores_an_unlisted_home_without_leaking_it():
    from app import tools as T
    before = set(T.BY_ID)
    body = {"neighborhood": "Oakwood", "sqft": 2000, "beds": 3, "year_built": 2010, "price": 600000, "annual_income": 150000,
            "features": ["garage", "pool"]}
    d = client.post("/api/value", json=body).json()
    assert d["valuation"]["estimated_value"] > 0 and d["valuation"]["asking_verdict"]
    assert len(d["factors"]) == 10 and d["finance"]["rate_pct"] == 7.4
    assert set(T.BY_ID) == before
    assert client.post("/api/value", json={**body, "neighborhood": "Atlantis"}).status_code == 400
    assert client.post("/api/value", json={**body, "sqft": 5}).status_code == 400
