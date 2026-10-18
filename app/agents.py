"""EstatePulse AI crew: 6 Nemotron agents served by vLLM, then the JEB layer (Jev on the same vLLM).

  Home Match Toolkit questionnaire -> buyer profile + weights (deterministic, app/intake.py)
  1. Zillow Listing Agent        finds homes in the target area (tool: find_listings)
  2-6. Safety & Environment, Commute & Location, Budget & Investment, Lifestyle & Amenities, Property Condition
                                 run in parallel, each with one evidence tool (app/metric_agents.py)
  JEB layer                      Jev calibrated judgements -> decision matrix -> Nemotron writes the recommendation

Every step degrades gracefully: if Nemotron is unreachable or returns bad tool calls, a deterministic fallback keeps
the pipeline running (and the UI log says so).
"""
import json, os, re, threading, time
from openai import OpenAI

from pipeline import databricks_sink as DBX

from . import extract as X, jev as J, live, metric_agents as MA, metrics as M, tools as T

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
DEFAULT_WEIGHTS = dict(M.DEFAULT_WEIGHTS)


def _extract_chat(messages, tools=None, tool_choice="auto"):
    return _chat(messages, tools=tools, tool_choice=tool_choice, max_tokens=300)


def build_profile(dream, form, use_llm=True):
    """Free-text request + form values -> profile (the questionnaire path builds it in app/intake.py instead).
    Filters stated in the text (budget, beds, area, commute...) are extracted (app/extract.py); form fields win."""
    weights = dict(DEFAULT_WEIGHTS)
    extracted, sources = X.extract(dream, chat=_extract_chat if use_llm else None) if (dream or "").strip() else ({}, {})
    profile = {**extracted, **{k: v for k, v in (form or {}).items() if v not in (None, "", []) and k != "weights"}}
    if sources:
        profile["extracted"] = {k: src for k, src in sources.items() if (form or {}).get(k) in (None, "", [])}
    weights.update((form or {}).get("weights") or {})
    if profile.get("cities") and not profile.get("location"):
        profile["location"] = ", ".join(profile["cities"])
    profile.update(weights=weights, summary=(dream or profile.get("summary") or "")[:300])
    return profile


def meets_must_haves(r, filt):
    """Hard constraints a home must satisfy to be shown (5% price stretch, same as search_listings)."""
    full = T.BY_ID.get(r["id"], r)
    return not ((filt.get("max_price") and full["price"] > filt["max_price"] * 1.05)
                or (filt.get("min_beds") and full["beds"] < filt["min_beds"])
                or (filt.get("min_baths") and full["baths"] < filt["min_baths"]))


def listing_agent(profile, emit):
    """Zillow Listing Agent: find_listings for the buyer's area. Every candidate is checked against the buyer's
    must-haves (price, beds, baths) whatever the model passed to the tool; the area may be widened (and says so)
    but the must-haves never are. Zero matches -> an empty shortlist, never made-up or off-criteria homes."""
    agent = MA.BY_KEY["listing"]
    filt = {k: profile.get(k) for k in ("location", "max_price", "min_beds", "min_baths", "property_types", "must_have_features")}
    found, feed = {}, {}
    try:
        text, res = run_agent(agent.name, agent.system_prompt, f"Buyer profile: {json.dumps(filt)}", ["find_listings"], emit, max_steps=3)
        for name, _, out in res:
            if isinstance(out, dict):
                feed = {k: v for k, v in out.items() if k != "listings"}
                for r in out.get("listings") or []:
                    found[r["id"]] = r
        if text:
            emit({"type": "say", "agent": agent.name, "text": text})
    except Exception as e:
        emit({"type": "fallback", "agent": agent.name, "reason": str(e)[:160]})
    if len(found) < 6:  # deterministic safety net: the same tool with the buyer's exact filters
        out = T.find_listings(**{k: v for k, v in filt.items() if v not in (None, [], "")}, limit=15)
        feed = feed or {k: v for k, v in out.items() if k != "listings"}
        for r in out["listings"]:
            found.setdefault(r["id"], r)
        emit({"type": "fallback", "agent": agent.name, "reason": f"ran find_listings with the buyer's filters: {len(found)} candidates"})
    bad = [i for i, r in found.items() if not meets_must_haves(r, filt)]
    for i in bad:
        found.pop(i)
    if bad:
        emit({"type": "fallback", "agent": agent.name, "reason": f"dropped {len(bad)} home(s) that broke the buyer's must-haves"})
    ids = list(found)[:12]
    emit({"type": "shortlist", "ids": ids, "feed": feed, "sources": sorted({r.get("source") or "unknown" for r in found.values()})})
    return ids


def relax_suggestions(profile):
    """What would produce matches: computed from the listings we actually have (no guessing)."""
    f = {k: profile.get(k) for k in ("location", "max_price", "min_beds", "min_baths")}
    tips, data = [], {}
    pool = [r for r in T.LISTINGS if not f.get("location") or T._in_area(r, f["location"])]
    ok_rooms = [r for r in pool if r["beds"] >= (f.get("min_beds") or 0) and r["baths"] >= (f.get("min_baths") or 0)]
    if f.get("max_price") and ok_rooms:
        p = min(r["price"] for r in ok_rooms)
        data["min_price_needed"] = p
        tips.append(f"raise the budget to about ${p:,} (the lowest-priced home with {f.get('min_beds') or 'any'}+ beds"
                    + (f" in {f['location']}" if f.get("location") else "") + ")")
    within = [r for r in pool if not f.get("max_price") or r["price"] <= f["max_price"] * 1.05]
    if f.get("min_beds") and within:
        b = max(r["beds"] for r in within)
        if b < f["min_beds"]:
            data["max_beds_in_budget"] = b
            tips.append(f"consider {b} bedroom{'s' if b != 1 else ''} (the most available within budget)")
    if f.get("location"):
        anywhere = [r for r in T.LISTINGS if meets_must_haves(r, f)]
        data["matches_without_area"] = len(anywhere)
        if anywhere:
            tips.append(f"widen the area: {len(anywhere)} home(s) across the Triangle meet the budget and room count")
    if not tips:
        tips.append("loosen one must-have (budget, bedrooms or area)")
    return tips, data


def no_match_message(profile, tips):
    want = []
    if profile.get("min_beds"):
        want.append(f"{profile['min_beds']}+ bedroom")
    want.append("homes")
    if profile.get("max_price"):
        want.append(f"under ${profile['max_price']:,}")
    if profile.get("location"):
        want.append(f"in {profile['location']}")
    return f"I couldn't find any {' '.join(want)} in the current listings. Would you like to " + "; or ".join(tips) + "?"


def matchmaker(rows, profile, emit):
    top = [{"id": r["listing"]["id"], "address": r["listing"]["address"], "neighborhood": r["listing"]["neighborhood"],
            "price": r["listing"]["price"], "total": r["total"], "scores": r["scores"],
            "monthly": r["finance"]["monthly_total"], "rate_pct": r["finance"]["rate_pct"],
            "jev_verdict": (r.get("jev") or {}).get("verdict")} for r in rows[:3]]
    system = ("You are the JEB layer's narrator. Given the buyer's dream and the decision-matrix results, write a warm, specific "
              "recommendation: 1 short paragraph on the #1 home (cite 2-3 scores exactly as given, and mention the live mortgage rate), then one line each for #2 and #3 "
              "on the trade-off vs #1. Under 140 words. Plain text, no markdown headers.")
    user = f"Dream: {profile.get('summary')}\nWeights: {profile['weights']}\nTop 3: {json.dumps(top)}"
    t = top[0] if top else None
    fallback = (f"#1 is {t['address']} in {t['neighborhood']} (fit {t['total']}), about ${t['monthly']:,}/mo." if t
                else "No homes matched — try widening the budget or cities.")
    if not top:  # nothing to recommend: never ask the model to narrate an empty list (that is how fake houses appear)
        return fallback
    try:
        t0 = time.time()
        resp = _chat([{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens=400)
        text = re.sub(r"<think>.*?</think>", "", resp.choices[0].message.content or "", flags=re.S).strip()
        emit({"type": "llm", "agent": "JEB Recommendation", "ms": int((time.time() - t0) * 1000), "tool_calls": []})
    except Exception as e:
        emit({"type": "fallback", "agent": "JEB Recommendation", "reason": str(e)[:160]})
        return fallback
    stray = unknown_addresses(text, [r["listing"]["address"] for r in rows])
    if stray or not text:
        emit({"type": "fallback", "agent": "JEB Recommendation",
              "reason": f"narration mentioned a home that isn't in the results ({stray[0]})" if stray else "empty narration"})
        return fallback
    return text


_ADDR = re.compile(r"\b\d{2,6}\s+(?:[A-Z][\w'.-]*\s+){1,4}(?:St|Street|Dr|Drive|Rd|Road|Ave|Avenue|Ln|Lane|Ct|Court|Way|Pl|Place|Cir|Circle|"
                   r"Blvd|Boulevard|Trl|Trail|Pkwy|Parkway|Loop|Ter|Terrace|Run|Row|Sq|Square)\b")


def unknown_addresses(text, known):
    """Street addresses in the model's text that don't belong to any home in the results (hallucination check)."""
    norm = lambda a: re.sub(r"[^a-z0-9]", "", a.lower())  # noqa: E731
    keys = [norm(k) for k in known]
    return [m.group(0) for m in _ADDR.finditer(text or "") if not any(norm(m.group(0)) in k or k in norm(m.group(0)) for k in keys)]


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
def run_pipeline(dream, form, emit, profile=None):
    t0 = time.time()
    emit({"type": "start", "health": health(), "data": live.status(), "jev_enabled": J.enabled(), "agents": len(MA.AGENTS)})
    profile = profile or build_profile(dream, form)
    profile.setdefault("weights", dict(DEFAULT_WEIGHTS))
    emit({"type": "profile", "profile": profile})
    ids = listing_agent(profile, emit)                     # agent 1
    if not ids:  # nothing meets the must-haves: say so and suggest what to relax, instead of scoring off-criteria homes
        tips, data = relax_suggestions(profile)
        msg = no_match_message(profile, tips)
        emit({"type": "no_match", "suggestions": tips, "data": data, "text": msg})
        emit({"type": "matrix", "criteria": T.CRITERIA, "rows": [], "weights": profile["weights"], "jev": {}})
        emit({"type": "recommendation", "text": msg})
        emit({"type": "done", "ms": int((time.time() - t0) * 1000)})
        return
    MA.run_all(ids, profile, emit, run_agent)               # agents 2-6 in parallel on Nemotron
    jev = jev_layer(ids, profile, emit)                     # JEB: calibrated judgements on vLLM
    rows = T.score_matrix(ids, profile, profile["weights"], jev)
    if DBX.configured():  # governance: audit every AI recommendation in Unity Catalog (non-blocking)
        engine = "jev" if any(v.get("engine") == "jev" for v in jev.values()) else "rules"
        threading.Thread(target=DBX.log_decision, args=(_MODEL or "unknown", engine, profile, rows), daemon=True).start()
    emit({"type": "matrix", "criteria": T.CRITERIA, "rows": rows, "weights": profile["weights"], "jev": jev})
    emit({"type": "recommendation", "text": matchmaker(rows, profile, emit)})
    emit({"type": "done", "ms": int((time.time() - t0) * 1000)})
