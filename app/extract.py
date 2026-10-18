"""Natural-language request -> search filters for the listing tool.

    "3-bedroom houses under 450k in Durham, 25 min commute to RTP"  ->
    {"max_price": 450000, "min_beds": 3, "location": "Durham", "max_commute_min": 25, "commute_destination": "rtp"}

Two extractors, merged field by field:
  rules  deterministic regex parser: exact on numbers ("450k", "$1.2M", "2,150"), never invents a value
  llm    Nemotron with a forced `set_search_filters` tool call (vLLM guided decoding): handles phrasing the rules miss
Numbers come from the rules when both found them (the model may round or mis-scale); the model fills the gaps.
Every value is validated (range checks) so a bad extraction is dropped rather than sent to the tool.
"""
import json
import re

WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "a": 1, "single": 1}
FIELDS = ("location", "max_price", "min_beds", "min_baths", "max_commute_min", "commute_destination", "annual_income",
          "max_monthly_payment", "property_types", "must_have_features")
BOUNDS = {"max_price": (25_000, 50_000_000), "min_beds": (0, 15), "min_baths": (0, 15), "max_commute_min": (1, 240),
          "annual_income": (5_000, 50_000_000), "max_monthly_payment": (100, 200_000)}
DEST_ALIASES = {"rtp": "rtp", "research triangle park": "rtp", "duke": "duke", "unc": "unc", "chapel hill campus": "unc",
                "nc state": "nc state", "ncsu": "nc state", "rdu": "rdu airport", "rdu airport": "rdu airport", "the airport": "rdu airport",
                "downtown raleigh": "downtown raleigh", "downtown durham": "downtown durham", "downtown cary": "cary"}
TYPES = {r"town ?(?:house|home)s?": "Townhouse", r"condo(?:minium)?s?": "Condo", r"single[- ]family": "Single Family"}
_NUM = r"\$?\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k|m|mm|million|thousand)?\b"


def _places():
    """Known Triangle cities and neighborhoods (lower-case -> display name)."""
    from . import tools as T
    names = {h["city"] for h in T.HOODS.values() if not h.get("generated")} | {n for n, h in T.HOODS.items() if not h.get("generated")}
    names |= set(T.CITIES)
    return {n.lower(): n for n in names}


def _features():
    try:
        from . import tools as T
        return T.FEATURES
    except Exception:
        return []


def _money(num, suffix):
    v = float(num.replace(",", ""))
    s = (suffix or "").lower()
    if s in ("k", "thousand"):
        v *= 1_000
    elif s in ("m", "mm", "million"):
        v *= 1_000_000
    return int(round(v))


def rule_params(text):
    t = " " + (text or "") + " "
    low = t.lower()
    out = {}
    # ---- money: classify each amount by its context
    prices = []
    for m in re.finditer(_NUM, low):
        num, suffix = m.group(1), m.group(2)
        has_dollar = "$" in low[max(0, m.start() - 1):m.start(1)]
        if not (has_dollar or suffix or "," in num):
            continue  # bare numbers are ZIPs, counts, minutes...
        v = _money(num, suffix)
        before, after = low[max(0, m.start() - 30):m.start()], low[m.end():m.end() + 22]
        income_before = re.search(r"(income|salary|make|making|earn|household|combined)\W*(?:of|is|about|around|~)?\W*$", before)
        if income_before or re.match(r"\s*(a year|/yr|per year|annual|income|salary)", after):
            out.setdefault("annual_income", v)
        elif re.match(r"\s*(/\s*mo|per month|a month|monthly|/month)", after) or re.search(r"(monthly|payment|per month)\W*(?:of|under|below|max|is)?\W*$", before):
            out.setdefault("max_monthly_payment", v)
        elif re.match(r"\s*(down|%)", after) or re.search(r"down payment\W*(?:of)?\W*$", before):
            continue
        elif v >= 25_000:
            prices.append(v)
    if prices:  # "between 400k and 500k" / "under 450k" -> the upper bound
        out["max_price"] = max(prices)
    # ---- rooms
    m = re.search(r"\b(\d+|" + "|".join(WORDS) + r")\s*(?:\+|or more)?\s*[- ]?\s*(?:bed(?:room)?s?|br|bd|bdrm)\b", low)
    if m:
        out["min_beds"] = int(m.group(1)) if m.group(1).isdigit() else WORDS[m.group(1)]
    elif re.search(r"\bstudio\b", low):
        out["min_beds"] = 0
    m = re.search(r"\b(\d+(?:\.5)?|" + "|".join(WORDS) + r")\s*(?:\+|or more)?\s*[- ]?\s*(?:full\s+)?(?:bath(?:room)?s?|ba)\b", low)
    if m:
        out["min_baths"] = float(m.group(1)) if m.group(1)[0].isdigit() else float(WORDS[m.group(1)])
    # ---- commute
    m = re.search(r"(\d+)\s*[- ]?\s*(?:min(?:ute)?s?|mins?)\b", low)
    if m and re.search(r"commute|drive|work|office|job|from|to ", low):
        out["max_commute_min"] = int(m.group(1))
    dest_span = None
    for alias in sorted(DEST_ALIASES, key=len, reverse=True):
        for dm in re.finditer(r"\b" + re.escape(alias) + r"\b", low):
            ctx = low[max(0, dm.start() - 40):dm.start()]
            landmark = alias in ("rtp", "research triangle park", "duke", "unc", "nc state", "ncsu", "rdu airport")
            if landmark or re.search(r"commute|work|works|working|job|office|drive|near|close to|to\s*$|at\s*$|from\s*$", ctx):
                out["commute_destination"], dest_span = DEST_ALIASES[alias], dm.span()
                break
        if dest_span:
            break
    # ---- location: known places, ZIPs, or "in <City>, ST" (destination phrase excluded)
    scan = low if not dest_span else low[:dest_span[0]] + " " * (dest_span[1] - dest_span[0]) + low[dest_span[1]:]
    found = []
    for key, name in sorted(_places().items(), key=lambda kv: -len(kv[0])):
        mm = re.search(r"\b" + re.escape(key) + r"\b", scan)
        if mm and not any(key in f.lower() for _, f in found):
            found.append((mm.start(), name))
            scan = scan[:mm.start()] + " " * len(key) + scan[mm.end():]
    for z in re.finditer(r"\b(\d{5})(?:-\d{4})?\b", t):
        if not re.match(r"\s*(k|m|sq|square|/)", low[z.end():z.end() + 4]) and "$" not in low[max(0, z.start() - 2):z.start()]:
            found.append((z.start(), z.group(1)))
    other = re.search(r"\bin\s+([A-Z][a-zA-Z.'-]+(?:\s+[A-Z][a-zA-Z.'-]+)?),?\s+([A-Z]{2})\b", t)
    if other and other.group(2) != "NC":
        found.append((other.start(), f"{other.group(1)}, {other.group(2)}"))
    if found:
        out["location"] = ", ".join(n for _, n in sorted(found))
    # ---- type + features
    types = [v for pat, v in TYPES.items() if re.search(r"\b" + pat + r"\b", low)]
    if types:
        out["property_types"] = types
    try:
        from . import tools as T
        feats = [f for f in T.FEATURES if re.search(r"\b" + re.escape(f.lower()) + r"\b", low)]
    except Exception:
        feats = []
    if feats:
        out["must_have_features"] = feats
    return out


TOOL = {"type": "function", "function": {
    "name": "set_search_filters",
    "description": "Record the home-search filters the buyer stated. Only include values the buyer actually gave; never guess.",
    "parameters": {"type": "object", "properties": {
        "location": {"type": "string", "description": "City / neighborhood / ZIP the buyer wants, comma separated. Omit if none."},
        "max_price": {"type": "integer", "description": "Maximum purchase price in US dollars (450k -> 450000)."},
        "min_beds": {"type": "integer"}, "min_baths": {"type": "number"},
        "max_commute_min": {"type": "integer", "description": "Longest acceptable commute in minutes."},
        "commute_destination": {"type": "string", "description": "Where they commute to, e.g. rtp, duke, unc, downtown raleigh."},
        "annual_income": {"type": "integer", "description": "Household income per year in US dollars."},
        "max_monthly_payment": {"type": "integer"},
        "property_types": {"type": "array", "items": {"type": "string", "enum": ["Single Family", "Townhouse", "Condo"]}},
        "must_have_features": {"type": "array", "items": {"type": "string"}}}}}}


def llm_params(text, chat):
    """chat(messages, tools, tool_choice) -> OpenAI-style response. Returns the tool-call arguments (validated) or {}."""
    resp = chat([{"role": "system", "content": "Extract the buyer's home-search filters by calling set_search_filters. "
                                               "Convert amounts to whole dollars. Leave out anything the buyer did not say."},
                 {"role": "user", "content": text}], tools=[TOOL],
                tool_choice={"type": "function", "function": {"name": "set_search_filters"}})
    calls = resp.choices[0].message.tool_calls or []
    if not calls:
        return {}
    try:
        return validate(json.loads(calls[0].function.arguments or "{}"))
    except (json.JSONDecodeError, TypeError):
        return {}


def validate(p):
    """Drop values outside sane bounds or of the wrong type; normalise 'Any'/'anywhere' locations to None."""
    out = {}
    for k in FIELDS:
        v = (p or {}).get(k)
        if v in (None, "", [], {}):
            continue
        if k in BOUNDS:
            try:
                v = float(str(v).replace(",", "").replace("$", ""))
            except ValueError:
                continue
            lo, hi = BOUNDS[k]
            if not lo <= v <= hi:
                continue
            v = v if k == "min_baths" else int(v)
        elif k == "location":
            v = str(v).strip()
            if v.lower() in ("any", "anywhere", "none", "n/a", "unknown", "triangle", "nc", "north carolina"):
                continue
        elif k in ("property_types", "must_have_features"):
            vocab = {x.lower(): x for x in (TYPES.values() if k == "property_types" else _features())}
            v = [vocab[str(x).strip().lower()] for x in (v if isinstance(v, list) else [v]) if x and str(x).strip().lower() in vocab]
            if not v:  # free-text wishes ("quiet street") aren't filterable features -> left to the scoring agents
                continue
        else:
            v = str(v).strip().lower()
        out[k] = v
    return out


def extract(text, chat=None):
    """-> (filters, {field: 'rules'|'llm'}). Rules win on numbers; the model fills fields the rules didn't find."""
    rules = validate(rule_params(text))
    llm = {}
    if chat and (text or "").strip():
        try:
            llm = llm_params(text, chat)
        except Exception:
            llm = {}
    merged, src = dict(rules), {k: "rules" for k in rules}
    for k, v in llm.items():
        if k not in merged and grounded(k, v, text):
            merged[k], src[k] = v, "llm"
    return merged, src


def grounded(field, value, text):
    """Guard against a hallucinated filter: the model's value must be traceable to the buyer's words."""
    low = (text or "").lower()
    if field == "location":
        return all(part.strip().lower() in low for part in str(value).split(",") if part.strip())
    if field == "commute_destination":
        return any(a in low for a, d in DEST_ALIASES.items() if d == value) or str(value).lower() in low
    if field in BOUNDS:  # a number must be stated AND the buyer must have talked about that thing (no "family of four" -> 3 beds)
        cue = {"min_beds": r"bed|\bbr\b|\bbd|bdrm|studio", "min_baths": r"bath|\bba\b", "max_commute_min": r"min|hour|hr\b",
               "max_price": r"\$|\d\s*(k|m)\b|thousand|million|budget|price|afford", "annual_income": r"income|salary|make|earn|a year|per year",
               "max_monthly_payment": r"month|/mo|payment"}[field]
        return bool(re.search(r"\d|\b(" + "|".join(WORDS) + r"|ten|fifteen|twenty|thirty|forty|fifty|sixty|half|hundred|thousand|million)\b", low)) and bool(re.search(cue, low))
    if field == "property_types":
        return any(re.search(pat, low) for pat, v in TYPES.items() if v in value) or "house" in low or "home" in low
    return all(str(f).lower() in low for f in value) if isinstance(value, list) else True
