"""FastAPI server: serves the decision-matrix UI and streams the Nemotron agent crew as NDJSON.

Run:  uvicorn app.server:app --host 0.0.0.0 --port 8501
"""
import json, os, queue, re, threading, time
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import agents, jev as J, live, tools as T
from pipeline import http as pipeline_http

app = FastAPI(title="DreamHome AI (Nemotron)")
STATIC = os.path.join(os.path.dirname(__file__), "static")


class MatchRequest(BaseModel):
    dream: str = ""
    form: dict = {}


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


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


@app.get("/api/meta")
def meta():
    return {"cities": T.CITIES, "features": T.FEATURES, "destinations": sorted(T.DESTINATIONS),
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
