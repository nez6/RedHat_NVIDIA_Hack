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
    monkeypatch.setenv("JEV_BACKEND", "typesafe")
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
    monkeypatch.setenv("JEV_BACKEND", "typesafe")
    monkeypatch.setattr(J, "_post", lambda *a: (_ for _ in ()).throw(TimeoutError("slow")))
    d = J.decide(L, P, M, "x", 61)
    assert d["engine"] == "rules" and "slow" in d["error"] and d["safety"] == 61


def test_batch_returns_one_decision_per_listing(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    items = [(r["id"], {"listing": r, "profile": P, "math_scores": M, "safety_text": "", "live_safety_index": 70})
             for r in T.LISTINGS[:12]]
    out = J.decide_batch(items)
    assert list(out) == [r["id"] for r in T.LISTINGS[:12]]


# ---------------------------------------------------------------- vLLM-served Jev
def fake_vllm(letter_logprobs):
    """A chat-completions transport that returns the given first-token logprobs and records the request."""
    seen = []

    def chat(body):
        seen.append(body)
        top = [{"token": k, "logprob": v} for k, v in letter_logprobs.items()] + [{"token": "<tool_call>", "logprob": -9999.0}]
        best = max(letter_logprobs, key=letter_logprobs.get)
        return {"choices": [{"message": {"content": best}, "logprobs": {"content": [{"token": best, "top_logprobs": top}]}}]}
    return chat, seen


def test_distribution_renormalizes_over_option_letters():
    p = J.distribution([{"token": "C", "logprob": -1.0}, {"token": "B", "logprob": -1.0}, {"token": "The", "logprob": -0.1}], ["A", "B", "C"])
    assert p["B"] == p["C"] and abs(sum(p.values()) - 1) < 1e-9 and p["A"] < 1e-4
    sharp = J.distribution([{"token": "A", "logprob": -0.1}, {"token": "B", "logprob": -2.0}], ["A", "B"], temperature=0.5)
    soft = J.distribution([{"token": "A", "logprob": -0.1}, {"token": "B", "logprob": -2.0}], ["A", "B"], temperature=2.0)
    assert sharp["A"] > soft["A"]  # calibration temperature sharpens / softens


def test_vllm_score_choice_and_noul(monkeypatch):
    monkeypatch.setattr(J, "_vllm_model", "nemotron")
    chat, seen = fake_vllm({"A": -5.0, "B": -5.0, "C": -5.0, "D": -0.01, "E": -5.0})
    score = J.ask_vllm("state", J.QUESTIONS["safety"], chat)
    assert round(score["score"]) == 3 and score["confidence"] > 0.9 and len(score["probabilities"]) == 5
    body = seen[0]
    assert body["max_tokens"] == 1 and body["structured_outputs"] == {"choice": ["A", "B", "C", "D", "E"]}
    assert body["chat_template_kwargs"] == {"enable_thinking": False} and "D) Safe" in body["messages"][1]["content"]
    chat, _ = fake_vllm({"A": -3.0, "B": -0.05, "C": -4.0})
    assert J.ask_vllm("s", J.QUESTIONS["verdict"], chat)["choice"] == "consider"
    chat, _ = fake_vllm({"A": -0.1, "B": -2.5})
    noul = J.ask_vllm("s", {"type": "noul", "instructions": "Is it in a flood zone?"}, chat)
    assert noul["noul"] is True and noul["probability"] > 0.8


def test_decide_uses_vllm_backend_by_default(monkeypatch):
    monkeypatch.setenv("JEV_BACKEND", "vllm")
    monkeypatch.setattr(J, "_vllm_model", "nemotron")
    chat, seen = fake_vllm({"A": -6.0, "B": -6.0, "C": -6.0, "D": -6.0, "E": -0.01})
    monkeypatch.setattr(J, "_http_json", lambda url, body=None: chat(body))
    d = J.decide(L, P, M, "Oakwood: 900 incidents", 80)
    assert d["engine"] == "jev" and d["backend"] == "vllm" and d["fit"] >= 95 and len(seen) == 3


def test_vllm_down_falls_back_to_rules(monkeypatch):
    monkeypatch.setenv("JEV_BACKEND", "vllm")
    monkeypatch.setattr(J, "_vllm_model", "nemotron")
    monkeypatch.setattr(J, "_http_json", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("vllm down")))
    d = J.decide(L, P, M, "x", 55)
    assert d["engine"] == "rules" and "vllm down" in d["error"] and d["safety"] == 55
