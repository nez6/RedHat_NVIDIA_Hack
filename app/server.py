"""FastAPI server: serves the decision-matrix UI and streams the Nemotron agent crew as NDJSON.

Run:  uvicorn app.server:app --host 0.0.0.0 --port 8501
"""
import itertools, json, os, queue, re, threading, time
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agents, jev as J, live, metric_agents as MA, metrics as M, tools as T, valuation
from pipeline import http as pipeline_http

app = FastAPI(title="DreamHome AI (Nemotron)")
STATIC = os.path.join(os.path.dirname(__file__), "static")


class MatchRequest(BaseModel):
    dream: str = ""
    form: dict = {}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/value")
def value_page():
    return FileResponse(os.path.join(STATIC, "value.html"))


@app.get("/how")
def how_page():
    return FileResponse(os.path.join(STATIC, "how.html"))


@app.get("/api/explain/{listing_id}")
def explain(listing_id: int, max_price: int = 550000, annual_income: int = 165000, min_beds: int = 3,
            commute_destination: str = "rtp"):
    """Every factor calculation for one listing: score, formula and inputs."""
    if listing_id not in T.BY_ID:
        raise HTTPException(404, "unknown listing")
    profile = {"max_price": max_price, "annual_income": annual_income, "min_beds": min_beds,
               "commute_destination": commute_destination}
    rows = M.explain(listing_id, profile)
    total = round(sum(r["score"] * M.DEFAULT_WEIGHTS[r["factor"]] for r in rows) / sum(M.DEFAULT_WEIGHTS.values()), 1)
    return {"listing": T.BY_ID[listing_id], "profile": profile, "factors": rows, "weights": M.DEFAULT_WEIGHTS,
            "composite": total, "data": live.status()}


_adhoc_ids = itertools.count(1)
_adhoc_lock = threading.Lock()


class ValueRequest(BaseModel):
    neighborhood: str
    sqft: int
    beds: int = 3
    baths: float = 2
    year_built: int = 2000
    hoa_monthly: int = 0
    property_type: str = "Single Family"
    features: list[str] = []
    price: int | None = None
    annual_income: int | None = None
    down_payment_pct: float = 20
    commute_destination: str = "downtown raleigh"


@app.post("/api/value")
def value(req: ValueRequest):
    """Free value calculator: estimate + 12-month outlook + the same 10 factor calculations as listed homes."""
    hood = T.HOODS.get(req.neighborhood)
    if not hood:
        raise HTTPException(400, f"unknown neighborhood; choose one of {sorted(T.HOODS)}")
    if not (300 <= req.sqft <= 15000 and 0 <= req.beds <= 12 and 1850 <= req.year_built <= 2030):
        raise HTTPException(400, "sqft, beds or year_built out of range")
    yoy, _ = live.hpi_yoy(hood["city"])
    home = req.model_dump()
    est = valuation.estimate(home, hood, yoy)
    price = req.price or est["estimated_value"]
    with _adhoc_lock:  # score the home with the same metric code by registering a temporary listing id
        lid = -next(_adhoc_ids)
        T.BY_ID[lid] = {"id": lid, "address": "Your home", "neighborhood": req.neighborhood, "city": hood["city"],
                        "lat": hood["lat"], "lon": hood["lon"], "price": int(price), "beds": req.beds, "baths": req.baths,
                        "sqft": req.sqft, "year_built": req.year_built, "lot_acres": 0.0, "hoa_monthly": req.hoa_monthly,
                        "property_type": req.property_type, "features": req.features, "days_on_market": 0}
        try:
            profile = {"max_price": int(price), "annual_income": req.annual_income, "down_payment_pct": req.down_payment_pct,
                       "min_beds": req.beds, "commute_destination": req.commute_destination}
            factors = M.explain(lid, profile)
            finance = T.affordability(lid, req.annual_income, req.down_payment_pct)
        finally:
            T.BY_ID.pop(lid, None)
    return {"valuation": est, "finance": finance, "factors": factors}


@app.get("/architecture")
def architecture():
    return FileResponse(os.path.join(STATIC, "architecture.html"))


@app.get("/api/fallback-demo")
def fallback_demo():
    """Real failure injection: police API down + Jev transport error -> cached snapshot + rule engine."""
    log = []
    t0 = time.time()
    try:
        pipeline_http.get("http://127.0.0.1:9/police-incidents", retries=1)
    except Exception as e:
        log.append({"level": "warn", "msg": f"Live police API unreachable after {int((time.time() - t0) * 1000)} ms ({str(e)[:80]})"})
    listing = next((r for r in T.LISTINGS if live.safety(r["neighborhood"])), T.LISTINGS[0])
    snap = live.safety(listing["neighborhood"])
    if snap:
        log.append({"level": "ok", "msg": f"Tool recovery: using last good snapshot for {listing['neighborhood']} "
                                          f"(safety {snap['safety_index']}, {snap['incidents']} incidents, data/live/safety.json)"})
    else:
        log.append({"level": "ok", "msg": f"Tool recovery: no snapshot for {listing['neighborhood']}, using baseline index"})

    def broken_transport(*_):
        raise TimeoutError("injected Jev timeout")

    m = T.math_inputs(listing["id"], {})
    d = J.decide(listing, {}, m, (snap or {}).get("jev_state", ""), m["safety_index"], post=broken_transport)
    log.append({"level": "ok", "msg": f"Jev call failed ({d.get('error')}); rule engine returned verdict '{d['verdict']}' "
                                      f"for {listing['address']} — pipeline kept running"})
    return {"log": log, "decision": d, "total_ms": int((time.time() - t0) * 1000)}


@app.post("/api/narrate")
def narrate(body: dict):
    """One short Nemotron narrative for a single scored home (shows live vLLM latency)."""
    rows = T.score_matrix([body["id"]], body.get("profile", {}), body.get("weights", {}))
    if not rows:
        return {"error": "unknown listing"}
    r = rows[0]
    prompt = (f"In 2 sentences, explain to a home buyer why {r['listing']['address']} in {r['listing']['neighborhood']} "
              f"scores {r['total']}/100. Sub-scores: {json.dumps(r['scores'])}. Monthly cost ${r['finance']['monthly_total']:,} "
              f"at {r['finance']['rate_pct']}%. Cite numbers exactly as given.")
    t0 = time.time()
    try:
        resp = agents._chat([{"role": "user", "content": prompt}], max_tokens=160)
        text = re.sub(r"<think>.*?</think>", "", resp.choices[0].message.content or "", flags=re.S).strip()
        return {"text": text, "ms": int((time.time() - t0) * 1000), "model": agents.model_name(),
                "tokens": getattr(resp.usage, "completion_tokens", None)}
    except Exception as e:
        return {"error": str(e)[:200], "ms": int((time.time() - t0) * 1000)}


@app.get("/api/health")
def health():
    return agents.health()


@app.get("/api/data-status")
def data_status():
    return {**live.status(), "jev_enabled": J.enabled()}


@app.get("/api/agents")
def agent_registry():
    """The 10 metric agents and the result of the configuration check."""
    return {"agents": MA.describe(), "problems": MA.validate_registry()}


@app.get("/api/gym")
def gym_summary():
    """Latest NeMo Gym evaluation / RL rollout summary (written by scripts/gym_summary.py)."""
    path = os.path.join(os.path.dirname(__file__), "..", "results", "gym", "summary.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"agents": {}, "files": {}}


@app.get("/api/meta")
def meta():
    return {"cities": T.CITIES, "features": T.FEATURES, "destinations": sorted(T.DESTINATIONS),
            "neighborhoods": [{"name": h["name"], "city": h["city"]} for h in T.HOODS.values()],
            "criteria": T.CRITERIA, "listings": len(T.LISTINGS)}


@app.post("/api/rescore")
def rescore(body: dict):
    """Instant re-rank when the user drags a weight slider (no LLM call)."""
    return T.score_matrix(body["ids"], body.get("profile", {}), body["weights"], body.get("jev"))


@app.post("/api/match")
def match(req: MatchRequest):
    q: "queue.Queue" = queue.Queue()

    def worker():
        try:
            agents.run_pipeline(req.dream, req.form, q.put)
        except Exception as e:  # never leave the UI hanging
            q.put({"type": "error", "message": str(e)[:300]})
        q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def stream():
        while (ev := q.get()) is not None:
            yield json.dumps(ev, default=str) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")
