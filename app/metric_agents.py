"""EstatePulse AI's 6 Nemotron agents (the architecture slide), plus the 10 metric specs they own.

  1. Zillow Listing Agent        find_listings: live feed + parsed Zillow pages, de-duplicated, off-market removed
  2. Safety & Environment Agent  safety_score + environment (police incidents, FEMA flood, AQI, weather)
  3. Commute & Location Agent    commute_score (Google Maps Distance Matrix when configured)
  4. Budget & Investment Agent   value_deal_score: deal calculator + 12-month forecast (NeMo Gym eval + RL)
  5. Lifestyle & Amenities Agent urban_city_score, school_score, lifestyle fit
  6. Property Condition Agent    maintenance_score, room_layout_score

Agents 2-6 each call one tool, get_<group>_evidence, which returns every metric they own for the shortlist. Scores are
deterministic (app/metrics.py); the agent reads the evidence and writes the takeaway. The JEB layer (Jev on vLLM)
then adds calibrated judgements and builds the decision matrix.

The 10 MetricSpecs are the unit NeMo Gym evaluates: 8 eval harnesses + eval/RL rollouts for forecast and deal.
`validate_registry()` is the configuration check used by tests, /api/agents and the dashboard.
"""
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import metrics as M
from . import tools as T


@dataclass(frozen=True)
class MetricSpec:
    key: str
    name: str
    sources: str
    logic: str
    controls: str
    gym_mode: str  # "eval" or "eval+rl" (NeMo Gym)
    tool_description: str
    profile_fields: tuple = field(default_factory=tuple)

    @property
    def tool(self):
        return M.tool_name(self.key)

    @property
    def system_prompt(self):
        return (f"You are the {self.name}, a metric specialist in EstatePulse AI. You own the "
                f"'{M.LABELS[self.key]}' score. Logic: {self.logic} Call {self.tool} exactly once with ALL shortlisted "
                f"listing_ids, then reply in at most 2 sentences naming the best and worst listing id for your metric and why. "
                f"Never invent numbers; quote the tool output.")


SPECS = [
    MetricSpec("lifestyle", "Lifestyle Agent", "listing text + features; Jev fit judgement (images planned)",
                "Matches amenities and must-haves to the buyer's dream; highest-variance score.", "preference weights",
                "eval", "Lifestyle evidence per listing: features, must-haves matched, fit score.", ("must_have_features",)),
    MetricSpec("school", "School Agent", "neighborhood school rating (raw 1-10, no normalization)",
                "Uses the raw rating; listings below the buyer's minimum are halved.", "threshold filter",
                "eval", "School rating per listing and whether it meets the buyer's minimum.", ("min_school_rating",)),
    MetricSpec("safety", "Safety Agent", "live Raleigh PD / Cary PD incidents + Jev safety judgement",
                "Severity-weighted incident density within 1 mile (90 days), blended 50/50 with Jev.", "customizable weight",
                "eval", "Live safety index, source and incident context per listing."),
    MetricSpec("environment", "Environment & Weather Agent", "FEMA flood zones, Open-Meteo AQI and 12-month weather extremes",
                "Penalizes 100/500-year floodplains, poor air quality, and extreme heat/rain/wind days.", "customizable weight",
                "eval", "Flood zone, 30-day AQI and extreme-weather days per listing."),
    MetricSpec("urban", "Urban Agent", "distance to the buyer's target hub + walk score",
                "70% proximity to the chosen urban center, 30% walkability.", "target area",
                "eval", "Miles to the buyer's urban target and walk score per listing.", ("urban_target",)),
    MetricSpec("commute", "Commute Agent", "drive-time estimate to the buyer's work destination",
                "Minutes to destination; halved when over the buyer's max commute.", "max commute & destination",
                "eval", "Commute minutes per listing and whether it exceeds the cap.", ("commute_destination", "max_commute_min")),
    MetricSpec("forecast", "Value & Forecast Agent", "FRED FHFA house-price index (MSA) + price vs neighborhood median",
                "12-month appreciation = 60% metro HPI momentum + mean reversion to the neighborhood price/sqft.", "risk tolerance",
                "eval+rl", "12-month value forecast and expected appreciation per listing.", ("risk_tolerance",)),
    MetricSpec("deal", "Deal Calculator Agent", "live FRED 30-yr rate, PITI + HOA, discount to area median",
                "60% affordability (budget + 28% housing ratio) + 40% discount to market.", "expected return / budget",
                "eval+rl", "Monthly cost, housing ratio, discount and deal score per listing.", ("max_price", "annual_income", "down_payment_pct")),
    MetricSpec("rooms", "Rooms & Layout Agent", "beds, baths, sqft",
                "Bedrooms vs requirement plus usable square footage.", "min rooms / sqft",
                "eval", "Beds, baths, sqft and space score per listing.", ("min_beds", "min_sqft")),
    MetricSpec("maintenance", "Maintenance Agent", "year built + HOA",
                "Penalizes age and HOA cost; extra penalty above the buyer's HOA cap.", "HOA tolerance & age",
                "eval", "Property age, HOA and maintenance score per listing.", ("max_hoa",)),
]
SPEC_BY_KEY = {a.key: a for a in SPECS}
for _a in SPECS:  # per-metric tools stay registered: NeMo Gym and /how use them
    T.SCHEMAS[_a.tool] = M.schema(_a.key, _a.tool_description)


@dataclass(frozen=True)
class CrewAgent:
    key: str
    name: str
    question: str          # the questionnaire question that sets this agent's weight
    sub_scores: str        # names used in the scoring spec
    metrics: tuple         # metric keys this agent owns (empty for the listing agent)
    sources: str
    gym_mode: str

    @property
    def tool(self):
        return "find_listings" if not self.metrics else f"get_{self.key}_evidence"

    @property
    def system_prompt(self):
        if not self.metrics:
            return ("You are the Zillow Listing Agent in EstatePulse AI. Call find_listings once with the buyer's target area "
                    "and must-haves. If fewer than 6 homes come back, call it again without the least important filter. "
                    "Finish with one sentence: how many homes, from which sources, and anything the data-quality step removed.")
        names = ", ".join(M.LABELS[m] for m in self.metrics)
        return (f"You are the {self.name} in EstatePulse AI, one of 6 Nemotron agents. You own: {names}. "
                f"Call {self.tool} exactly once with ALL shortlisted listing_ids, then reply in at most 2 sentences naming the "
                f"best and worst listing id for your area and why. Never invent numbers; quote the tool output.")


AGENTS = [
    CrewAgent("listing", "Zillow Listing Agent", "Target Neighborhoods / Zip Codes + must-have parameters", "candidate set",
              (), "Zillow saved pages (parser) + live listings feed (RentCast today, MLS / Zillow Bridge later); "
              "dedupes and drops sold, pending, off-market and incomplete records", "eval"),
    CrewAgent("safety", "Safety & Environment Agent", "How important is low crime rate and neighborhood stability?",
              "safety_score", ("safety", "environment"), "Raleigh PD / Cary PD incidents (FBI NIBRS categories), FEMA flood zones, "
              "Open-Meteo air quality + weather extremes", "eval"),
    CrewAgent("commute", "Commute & Location Agent", "What is your daily work location and target max commute time?",
              "commute_score", ("commute",), "Google Maps Distance Matrix (when GOOGLE_MAPS_API_KEY is set) or drive-time estimate", "eval"),
    CrewAgent("budget", "Budget & Investment Agent", "Are you seeking cash flow, appreciation, or below-market value?",
              "value_deal_score", ("deal", "forecast"), "FRED mortgage rates + FHFA house-price index, listing price vs "
              "neighborhood price/sqft", "eval+rl"),
    CrewAgent("lifestyle", "Lifestyle & Amenities Agent", "Do you prefer vibrant urban living or suburban space and schools?",
              "urban_city_score, school_score", ("urban", "school", "lifestyle"), "GreatSchools ratings from the Zillow listing, "
              "walkability, distance to your urban hub, listing features", "eval"),
    CrewAgent("property", "Property Condition Agent", "What is your tolerance for renovation, HOA fees and age of home?",
              "maintenance_score, room_layout_score", ("maintenance", "rooms"), "Zillow listing description + facts, year built, "
              "HOA disclosure, beds / baths / sqft", "eval"),
]
BY_KEY = {a.key: a for a in AGENTS}
GROUP_OF = {m: a.key for a in AGENTS for m in a.metrics}


def _group_schema(a):
    names = ", ".join(M.LABELS[m] for m in a.metrics)
    return {"type": "function", "function": {
        "name": a.tool, "description": f"{names} evidence and scores for each shortlisted listing.",
        "parameters": {"type": "object", "properties": {
            "listing_ids": {"type": "array", "items": {"type": "integer"}, "description": "Shortlisted listing ids"}},
            "required": ["listing_ids"]}}}


def group_evidence(agent, listing_ids, profile, jev=None):
    """One row per listing with every metric the agent owns: {listing_id, <metric>: {score, ...inputs}}."""
    rows = {}
    for m in agent.metrics:
        for r in M.run_tool(m, listing_ids, profile, jev):
            rows.setdefault(r["listing_id"], {"listing_id": r["listing_id"]})[m] = {k: v for k, v in r.items() if k != "listing_id"}
    return list(rows.values())


for _a in AGENTS:
    if _a.metrics:
        T.SCHEMAS[_a.tool] = _group_schema(_a)


def validate_registry():
    """Configuration check: returns a list of problems (empty == every agent is wired correctly)."""
    problems = []
    if len(AGENTS) != 6 or len({a.key for a in AGENTS}) != 6:
        problems.append(f"expected 6 unique agents, found {len({a.key for a in AGENTS})}")
    owned = [m for a in AGENTS for m in a.metrics]
    if sorted(owned) != sorted(M.METRICS) or len(owned) != len(set(owned)):
        problems.append(f"metric ownership must cover all 10 metrics exactly once: {sorted(set(M.METRICS) ^ set(owned))}")
    for a in AGENTS:
        if a.tool not in T.SCHEMAS:
            problems.append(f"{a.key}: tool schema missing")
        if a.tool not in a.system_prompt:
            problems.append(f"{a.key}: prompt does not name its tool")
        if a.gym_mode not in ("eval", "eval+rl"):
            problems.append(f"{a.key}: bad gym_mode {a.gym_mode}")
    for s in SPECS:
        if s.key not in M.FUNCS:
            problems.append(f"{s.key}: no scorer")
        if s.key not in M.DEFAULT_WEIGHTS:
            problems.append(f"{s.key}: no default weight")
        if s.tool not in T.SCHEMAS:
            problems.append(f"{s.key}: metric tool schema missing")
    if sorted(s.key for s in SPECS if s.gym_mode == "eval+rl") != ["deal", "forecast"]:
        problems.append("eval+rl must be exactly forecast and deal")
    return problems


def describe():
    return [{"key": a.key, "name": a.name, "tool": a.tool, "question": a.question, "sub_scores": a.sub_scores,
             "metrics": list(a.metrics), "labels": [M.LABELS[m] for m in a.metrics], "sources": a.sources,
             "gym_mode": a.gym_mode, "default_weights": {m: M.DEFAULT_WEIGHTS[m] for m in a.metrics}} for a in AGENTS]


def describe_metrics():
    return [{"key": s.key, "name": s.name, "label": M.LABELS[s.key], "tool": s.tool, "agent": GROUP_OF[s.key],
             "sources": s.sources, "logic": s.logic, "controls": s.controls, "gym_mode": s.gym_mode,
             "default_weight": M.DEFAULT_WEIGHTS[s.key], "profile_fields": list(s.profile_fields)} for s in SPECS]


def run_one(agent, ids, profile, emit, run_agent):
    """Run one of the 5 scoring agents. Falls back to running its tool directly so the matrix is always complete."""
    t0 = time.time()
    called = {}

    def capture(args):
        called["ids"] = args.get("listing_ids") or ids
        return group_evidence(agent, called["ids"], profile)

    text, status = "", "done"
    try:
        text, _ = run_agent(agent.name, agent.system_prompt, f"Shortlisted listing_ids: {ids}", [agent.tool], emit,
                            max_steps=3, capture={agent.tool: capture})
    except Exception as e:
        status = "fallback"
        emit({"type": "fallback", "agent": agent.name, "reason": str(e)[:160]})
    if "ids" not in called:
        if status == "done":
            status = "fallback"
            emit({"type": "fallback", "agent": agent.name, "reason": "agent did not call its tool; ran tool directly"})
        group_evidence(agent, ids, profile)
    emit({"type": "crew_agent", "key": agent.key, "agent": agent.name, "status": status, "metrics": list(agent.metrics),
          "ms": int((time.time() - t0) * 1000), "text": (text or "")[:300]})
    return agent.key, status


def run_all(ids, profile, emit, run_agent, workers=5):
    scoring = [a for a in AGENTS if a.metrics]
    with ThreadPoolExecutor(workers) as ex:
        return dict(f.result() for f in [ex.submit(run_one, a, ids, profile, emit, run_agent) for a in scoring])
