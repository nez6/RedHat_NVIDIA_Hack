"""Decision-matrix invariants: bounded, deterministic, weight-monotonic, fast."""
import time

from app import tools as T

IDS = [r["id"] for r in T.LISTINGS]
PROFILE = {"max_price": 550_000, "annual_income": 165_000, "min_beds": 3,
           "must_have_features": ["fenced yard", "home office"], "commute_destination": "rtp"}
EVEN = {c: 1 for c in T.CRITERIA}


def test_every_criterion_is_bounded_0_100():
    for lid in IDS:
        s = T.criterion_scores(lid, PROFILE)
        assert set(s) == set(T.CRITERIA)
        assert all(0 <= v <= 100 for v in s.values()), (lid, s)


def test_ranking_is_deterministic():
    a = [r["listing"]["id"] for r in T.score_matrix(IDS, PROFILE, EVEN)]
    b = [r["listing"]["id"] for r in T.score_matrix(IDS, PROFILE, EVEN)]
    assert a == b


def test_single_weight_ranks_by_that_criterion():
    w = {c: 0 for c in T.CRITERIA} | {"deal": 5}
    rows = T.score_matrix(IDS, PROFILE, w)
    vals = [r["scores"]["deal"] for r in rows]
    assert vals == sorted(vals, reverse=True)
    assert all(r["total"] == r["scores"]["deal"] for r in rows)


def test_raising_a_weight_never_hurts_the_leader_on_it():
    rows = T.score_matrix(IDS, PROFILE, EVEN)
    best_safety = max(rows, key=lambda r: r["scores"]["safety"])["listing"]["id"]
    rank = lambda w: [r["listing"]["id"] for r in T.score_matrix(IDS, PROFILE, w)].index(best_safety)
    assert rank(EVEN | {"safety": 5}) <= rank(EVEN)


def test_jev_decision_overrides_fit_and_blends_safety():
    lid = IDS[0]
    base = T.criterion_scores(lid, PROFILE)
    s = T.criterion_scores(lid, PROFILE, {"safety": 100, "fit": 7, "verdict": "pass"})
    assert s["lifestyle"] == 7
    assert s["safety"] == round(0.5 * base["safety"] + 50)


def test_live_police_safety_replaces_baseline():
    oak = next(r for r in T.LISTINGS if r["neighborhood"] == "Oakwood")
    n = T.neighborhood_report(oak["id"])
    assert n["safety_index"] == 41 and n["safety_source"].startswith("raleigh_pd")
    other = next(r for r in T.LISTINGS if r["neighborhood"] == "Garner")
    assert T.neighborhood_report(other["id"])["safety_source"] == "baseline"


def test_full_matrix_is_sub_50ms():
    t0 = time.perf_counter()
    T.score_matrix(IDS, PROFILE, EVEN)
    assert (time.perf_counter() - t0) < 0.05


def test_search_respects_budget_and_beds():
    for r in T.search_listings(max_price=400_000, min_beds=3):
        assert r["price"] <= 400_000 * 1.05 and r["beds"] >= 3
