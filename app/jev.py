"""Jev (TypeSafe AI) decision layer — calibrated, typed decisions in ~100 ms.

Division of labour (Jev does not do arithmetic, so we never ask it to):
  * Math (affordability, payment, value, commute)  -> deterministic formulas in tools.py, fed by live FRED rates
  * Functional judgements                           -> Jev, fed by live police data + listing facts
      - safety   : Score  (5-level rubric over the live incident mix)
      - fit      : Score  (5-level rubric: does this home match the buyer's dream?)
      - verdict  : Choice (tour_now / consider / pass) with calibrated probabilities
No TYPESAFE_API_KEY -> transparent rule-based fallback with the same output shape.
"""
import json, os, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

JEV_URL = os.getenv("JEV_URL", "https://api.typesafe.ai/v1/systemone")
JEV_MODEL = os.getenv("JEV_MODEL", "jev-latest")
TIMEOUT = float(os.getenv("JEV_TIMEOUT", "10"))

SAFETY_RUBRIC = ["Very unsafe: frequent violent crime", "Below average: notable violent or property crime",
                 "Average for a US metro", "Safe: mostly minor property incidents", "Very safe: rare, minor incidents"]
FIT_RUBRIC = ["Not a fit: misses core needs", "Weak fit: misses several needs", "Partial fit: meets core needs with trade-offs",
              "Strong fit: meets nearly all needs", "Dream home: meets every stated need"]
VERDICTS = {"tour_now": "Strong match the buyer should tour this week",
            "consider": "Worth considering but has a meaningful trade-off",
            "pass": "Does not fit the buyer's budget or needs"}


def enabled():
    return bool(os.getenv("TYPESAFE_API_KEY"))


def _post(state, questions):
    body = json.dumps({"model": JEV_MODEL, "state": state, "questions": questions}).encode()
    req = urllib.request.Request(JEV_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {os.environ['TYPESAFE_API_KEY']}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())


def _score01(ans, levels=5):
    """Jev Score answer -> 0..100. Accepts expected score, or a probability distribution."""
    if ans is None:
        return None
    if isinstance(ans, (int, float)):
        return round(float(ans) / (levels - 1) * 100)
    if isinstance(ans, dict):
        if isinstance(ans.get("score"), (int, float)):
            return round(float(ans["score"]) / (levels - 1) * 100)
        probs = ans.get("probabilities")
        if isinstance(probs, list) and probs:
            return round(sum(i * p for i, p in enumerate(probs)) / (levels - 1) * 100)
        if isinstance(probs, dict) and probs:
            vals = list(probs.values())
            return round(sum(i * p for i, p in enumerate(vals)) / (levels - 1) * 100)
    return None


def parse(resp):
    a = resp.get("answers", resp)
    v = a.get("verdict") or {}
    return {"safety": _score01(a.get("safety")), "fit": _score01(a.get("fit")),
            "verdict": v.get("choice") if isinstance(v, dict) else v,
            "verdict_probs": v.get("probabilities") if isinstance(v, dict) else None,
            "confidence": (a.get("fit") or {}).get("confidence") if isinstance(a.get("fit"), dict) else None}


def build_state(listing, profile, math_scores, safety_text):
    L = listing
    return (f"BUYER: {profile.get('summary', '')} Must-haves: {', '.join(profile.get('must_have_features') or []) or 'none'}. "
            f"Min beds {profile.get('min_beds') or 'any'}. Commute to {profile.get('commute_destination') or 'downtown raleigh'}.\n"
            f"HOME: {L['address']}, {L['neighborhood']}, {L['city']}. {L['property_type']}, {L['beds']} bd / {L['baths']} ba, "
            f"{L['sqft']} sqft, built {L['year_built']}, lot {L['lot_acres']} ac. Features: {', '.join(L['features'])}.\n"
            f"PRE-COMPUTED MATH (trust these numbers): price ${L['price']:,}; est. monthly cost ${math_scores['monthly']:,} "
            f"at {math_scores['rate']}% (live FRED 30-yr rate); housing ratio {math_scores.get('ratio') or 'n/a'}% of income; "
            f"price vs neighborhood median {math_scores['vs_area']}%; commute {math_scores['commute_min']} min.\n"
            f"SAFETY DATA (live police feed): {safety_text}")


QUESTIONS = {
    "safety": {"type": "score", "instructions": "Using only the SAFETY DATA, how safe is this area for a family to live?",
               "criteria": SAFETY_RUBRIC},
    "fit": {"type": "score", "instructions": "How well does this HOME match the BUYER's stated needs and must-haves?",
            "criteria": FIT_RUBRIC},
    "verdict": {"type": "choice", "instructions": "Given the buyer, the home, the pre-computed math and safety data, what should the buyer do?",
                "criteria": VERDICTS},
}


def rule_fallback(listing, profile, math_scores, live_safety_index):
    wants = [f.lower() for f in profile.get("must_have_features") or []]
    hit = sum(f in listing["features"] for f in wants) / len(wants) if wants else 0.6
    beds_ok = listing["beds"] >= (profile.get("min_beds") or 0)
    fit = round(100 * (0.7 * hit + 0.3 * beds_ok))
    ratio = math_scores.get("ratio") or 25
    verdict = "tour_now" if fit >= 70 and ratio <= 30 else "pass" if fit < 35 or ratio > 40 else "consider"
    return {"safety": live_safety_index, "fit": fit, "verdict": verdict, "verdict_probs": None, "confidence": None}


def decide(listing, profile, math_scores, safety_text, live_safety_index, post=None):
    """post: optional transport override (used by the failure-injection demo and tests)."""
    if post is None and not enabled():
        return {**rule_fallback(listing, profile, math_scores, live_safety_index), "engine": "rules", "ms": 0}
    post = post or _post
    t0 = time.time()
    try:
        out = parse(post(build_state(listing, profile, math_scores, safety_text), QUESTIONS))
        fb = rule_fallback(listing, profile, math_scores, live_safety_index)
        for k in ("safety", "fit", "verdict"):
            if out.get(k) is None:
                out[k] = fb[k]
        return {**out, "engine": "jev", "ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        return {**rule_fallback(listing, profile, math_scores, live_safety_index), "engine": "rules",
                "error": str(e)[:160], "ms": int((time.time() - t0) * 1000)}


def decide_batch(items, workers=12):
    """items: list of (listing_id, kwargs for decide). Fans out in parallel; one failure never aborts the batch."""
    with ThreadPoolExecutor(max(1, min(workers, len(items) or 1))) as ex:
        futs = {lid: ex.submit(decide, **kw) for lid, kw in items}
        return {lid: f.result() for lid, f in futs.items()}
