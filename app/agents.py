"""DreamHome multi-agent crew, all running on NVIDIA Nemotron served by vLLM.

Crew:
  1. Concierge      - turns the buyer's free-text dream into a structured profile + weights (tool: set_buyer_profile)
  2. Listings Scout - searches the housing DB (tool: search_listings)
  3. Neighborhood   - safety / schools / commute per shortlisted home (tool: neighborhood_report)
  4. Finance        - monthly cost + affordability per home (tool: affordability)
  5. Matchmaker     - reads the deterministic decision matrix and explains the top picks

Every agent degrades gracefully: if Nemotron is unreachable or returns bad tool calls,
a deterministic fallback keeps the pipeline running (and the UI log says so).
"""
import json, os, re, time
from concurrent.futures import ThreadPoolExecutor
from openai import OpenAI

from . import jev as J, live, tools as T

BASE_URL = os.getenv("NEMOTRON_BASE_URL", "http://localhost:8000/v1")
API_KEY = os.getenv("NEMOTRON_API_KEY", "EMPTY")
client = OpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=float(os.getenv("NEMOTRON_TIMEOUT", "60")),
                max_retries=int(os.getenv("NEMOTRON_MAX_RETRIES", "2")))
_MODEL = os.getenv("NEMOTRON_MODEL")
# Nemotron 3 has a reasoning mode; off by default for speed, set NEMOTRON_THINKING=1 to enable.
THINKING = os.getenv("NEMOTRON_THINKING", "0") == "1"


def model_name():
    global _MODEL
    if not _MODEL:
        _MODEL = client.models.list().data[0].id
    return _MODEL


def health():
    try:
        return {"ok": True, "model": model_name(), "base_url": BASE_URL}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "base_url": BASE_URL}


def _chat(messages, tools=None, tool_choice="auto", max_tokens=1200):
    kw = dict(model=model_name(), messages=messages, temperature=0.2, max_tokens=max_tokens,
              extra_body={"chat_template_kwargs": {"enable_thinking": THINKING}})
    if tools:
        kw["tools"], kw["tool_choice"] = tools, tool_choice
    return client.chat.completions.create(**kw)


def run_agent(name, system, user, tool_names, emit, max_steps=6, capture=None):
    """Generic tool-calling loop. Returns (final_text, list_of_tool_results)."""
    tools = [T.SCHEMAS[t] for t in tool_names]
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    results = []
    for step in range(max_steps):
        t0 = time.time()
        resp = _chat(msgs, tools=tools)
        ms = int((time.time() - t0) * 1000)
        msg = resp.choices[0].message
        calls = msg.tool_calls or []
        emit({"type": "llm", "agent": name, "ms": ms, "tool_calls": [c.function.name for c in calls],
              "tokens": getattr(resp.usage, "completion_tokens", None)})
        if not calls:
            return (msg.content or "").strip(), results
        msgs.append({"role": "assistant", "content": msg.content or "",
                     "tool_calls": [c.model_dump() for c in calls]})
        for c in calls:
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            t1 = time.time()
            if c.function.name in T.IMPLS:
                try:
                    out = T.IMPLS[c.function.name](**args)
                except TypeError as e:
                    out = {"error": f"bad arguments: {e}"}
            elif capture and c.function.name in capture:
                out = capture[c.function.name](args)
            else:
                out = {"error": f"unknown tool {c.function.name}"}
            results.append((c.function.name, args, out))
            emit({"type": "tool", "agent": name, "tool": c.function.name, "args": args,
                  "ms": int((time.time() - t1) * 1000),
                  "result_preview": (f"{len(out)} rows" if isinstance(out, list) else
                                     {k: out[k] for k in list(out)[:5]} if isinstance(out, dict) else str(out)[:120])})
            msgs.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps(out)[:6000]})
    return "", results


# ---------------- individual agents ----------------
DEFAULT_WEIGHTS = {"affordability": 4, "safety": 4, "schools": 3, "commute": 3, "space": 3, "features": 3, "value": 2, "fit": 4}


def concierge(dream, form, emit):
    profile = {}

    def capture(args):
        profile.update(args)
        return {"saved": True}

    system = ("You are the Concierge agent for DreamHome, a Raleigh-Durham (NC Triangle) home-matching service. "
              "Read the buyer's description and the form values, then call set_buyer_profile exactly once. "
              "Use only enum values from the schema. Infer weights 0-5 from what they emphasise "
              "(e.g. 'kids' -> schools 5, 'walk to cafes' -> commute/features). Form values override the text.")
    user = f"Buyer description:\n{dream}\n\nForm values (JSON):\n{json.dumps(form)}"
    try:
        run_agent("Concierge", system, user, ["set_buyer_profile"], emit, max_steps=2,
                  capture={"set_buyer_profile": capture})
    except Exception as e:
        emit({"type": "fallback", "agent": "Concierge", "reason": str(e)[:160]})
    # merge: explicit form values win, then model output, then defaults
    merged = {"weights": dict(DEFAULT_WEIGHTS)}
    merged.update({k: v for k, v in profile.items() if v not in (None, [], "")})
    merged["weights"] = {**DEFAULT_WEIGHTS, **(profile.get("weights") or {})}
    for k, v in form.items():
        if v not in (None, "", []):
            if k == "weights":
                merged["weights"].update(v)
            else:
                merged[k] = v
    merged.setdefault("summary", dream[:160])
    emit({"type": "profile", "profile": merged})
    return merged


def scout(profile, emit):
    system = ("You are the Listings Scout. Call search_listings to find candidate homes for the buyer profile. "
              "If fewer than 6 results come back, relax the least important filter and search again. "
              "Finish with one short sentence on what you found.")
    filt = {k: profile.get(k) for k in ("max_price", "min_beds", "min_baths", "cities", "property_types", "must_have_features")}
    found = {}
    try:
        text, res = run_agent("Listings Scout", system, f"Buyer profile: {json.dumps(filt)}",
                              ["search_listings"], emit, max_steps=3)
        for name, _, out in res:
            if isinstance(out, list):
                for r in out:
                    found[r["id"]] = r
        if text:
            emit({"type": "say", "agent": "Listings Scout", "text": text})
    except Exception as e:
        emit({"type": "fallback", "agent": "Listings Scout", "reason": str(e)[:160]})
    if len(found) < 6:  # deterministic safety net
        relaxed = dict(filt, must_have_features=None, cities=filt.get("cities"))
        for r in T.search_listings(**relaxed, limit=15) or T.search_listings(max_price=filt.get("max_price"), limit=15):
            found.setdefault(r["id"], r)
        emit({"type": "fallback", "agent": "Listings Scout", "reason": f"topped up to {len(found)} candidates"})
    ids = list(found)[:12]
    emit({"type": "shortlist", "ids": ids})
    return ids


def _analyst(name, tool, system, ids, profile, emit):
    user = (f"Listing ids: {ids}. Buyer: income={profile.get('annual_income')}, "
            f"down_payment_pct={profile.get('down_payment_pct', 20)}, commute_destination={profile.get('commute_destination', 'downtown raleigh')}. "
            f"Call {tool} for EVERY listing id (you may call it several times in one turn), then give a 2-sentence takeaway "
            "naming the strongest and weakest listing ids.")
    try:
        text, _ = run_agent(name, system, user, [tool], emit, max_steps=4)
        if text:
            emit({"type": "say", "agent": name, "text": text})
    except Exception as e:
        emit({"type": "fallback", "agent": name, "reason": str(e)[:160]})


def neighborhood(ids, profile, emit):
    _analyst("Neighborhood Analyst", "neighborhood_report",
             "You are the Neighborhood Analyst. You judge safety, schools, walkability and commute.", ids, profile, emit)


def finance(ids, profile, emit):
    _analyst("Finance Advisor", "affordability",
             "You are the Finance Advisor. You judge monthly cost, housing ratio (<28% is healthy) and price vs area median. "
             "This is an estimate, not financial advice.", ids, profile, emit)


def matchmaker(rows, profile, emit):
    top = [{"id": r["listing"]["id"], "address": r["listing"]["address"], "neighborhood": r["listing"]["neighborhood"],
            "price": r["listing"]["price"], "total": r["total"], "scores": r["scores"],
            "monthly": r["finance"]["monthly_total"], "rate_pct": r["finance"]["rate_pct"],
            "jev_verdict": (r.get("jev") or {}).get("verdict")} for r in rows[:3]]
    system = ("You are the Matchmaker. Given the buyer's dream and the decision-matrix results, write a warm, specific "
              "recommendation: 1 short paragraph on the #1 home (cite 2-3 scores exactly as given, and mention the live mortgage rate), then one line each for #2 and #3 "
              "on the trade-off vs #1. Under 140 words. Plain text, no markdown headers.")
    user = f"Dream: {profile.get('summary')}\nWeights: {profile['weights']}\nTop 3: {json.dumps(top)}"
    try:
        t0 = time.time()
        resp = _chat([{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens=400)
        text = re.sub(r"<think>.*?</think>", "", resp.choices[0].message.content or "", flags=re.S).strip()
        emit({"type": "llm", "agent": "Matchmaker", "ms": int((time.time() - t0) * 1000), "tool_calls": []})
    except Exception as e:
        emit({"type": "fallback", "agent": "Matchmaker", "reason": str(e)[:160]})
        t = top[0] if top else None
        text = (f"#1 is {t['address']} in {t['neighborhood']} (fit {t['total']}), about ${t['monthly']:,}/mo." if t
                else "No homes matched — try widening the budget or cities.")
    return text


def jev_layer(ids, profile, emit):
    """Jev decision layer: one calibrated call per home (safety + fit + verdict), all in parallel."""
    t0 = time.time()
    items = []
    for lid in ids:
        L = T.BY_ID[int(lid)]
        m = T.math_inputs(lid, profile)
        lv = live.safety(L["neighborhood"])
        items.append((lid, {"listing": L, "profile": profile, "math_scores": m,
                            "safety_text": (lv or {}).get("jev_state") or f"{L['neighborhood']}: baseline safety index {m['safety_index']}.",
                            "live_safety_index": m["safety_index"]}))
    out = J.decide_batch(items)
    engines = {d["engine"] for d in out.values()}
    emit({"type": "jev", "agent": "Jev Decision Layer", "engine": "jev" if "jev" in engines else "rules",
          "calls": len(out), "ms": int((time.time() - t0) * 1000),
          "p50_call_ms": sorted(d["ms"] for d in out.values())[len(out) // 2] if out else 0,
          "verdicts": {str(k): v["verdict"] for k, v in out.items()},
          "errors": [v["error"] for v in out.values() if v.get("error")][:2]})
    return {str(k): v for k, v in out.items()}


# ---------------- orchestrator ----------------
def run_pipeline(dream, form, emit):
    t0 = time.time()
    emit({"type": "start", "health": health(), "data": live.status(), "jev_enabled": J.enabled()})
    profile = concierge(dream, form, emit)
    ids = scout(profile, emit)
    with ThreadPoolExecutor(2) as ex:  # Neighborhood + Finance run in parallel on Nemotron
        f1 = ex.submit(neighborhood, ids, profile, emit)
        f2 = ex.submit(finance, ids, profile, emit)
        f1.result(), f2.result()
    jev = jev_layer(ids, profile, emit)
    rows = T.score_matrix(ids, profile, profile["weights"], jev)
    emit({"type": "matrix", "criteria": T.CRITERIA, "rows": rows, "weights": profile["weights"], "jev": jev})
    emit({"type": "recommendation", "text": matchmaker(rows, profile, emit)})
    emit({"type": "done", "ms": int((time.time() - t0) * 1000)})
