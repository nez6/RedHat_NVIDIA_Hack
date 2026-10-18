"""Data-pulling agent quality: the three failure modes judged for the hackathon.

  1. Parameter extraction  natural-language constraints -> exact find_listings parameters (nothing dropped, nothing invented)
  2. Empty data            zero matches -> "would you like to relax X?" built from real data; no fake or off-criteria homes
  3. Normalization         messy feed values ("$1.2M", "2,150 sqft", "3 bd", "Active") -> typed, validated listing schema

Hermetic: Nemotron is unreachable in tests (closed port), so model behaviour is simulated with fake chat responses.
scripts/agent_eval.py runs the same cases against the live Nemotron on vLLM and reports accuracy.
"""
import json
import os
from types import SimpleNamespace

import pytest

from app import agents as AG, extract as X, tools as T
from pipeline import listings as PL

CASES = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "extraction_cases.json")))


def run(dream, form=None):
    events = []
    AG.run_pipeline(dream, form or {}, events.append)
    return events


def of(events, kind):
    return [e for e in events if e["type"] == kind]


def fake_response(content="", calls=()):
    tcs = [SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name=n, arguments=json.dumps(a)),
                           model_dump=lambda n=n, a=a, i=i: {"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}})
           for i, (n, a) in enumerate(calls)]
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tcs or None))],
                           usage=SimpleNamespace(completion_tokens=5))


# ------------------------------------------------------------------ 1. parameter extraction
class TestParameterExtraction:
    @pytest.mark.parametrize("case", CASES, ids=[c["prompt"][:40] for c in CASES])
    def test_rules_extract_exact_filters(self, case):
        got, src = X.extract(case["prompt"])
        assert got == case["expected"]  # every stated constraint, exact values, and no invented location / budget
        assert set(src.values()) <= {"rules"}

    def test_user_examples_reach_the_listing_tool(self, monkeypatch):
        calls = []
        real = T.find_listings
        monkeypatch.setattr(T, "find_listings", lambda **kw: calls.append(kw) or real(**kw))
        run("Show me 3-bedroom houses under 450k in Durham")
        assert calls and {k: calls[0][k] for k in ("max_price", "min_beds", "location")} == {"max_price": 450000, "min_beds": 3, "location": "Durham"}
        calls.clear()
        run("Looking for a cheap 2 bed place max $350,000")
        assert calls[0]["max_price"] == 350000 and calls[0]["min_beds"] == 2 and "location" not in calls[0]  # "Any" = no filter

    def test_form_fields_override_text(self):
        p = AG.build_profile("3 bed under 450k in Durham", {"max_price": 500000}, use_llm=False)
        assert p["max_price"] == 500000 and p["min_beds"] == 3 and "max_price" not in p["extracted"]

    def test_model_fills_gaps_but_cannot_override_or_invent(self):
        def chat(messages, tools=None, tool_choice=None):
            assert tool_choice["function"]["name"] == "set_search_filters"  # forced tool call
            return fake_response(calls=[("set_search_filters", {"max_price": 45000, "min_beds": 3, "location": "Raleigh", "max_commute_min": 20})])
        got, src = X.extract("3 bed under $450k, commute no more than twenty mins", chat=chat)
        assert got["max_price"] == 450000 and src["max_price"] == "rules"  # rules win on numbers (model mis-scaled)
        assert got["max_commute_min"] == 20 and src["max_commute_min"] == "llm"  # model fills what the regex missed
        assert "location" not in got  # "Raleigh" never appears in the request -> rejected as hallucinated

    def test_invalid_values_are_dropped(self):
        assert X.validate({"max_price": -5, "min_beds": 40, "location": "Any", "min_baths": "2"}) == {"min_baths": 2.0}
        # seen live: Nemotron turned "quiet street, good schools" into features -> only the real feature vocabulary survives
        assert X.validate({"must_have_features": ["Quiet street", "Garage"], "property_types": ["Mansion"]}) == {"must_have_features": ["garage"]}

    def test_live_nemotron_mistakes_are_rejected(self):
        # actual Nemotron outputs from scripts/agent_eval.py: location invented from context
        assert not X.grounded("location", "safe area with great schools", "Family of four working at RTP, safe area, great schools")
        assert not X.grounded("location", "Durham", "two bedroom condo near Duke, budget 1.2M")
        # ...and rooms inferred from "family of four" when no bedroom count was stated
        assert not X.grounded("min_beds", 3, "Family of four working at RTP, ~$550k, income $165k")
        assert X.grounded("max_commute_min", 20, "commute no more than twenty mins")

    def test_wrong_tool_arguments_cannot_leak_off_criteria_homes(self, monkeypatch):
        # Nemotron "forgets" min_beds and the budget when calling find_listings; the must-have check still applies
        replies = iter([fake_response(calls=[("find_listings", {"location": "Durham"})]), fake_response("done")])
        monkeypatch.setattr(AG, "_chat", lambda *a, **k: next(replies))
        ids = AG.listing_agent({"location": "Durham", "max_price": 450000, "min_beds": 3}, lambda e: None)
        assert ids and all(T.BY_ID[i]["beds"] >= 3 and T.BY_ID[i]["price"] <= 450000 * 1.05 for i in ids)


# ------------------------------------------------------------------ 2. empty data / fallback
class TestEmptyDataFallback:
    def test_zero_results_asks_to_relax_instead_of_inventing(self):
        ev = run("4 bedroom house under $150k in Durham")
        nm = of(ev, "no_match")
        assert nm and of(ev, "shortlist")[0]["ids"] == []
        assert of(ev, "matrix")[0]["rows"] == [] and not of(ev, "crew_agent")  # nothing scored, no agents run on nothing
        text = of(ev, "recommendation")[0]["text"]
        assert text.startswith("I couldn't find any 4+ bedroom homes under $150,000 in Durham") and "Would you like to" in text
        cheapest = min(r["price"] for r in T.LISTINGS if T._in_area(r, "Durham") and r["beds"] >= 4)
        assert nm[0]["data"]["min_price_needed"] == cheapest and f"${cheapest:,}" in text  # suggestion computed from real listings

    def test_suggests_fewer_bedrooms_when_budget_cannot_buy_them(self):
        tips, data = AG.relax_suggestions({"max_price": 250000, "min_beds": 9})
        assert data["max_beds_in_budget"] < 9 and any("bedroom" in t for t in tips)

    def test_matchmaker_never_narrates_an_empty_list(self, monkeypatch):
        monkeypatch.setattr(AG, "_chat", lambda *a, **k: pytest.fail("LLM must not be asked to describe zero homes"))
        assert "No homes matched" in AG.matchmaker([], {"summary": "x", "weights": {}}, lambda e: None)

    def test_hallucinated_address_in_narration_is_replaced(self, monkeypatch):
        ev = run("3 bed under 600k in Cary")
        rows = of(ev, "matrix")[0]["rows"]
        assert rows
        monkeypatch.setattr(AG, "_chat", lambda *a, **k: fake_response(f"Top pick: 742 Evergreen Terrace beats {rows[0]['listing']['address']}."))
        seen = []
        text = AG.matchmaker(rows, {"summary": "x", "weights": {}}, seen.append)
        assert "Evergreen" not in text and rows[0]["listing"]["address"] in text
        assert any(e["type"] == "fallback" and "isn't in the results" in e["reason"] for e in seen)

    def test_real_addresses_pass_the_guard(self):
        assert AG.unknown_addresses("#1 is 101 Maple Drive, #2 is 412 Oak Grove Dr.", ["101 Maple Dr", "412 Oak Grove Dr"]) == []

    def test_every_shortlisted_home_exists_and_meets_must_haves(self):
        ev = run("3 bed 2 bath under $500k in Raleigh")
        ids = of(ev, "shortlist")[0]["ids"]
        assert ids and all(i in T.BY_ID and T.BY_ID[i]["beds"] >= 3 and T.BY_ID[i]["baths"] >= 2 and T.BY_ID[i]["price"] <= 525000 for i in ids)

    def test_new_city_without_listings_returns_nothing_not_raleigh(self, monkeypatch):
        monkeypatch.setattr(T, "ensure_cities", lambda loc, wait=True: [{"city": "Boise, ID", "status": "ready", "areas": 5}])
        out = T.find_listings(location="Boise, ID", max_price=400000)
        assert out["listings"] == [] and "add listings" in out["note"]


# ------------------------------------------------------------------ 3. tool output normalization
MESSY = [
    {"address": "  412  OAK GROVE DR, ", "city": "CARY", "state": "nc", "zip": "27513-4411", "price": "$515,000", "beds": "4 bd", "baths": "2.5 ba",
     "sqft": "2,300 sqft", "lat": "35.7801", "lon": "78.8120", "status": "Active", "home_type": "single family residential", "year_built": "2008",
     "hoa_monthly": "$40/mo", "source": "rentcast_api"},
    {"address": "412 Oak Grove Drive", "city": "Cary", "zip": "27513", "price": "515K", "beds": 4, "baths": 2.5, "sqft": 2300, "lat": 35.7801,
     "lon": -78.812, "status": "FOR_SALE", "source": "zillow_saved_page", "description": "Fenced backyard, 2-car garage"},
    {"address": "9 Sold Way", "price": "300000", "beds": "2", "sqft": "1200", "lat": 35.78, "lon": -78.81, "status": "Recently Sold"},
    {"address": "1 Missing Price Ln", "price": "Call for price", "beds": "3", "sqft": "1500", "lat": 35.78, "lon": -78.81},
    {"address": "77 Main St Unit 4", "city": "Raleigh", "price": "$1.2M", "beds": "Studio", "baths": "1", "sqft": "1,100", "lat": 35.779, "lon": -78.639,
     "status": "coming soon", "home_type": "Condo/Co-op", "year_built": "3015"},
]


class TestNormalization:
    @pytest.mark.parametrize("raw,kind,want", [
        ("$450,000", "price", 450000), ("450K", "price", 450000), ("$1.2M", "price", 1200000), ("2,150 sqft", None, 2150),
        ("3 bd", None, 3), ("2.5 ba", None, 2.5), ("Studio", None, 0), ("--", None, None), ("N/A", "price", None), (None, None, None),
        ("Call for price", "price", None), (1800, None, 1800)])
    def test_to_number(self, raw, kind, want):
        assert PL.to_number(raw, kind) == want

    def test_messy_feed_becomes_typed_schema(self):
        rows, rep = PL.clean(MESSY)
        assert (rep["input"], rep["dropped_not_for_sale"], rep["dropped_incomplete"], rep["dropped_duplicate"], rep["kept"]) == (5, 1, 1, 1, 2)
        types = {"price": int, "beds": int, "baths": float, "sqft": int, "year_built": int, "hoa_monthly": int, "lat": float, "lon": float}
        for r in rows:
            assert all(isinstance(r[k], t) for k, t in types.items()), r
            assert r["price"] > 0 and r["sqft"] > 0 and r["lon"] < 0 and r["property_type"] in ("Single Family", "Townhouse", "Condo")

    def test_duplicates_merge_and_fields_are_cleaned(self):
        rows, _ = PL.clean(MESSY)
        oak = next(r for r in rows if "Oak Grove" in r["address"])
        assert (oak["price"], oak["beds"], oak["zip"], oak["city"]) == (515000, 4, "27513", "Cary")
        assert oak["year_built"] == 2008 and oak["hoa_monthly"] == 40  # filled from the duplicate the richer record replaced
        assert "fenced yard" in oak["features"] and "garage" in oak["features"]
        condo = next(r for r in rows if r["address"] == "77 Main St Unit 4")
        assert (condo["price"], condo["beds"], condo["property_type"]) == (1200000, 0, "Condo")
        assert condo["year_built_estimated"]  # impossible year 3015 discarded, not trusted

    def test_listing_tool_output_is_ui_safe(self):
        out = T.find_listings(max_price=600000, min_beds=3)
        assert out["listings"]
        for r in out["listings"]:
            assert isinstance(r["id"], int) and isinstance(r["price"], int) and isinstance(r["beds"], int) and isinstance(r["features"], list)
            assert r["address"] and r["address"] == r["address"].strip()
