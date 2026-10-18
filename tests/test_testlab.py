"""Agent Test Lab: prompt variants on (simulated) Nemotron, per-agent KPIs, NeMo Gym reward as the validator."""
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import agents as AG, testlab as L
from app.server import app
from conftest import login


def reply(content="", calls=()):
    tcs = [SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name=n, arguments=json.dumps(a)),
                           model_dump=lambda n=n, a=a, i=i: {"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}})
           for i, (n, a) in enumerate(calls)]
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tcs or None))], usage=SimpleNamespace(completion_tokens=3))


def fake_model(answer_best=True, all_ids=True):
    """Calls the agent's tool, then answers. Reads the shortlist and tool name from the prompt it was given."""
    def chat(messages, tools=None, **kw):
        if messages[-1]["role"] == "tool":
            ev = json.loads(messages[-1]["content"])
            best = L._expected_best(STATE["agent"], STATE["ids"], STATE["profile"])[0]
            return reply(f"Best is {best}. ANSWER: {best if answer_best else STATE['ids'][-1] if STATE['ids'][-1] != best else STATE['ids'][0]}" if ev else "")
        ids = STATE["ids"] if all_ids else STATE["ids"][:1]
        return reply(calls=[(tools[0]["function"]["name"], {"listing_ids": ids})])
    return chat


STATE = {}


def test_scoring_agent_kpis_and_gym_reward(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "DIR", str(tmp_path))
    sc = L.scenario("family-rtp")
    agent = L.MA.BY_KEY["budget"]
    STATE.update(agent=agent, ids=sc["ids"], profile=sc["profile"])
    monkeypatch.setattr(AG, "_chat", fake_model())
    good = L.run("budget", None, "default", ["family-rtp"], sink=lambda rows: None)
    r = good["rows"][0]
    assert r["gym_pass"] and r["gym_reward"] == 1.0 and r["kpis"]["coverage"] == 1.0 and r["kpis"]["best_correct"] and r["prompt_is_default"]
    monkeypatch.setattr(AG, "_chat", fake_model(answer_best=False, all_ids=False))
    bad = L.run("budget", "Be brief.", "terse", ["family-rtp"], sink=lambda rows: None)["rows"][0]
    assert not bad["gym_pass"] and bad["gym_reward"] == 0.0 and bad["kpis"]["coverage"] < 1 and not bad["prompt_is_default"]
    board = {(b["agent"], b["label"]): b for b in L.history()["leaderboard"]}
    assert board[("budget", "default")]["pass_rate"] == 1.0 and board[("budget", "terse")]["pass_rate"] == 0.0


def test_listing_agent_filters_and_compliance(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "DIR", str(tmp_path))
    sc = L.scenario("cary-townhome")
    filt = {k: sc["profile"][k] for k in L.FILTER_KEYS if sc["profile"].get(k)}
    replies = iter([reply(calls=[("find_listings", filt)]), reply("found homes")])
    monkeypatch.setattr(AG, "_chat", lambda *a, **k: next(replies))
    r = L.run("listing", None, "default", ["cary-townhome"], sink=lambda rows: None)["rows"][0]
    assert r["kpis"]["filters_exact"] == 1.0 and r["kpis"]["tool_called"]
    dropped = {k: v for k, v in filt.items() if k != "min_beds"}
    replies = iter([reply(calls=[("find_listings", dropped)]), reply("found homes")])
    r2 = L.run("listing", None, "dropped-beds", ["cary-townhome"], sink=lambda rows: None)["rows"][0]
    assert r2["kpis"]["filters_exact"] < 1 and "min_beds" in r2["gym"]["detail"]["missing_or_changed_filters"] and not r2["gym_pass"]


def test_testlab_api_is_admin_only():
    c = TestClient(app)
    assert c.get("/api/testlab").status_code == 401
    login(c, "realtor")
    assert c.get("/api/testlab").status_code == 403
    c = TestClient(app)
    login(c, "admin")
    d = c.get("/api/testlab").json()
    assert len(d["agents"]) == 6 and d["scenarios"] and "NeMo Gym" in d["validator"]
    assert c.post("/api/testlab/run", json={"agent": "nope"}).status_code == 400
    assert c.get("/testlab").status_code == 200
