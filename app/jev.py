"""Jev decision layer — typed, probabilistic decisions (Score / Choice / Noul), served by our own vLLM.

TypeSafe's hosted Jev model is closed-weight, so it cannot be loaded into vLLM. Instead this module runs the Jev
decision pattern on the Nemotron vLLM server we already operate (JEV_BACKEND=vllm, the default):
  one forward pass per question, the answer constrained to the option letters with vLLM structured outputs,
  and the probability distribution read from the model's logprobs (temperature-calibrated, JEV_TEMPERATURE).
The response has the same shape as TypeSafe's /v1/systemone, so JEV_BACKEND=typesafe (+ TYPESAFE_API_KEY) swaps in the
hosted model without other changes, and JEV_BACKEND=off uses the rule engine only.

Division of labour (Jev does not do arithmetic, so we never ask it to):
  * Math (affordability, payment, value, commute)  -> deterministic formulas in tools.py, fed by live FRED rates
  * Functional judgements                           -> Jev, fed by live police data + listing facts
      - safety   : Score  (5-level rubric over the live incident mix)
      - fit      : Score  (5-level rubric: does this home match the buyer's dream?)
      - verdict  : Choice (tour_now / consider / pass) with calibrated probabilities
Any error -> transparent rule-based fallback with the same output shape.
"""
import json, math, os, string, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

JEV_URL = os.getenv("JEV_URL", "https://api.typesafe.ai/v1/systemone")
JEV_MODEL = os.getenv("JEV_MODEL", "jev-latest")
TIMEOUT = float(os.getenv("JEV_TIMEOUT", "10"))
VLLM_URL = os.getenv("JEV_VLLM_BASE_URL", os.getenv("NEMOTRON_BASE_URL", "http://localhost:8000/v1")).rstrip("/")
TEMPERATURE = float(os.getenv("JEV_TEMPERATURE", "1.0"))  # calibration temperature applied to the option logprobs
_vllm_model = os.getenv("JEV_VLLM_MODEL") or os.getenv("NEMOTRON_MODEL")

SAFETY_RUBRIC = ["Very unsafe: frequent violent crime", "Below average: notable violent or property crime",
                 "Average for a US metro", "Safe: mostly minor property incidents", "Very safe: rare, minor incidents"]
FIT_RUBRIC = ["Not a fit: misses core needs", "Weak fit: misses several needs", "Partial fit: meets core needs with trade-offs",
              "Strong fit: meets nearly all needs", "Dream home: meets every stated need"]
VERDICTS = {"tour_now": "Strong match the buyer should tour this week",
            "consider": "Worth considering but has a meaningful trade-off",
            "pass": "Does not fit the buyer's budget or needs"}


def backend():
    """vllm (default) | typesafe | off"""
    b = os.getenv("JEV_BACKEND", "vllm").lower()
    if b == "typesafe" and not os.getenv("TYPESAFE_API_KEY"):
        return "off"
    return b if b in ("vllm", "typesafe", "off") else "vllm"


def enabled():
    return backend() != "off"


# ---------------------------------------------------------------- vLLM-served Jev engine
SYSTEM = ("You are a decision engine. Read the STATE, then answer the QUESTION by choosing exactly one option. "
          "Reply with the option letter only. Do not do arithmetic; trust any pre-computed numbers in the STATE.")


def _http_json(url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())


def _model():
    global _vllm_model
    if not _vllm_model:
        _vllm_model = _http_json(VLLM_URL + "/models")["data"][0]["id"]
    return _vllm_model


def distribution(top_logprobs, letters, temperature=None):
    """Option probabilities from the first token's top logprobs, renormalized over the allowed letters."""
    t = temperature or TEMPERATURE
    lp = {}
    for item in top_logprobs or []:
        tok = (item.get("token") or "").strip()
        if tok in letters and tok not in lp:
            lp[tok] = item["logprob"]
    if not lp:
        raise ValueError("no option letter in logprobs")
    floor = min(lp.values()) - 10  # letters missing from top-k get a negligible mass
    z = {k: lp.get(k, floor) / t for k in letters}
    m = max(z.values())
    e = {k: math.exp(v - m) for k, v in z.items()}
    tot = sum(e.values())
    return {k: e[k] / tot for k in letters}


def ask_vllm(state, question, chat=None):
    """One Jev question -> typed answer with probabilities, via one constrained 1-token decode on vLLM."""
    if question["type"] == "noul":
        labels, texts = ["true", "false"], ["Yes", "No"]
    elif question["type"] == "score":
        labels, texts = list(range(len(question["criteria"]))), list(question["criteria"])
    else:
        c = question["criteria"]
        labels, texts = (list(c), [c[k] for k in c]) if isinstance(c, dict) else (list(c), list(c))
    letters = list(string.ascii_uppercase[:len(texts)])
    opts = "\n".join(f"{L}) {t}" for L, t in zip(letters, texts))
    body = {"model": _model(), "max_tokens": 1, "temperature": 0, "logprobs": True, "top_logprobs": 20,
            "chat_template_kwargs": {"enable_thinking": False}, "structured_outputs": {"choice": letters},
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": f"STATE:\n{state}\n\nQUESTION: {question['instructions']}\n{opts}\nAnswer:"}]}
    resp = (chat or (lambda b: _http_json(VLLM_URL + "/chat/completions", b)))(body)
    first = resp["choices"][0]["logprobs"]["content"][0]
    probs = distribution(first["top_logprobs"], letters)
    by_label = {labels[i]: probs[L] for i, L in enumerate(letters)}
    best = max(by_label, key=by_label.get)
    conf = round(by_label[best], 4)
    if question["type"] == "score":
        return {"score": round(sum(i * p for i, p in by_label.items()), 3), "confidence": conf,
                "probabilities": [round(by_label[i], 4) for i in labels]}
    if question["type"] == "noul":
        return {"noul": best == "true", "probability": round(by_label["true"], 4)}
    return {"choice": best, "confidence": conf, "probabilities": {k: round(v, 4) for k, v in by_label.items()}}


def systemone_vllm(state, questions, chat=None):
    """TypeSafe /v1/systemone-compatible response, answered by the local vLLM server (questions run in parallel)."""
    t0 = time.time()
    with ThreadPoolExecutor(max(1, len(questions))) as ex:
        futs = {k: ex.submit(ask_vllm, state, q, chat) for k, q in questions.items()}
        answers = {k: f.result() for k, f in futs.items()}
    return {"model": f"jev-pattern@vllm/{_model()}", "answers": answers, "latency_ms": int((time.time() - t0) * 1000)}


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
    post = post or (systemone_vllm if backend() == "vllm" else _post)
    t0 = time.time()
    try:
        out = parse(post(build_state(listing, profile, math_scores, safety_text), QUESTIONS))
        fb = rule_fallback(listing, profile, math_scores, live_safety_index)
        for k in ("safety", "fit", "verdict"):
            if out.get(k) is None:
                out[k] = fb[k]
        return {**out, "engine": "jev", "backend": backend(), "ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        return {**rule_fallback(listing, profile, math_scores, live_safety_index), "engine": "rules",
                "error": str(e)[:160], "ms": int((time.time() - t0) * 1000)}


def decide_batch(items, workers=12):
    """items: list of (listing_id, kwargs for decide). Fans out in parallel; one failure never aborts the batch."""
    with ThreadPoolExecutor(max(1, min(workers, len(items) or 1))) as ex:
        futs = {lid: ex.submit(decide, **kw) for lid, kw in items}
        return {lid: f.result() for lid, f in futs.items()}
