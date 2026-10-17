"""FastAPI server: serves the decision-matrix UI and streams the Nemotron agent crew as NDJSON.

Run:  uvicorn app.server:app --host 0.0.0.0 --port 8501
"""
import json, os, queue, threading
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import agents, jev as J, live, tools as T

app = FastAPI(title="DreamHome AI (Nemotron)")
STATIC = os.path.join(os.path.dirname(__file__), "static")


class MatchRequest(BaseModel):
    dream: str = ""
    form: dict = {}


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


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
