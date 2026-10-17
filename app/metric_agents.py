"""The 10 custom metric agents — one Nemotron tool-calling agent per homebuyer-matrix metric.

Each agent owns exactly one tool (get_<metric>_inputs), one scorer (app/metrics.py) and one weight.
The agent gathers evidence with its tool and writes a short takeaway; the score itself is deterministic.
`validate_registry()` is the configuration check used by tests, /api/agents and the dashboard.
"""
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import metrics as M
from . import tools as T


@dataclass(frozen=True)
class MetricAgent:
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
        return (f"You are the {self.name}, one of 10 specialist agents in EstatePulse AI. You own the "
                f"'{M.LABELS[self.key]}' score. Logic: {self.logic} Call {self.tool} exactly once with ALL shortlisted "
                f"listing_ids, then reply in at most 2 sentences naming the best and worst listing id for your metric and why. "
                f"Never invent numbers; quote the tool output.")


AGENTS = [
    MetricAgent("lifestyle", "Lifestyle Agent", "listing text + features; Jev fit judgement (images planned)",
                "Matches amenities and must-haves to the buyer's dream; highest-variance score.", "preference weights",
                "eval", "Lifestyle evidence per listing: features, must-haves matched, fit score.", ("must_have_features",)),
    MetricAgent("school", "School Agent", "neighborhood school rating (raw 1-10, no normalization)",
                "Uses the raw rating; listings below the buyer's minimum are halved.", "threshold filter",
                "eval", "School rating per listing and whether it meets the buyer's minimum.", ("min_school_rating",)),
    MetricAgent("safety", "Safety Agent", "live Raleigh PD / Cary PD incidents + Jev safety judgement",
                "Severity-weighted incident density within 1 mile (90 days), blended 50/50 with Jev.", "customizable weight",
                "eval", "Live safety index, source and incident context per listing."),
    MetricAgent("environment", "Environment & Weather Agent", "FEMA flood zones, Open-Meteo AQI and 12-month weather extremes",
                "Penalizes 100/500-year floodplains, poor air quality, and extreme heat/rain/wind days.", "customizable weight",
                "eval", "Flood zone, 30-day AQI and extreme-weather days per listing."),
    MetricAgent("urban", "Urban Agent", "distance to the buyer's target hub + walk score",
                "70% proximity to the chosen urban center, 30% walkability.", "target area",
                "eval", "Miles to the buyer's urban target and walk score per listing.", ("urban_target",)),
    MetricAgent("commute", "Commute Agent", "drive-time estimate to the buyer's work destination",
                "Minutes to destination; halved when over the buyer's max commute.", "max commute & destination",
                "eval", "Commute minutes per listing and whether it exceeds the cap.", ("commute_destination", "max_commute_min")),
    MetricAgent("forecast", "Value & Forecast Agent", "FRED FHFA house-price index (MSA) + price vs neighborhood median",
                "12-month appreciation = 60% metro HPI momentum + mean reversion to the neighborhood price/sqft.", "risk tolerance",
                "eval+rl", "12-month value forecast and expected appreciation per listing.", ("risk_tolerance",)),
    MetricAgent("deal", "Deal Calculator Agent", "live FRED 30-yr rate, PITI + HOA, discount to area median",
                "60% affordability (budget + 28% housing ratio) + 40% discount to market.", "expected return / budget",
                "eval+rl", "Monthly cost, housing ratio, discount and deal score per listing.", ("max_price", "annual_income", "down_payment_pct")),
    MetricAgent("rooms", "Rooms & Layout Agent", "beds, baths, sqft",
                "Bedrooms vs requirement plus usable square footage.", "min rooms / sqft",
                "eval", "Beds, baths, sqft and space score per listing.", ("min_beds", "min_sqft")),
    MetricAgent("maintenance", "Maintenance Agent", "year built + HOA",
                "Penalizes age and HOA cost; extra penalty above the buyer's HOA cap.", "HOA tolerance & age",
                "eval", "Property age, HOA and maintenance score per listing.", ("max_hoa",)),
]
BY_KEY = {a.key: a for a in AGENTS}

# register each agent's tool schema so the generic tool-calling loop can expose it
for _a in AGENTS:
    T.SCHEMAS[_a.tool] = M.schema(_a.key, _a.tool_description)


def validate_registry():
    """Configuration check: returns a list of problems (empty == every agent is wired correctly)."""
    problems = []
    keys = [a.key for a in AGENTS]
    if len(AGENTS) != 10 or len(set(keys)) != 10:
        problems.append(f"expected 10 unique agents, found {len(set(keys))}")
    if set(keys) != set(M.METRICS):
        problems.append(f"agents {sorted(set(keys) ^ set(M.METRICS))} do not match matrix metrics")
    for a in AGENTS:
        if a.key not in M.FUNCS:
            problems.append(f"{a.key}: no scorer")
        if a.tool not in T.SCHEMAS:
            problems.append(f"{a.key}: tool schema missing")
        if a.key not in M.DEFAULT_WEIGHTS:
            problems.append(f"{a.key}: no default weight")
        if a.gym_mode not in ("eval", "eval+rl"):
            problems.append(f"{a.key}: bad gym_mode {a.gym_mode}")
        if a.tool not in a.system_prompt:
            problems.append(f"{a.key}: prompt does not name its tool")
    if sorted(a.key for a in AGENTS if a.gym_mode == "eval+rl") != ["deal", "forecast"]:
        problems.append("eval+rl must be exactly forecast and deal")
    return problems


def describe():
    return [{"key": a.key, "name": a.name, "label": M.LABELS[a.key], "tool": a.tool, "sources": a.sources, "logic": a.logic,
             "controls": a.controls, "gym_mode": a.gym_mode, "default_weight": M.DEFAULT_WEIGHTS[a.key],
             "profile_fields": list(a.profile_fields)} for a in AGENTS]


def run_one(agent, ids, profile, emit, run_agent):
    t0 = time.time()
    called = {}

    def capture(args):
        called["ids"] = args.get("listing_ids") or ids
        return M.run_tool(agent.key, called["ids"], profile)

    text, status = "", "done"
    try:
        text, _ = run_agent(agent.name, agent.system_prompt, f"Shortlisted listing_ids: {ids}", [agent.tool], emit,
                            max_steps=3, capture={agent.tool: capture})
    except Exception as e:
        status = "fallback"
        emit({"type": "fallback", "agent": agent.name, "reason": str(e)[:160]})
    if "ids" not in called:  # agent never used its tool -> run it deterministically so the matrix stays complete
        if status == "done":
            status = "fallback"
            emit({"type": "fallback", "agent": agent.name, "reason": "agent did not call its tool; ran tool directly"})
        M.run_tool(agent.key, ids, profile)
    emit({"type": "metric_agent", "key": agent.key, "agent": agent.name, "status": status,
          "ms": int((time.time() - t0) * 1000), "text": (text or "")[:300]})
    return agent.key, status


def run_all(ids, profile, emit, run_agent, workers=10):
    with ThreadPoolExecutor(workers) as ex:
        return dict(f.result() for f in [ex.submit(run_one, a, ids, profile, emit, run_agent) for a in AGENTS])
