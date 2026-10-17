"""Jev decision layer: response parsing, live call path (mocked), and fallback behaviour."""
from app import jev as J
from app import tools as T

L = T.LISTINGS[0]
P = {"summary": "family home", "must_have_features": [L["features"][0]], "min_beds": 1}
M = {"monthly": 2500, "rate": 7.4, "ratio": 22.0, "vs_area": -3.0, "commute_min": 20, "safety_index": 80}


def test_rules_mode_without_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    d = J.decide(L, P, M, "text", 80)
    assert d["engine"] == "rules" and d["safety"] == 80 and d["fit"] == 100 and d["verdict"] == "tour_now"


def test_parse_score_and_distribution_formats():
    r = J.parse({"answers": {"safety": {"score": 3.0, "confidence": 0.9},
                             "fit": {"probabilities": [0, 0, 0, 0, 1]},
                             "verdict": {"choice": "consider", "probabilities": {"tour_now": .2, "consider": .7, "pass": .1}}}})
    assert r["safety"] == 75 and r["fit"] == 100 and r["verdict"] == "consider"


def test_live_jev_path(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    seen = {}

    def fake_post(state, questions):
        seen["state"], seen["q"] = state, questions
        return {"answers": {"safety": {"score": 2}, "fit": {"score": 4}, "verdict": {"choice": "tour_now"}}}

    monkeypatch.setattr(J, "_post", fake_post)
    d = J.decide(L, P, M, "Oakwood: 900 incidents", 80)
    assert d["engine"] == "jev" and d["safety"] == 50 and d["fit"] == 100
    assert "7.4%" in seen["state"] and "Oakwood: 900 incidents" in seen["state"]
    assert set(seen["q"]) == {"safety", "fit", "verdict"} and seen["q"]["verdict"]["type"] == "choice"


def test_jev_error_falls_back_without_raising(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(J, "_post", lambda *a: (_ for _ in ()).throw(TimeoutError("slow")))
    d = J.decide(L, P, M, "x", 61)
    assert d["engine"] == "rules" and "slow" in d["error"] and d["safety"] == 61


def test_batch_returns_one_decision_per_listing(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    items = [(r["id"], {"listing": r, "profile": P, "math_scores": M, "safety_text": "", "live_safety_index": 70})
             for r in T.LISTINGS[:12]]
    out = J.decide_batch(items)
    assert list(out) == [r["id"] for r in T.LISTINGS[:12]]
