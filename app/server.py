"""FastAPI server: serves the decision-matrix UI and streams the Nemotron agent crew as NDJSON.

Run:  uvicorn app.server:app --host 0.0.0.0 --port 8501
"""
import itertools, json, os, queue, re, threading, time
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agents, intake as I, jev as J, live, metric_agents as MA, metrics as M, tools as T, valuation
from pipeline import http as pipeline_http
from pipeline import listings as PL
from pipeline.sources import zillow as Z

app = FastAPI(title="DreamHome AI (Nemotron)")
STATIC = os.path.join(os.path.dirname(__file__), "static")


class MatchRequest(BaseModel):
    dream: str = ""
    form: dict = {}
    portal_token: str = ""  # questionnaire path: profile comes from the client's intake record


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/start")
def start_page():
    return FileResponse(os.path.join(STATIC, "start.html"))


@app.get("/portal")
def portal_page():
    return FileResponse(os.path.join(STATIC, "portal.html"))


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


@app.post("/v1/systemone")
def systemone(body: dict):
    """Jev-compatible decision endpoint (state + typed questions -> probabilistic answers), served by our vLLM."""
    if not isinstance(body.get("state"), str) or not isinstance(body.get("questions"), dict) or not body["questions"]:
        raise HTTPException(400, "need 'state' (string) and 'questions' (object)")
    for k, q in body["questions"].items():
        if not isinstance(q, dict) or q.get("type") not in ("score", "choice", "noul") or not isinstance(q.get("instructions"), str) \
                or (q["type"] != "noul" and not isinstance(q.get("criteria"), (list, dict))) or len(q.get("criteria") or []) > 26:
            raise HTTPException(400, f"question {k}: needs type score/choice/noul, instructions, and criteria (list or object) for score/choice")
    try:
        return J.systemone_vllm(body["state"], body["questions"])
    except Exception as e:
        raise HTTPException(503, f"vLLM decision engine unavailable: {str(e)[:160]}")


@app.get("/api/health")
def health():
    return agents.health()


@app.get("/api/data-status")
def data_status():
    by_source = {}
    for r in T.LISTINGS:
        by_source[r.get("source") or "unknown"] = by_source.get(r.get("source") or "unknown", 0) + 1
    return {**live.status(), "jev_enabled": J.enabled(), "jev_backend": J.backend(), "listings_by_source": by_source,
            "live_listings_feed": "rentcast" if os.getenv("RENTCAST_API_KEY") else None,
            "google_maps": bool(os.getenv("GOOGLE_MAPS_API_KEY"))}


@app.get("/api/agents")
def agent_registry():
    """The 6 Nemotron agents, the 10 metrics they own, and the result of the configuration check."""
    return {"agents": MA.describe(), "metrics": MA.describe_metrics(), "problems": MA.validate_registry()}


# ------------------------------------------------------------------ Home Match Toolkit (questionnaire + portal)
@app.post("/api/intake")
def create_intake(body: dict):
    """Questionnaire submission: {answers: {...}, document: {name, data: base64} | null}."""
    try:
        rec = I.create(body.get("answers") or {}, body.get("document"))
    except I.IntakeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # storage failure: never echo internals or document details
        raise HTTPException(503, f"could not store your submission right now ({type(e).__name__}); please try again")
    return {"id": rec["id"], "portal_token": I.portal_token(rec["id"]), "showings_unlocked": rec["showings_unlocked"],
            "document_stored_in": (rec.get("document") or {}).get("stored_in"), "profile": rec["profile"]}


@app.get("/api/portal")
def portal(token: str):
    intake_id = I.verify_token(token)
    rec = I.load(intake_id) if intake_id else None
    if not rec:
        raise HTTPException(401, "this sign-in link is invalid or has expired")
    return I.public_view(rec)


@app.post("/api/admin/intake/{intake_id}/kickoff")
def kickoff(intake_id: str, body: dict = None, x_admin_token: str = Header(default="")):
    """Advisor marks the kickoff meeting done; scripts/send_portal_invites.py then emails the sign-in link."""
    import hmac
    want = os.getenv("ADMIN_TOKEN", "")
    if not want or not hmac.compare_digest(x_admin_token, want):
        raise HTTPException(403, "admin token required")
    try:
        rec = I.mark_kickoff(intake_id, (body or {}).get("advisor"))
    except I.IntakeError as e:
        raise HTTPException(400, str(e))
    if not rec:
        raise HTTPException(404, "unknown intake")
    return {"id": rec["id"], "status": rec["status"], "kickoff_completed_at": rec["kickoff_completed_at"]}


# ------------------------------------------------------------------ Zillow
@app.get("/api/zillow/search-url")
def zillow_search_url(location: str):
    return {"location": location, "url": Z.search_url(location)}


@app.post("/api/zillow/import")
def zillow_import(body: dict):
    """A Zillow page the user saved from their own browser: {name, html}. Parsed, cleaned, added to the listing store."""
    page = body.get("html") or ""
    if not page or len(page) > 15_000_000:
        raise HTTPException(400, "send the saved page's HTML (max 15 MB)")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", body.get("name") or "zillow_page")[:80]
    raw = [dict(r, source="zillow_upload") for r in Z.parse_html(page, name)]
    if not raw:
        hint = "it looks like a CAPTCHA page" if "captcha" in page.lower() else "no Zillow listing data found in it"
        raise HTTPException(422, f"could not read listings from that page: {hint}")
    rows, report = PL.clean(raw)
    added = T.add_listings(rows)
    try:  # keep the page so the next pipeline build includes it
        os.makedirs(PL.SAVED_DIR, exist_ok=True)
        with open(os.path.join(PL.SAVED_DIR, name if name.endswith((".html", ".htm")) else name + ".html"), "w") as f:
            f.write(page)
    except OSError:
        pass
    return {"parsed": len(raw), "added": len(added), "quality": report,
            "listings": [{k: r.get(k) for k in ("id", "address", "city", "price", "beds", "baths", "sqft")} for r in rows[:50]]}


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
            "criteria": T.CRITERIA, "listings": len(T.LISTINGS),
            "listing_ids": [r["id"] for r in T.LISTINGS if r["id"] > 0][:500]}


@app.post("/api/rescore")
def rescore(body: dict):
    """Instant re-rank when the user drags a weight slider (no LLM call)."""
    return T.score_matrix(body["ids"], body.get("profile", {}), body["weights"], body.get("jev"))


@app.post("/api/match")
def match(req: MatchRequest):
    q: "queue.Queue" = queue.Queue()

    profile = None
    if req.portal_token:
        intake_id = I.verify_token(req.portal_token)
        rec = I.load(intake_id) if intake_id else None
        if not rec:
            raise HTTPException(401, "sign-in link invalid or expired")
        profile = dict(rec["profile"])
        if req.form.get("weights"):
            profile["weights"] = {**profile["weights"], **req.form["weights"]}

    def worker():
        try:
            agents.run_pipeline(req.dream, req.form, q.put, profile=profile)
        except Exception as e:  # never leave the UI hanging
            q.put({"type": "error", "message": str(e)[:300]})
        q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def stream():
        while (ev := q.get()) is not None:
            yield json.dumps(ev, default=str) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")
