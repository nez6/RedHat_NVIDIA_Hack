# DreamHome AI: a Nemotron agent crew for finding your dream home

Runs on a Brev L40S: **NVIDIA Nemotron 3.5 Lightning (30B-A3B)** on **vLLM** (FP8 at load), with tool calling.

## Quick start
```bash
./start_nemotron.sh   # vLLM + Nemotron on :8000 (first run downloads ~60GB)
./run_app.sh          # UI + agent API on :8501
```

## Agent crew (all Nemotron)
| Agent | Job | Tool |
|---|---|---|
| Concierge | Free text -> buyer profile + weights | set_buyer_profile |
| Listings Scout | Searches listings, relaxes filters | search_listings |
| Neighborhood Analyst | Safety, schools, walkability, commute | neighborhood_report |
| Finance Advisor | PITI + HOA, housing ratio, price vs area | affordability |
| Matchmaker | Explains the top 3 | - |

Ranking is a weighted decision matrix over 8 criteria (`app/tools.py::score_matrix`); a Jev Decision Layer runs between the analysts and the Matchmaker. Every step has a fallback.

## Layout
- `app/agents.py` agent loop + orchestrator
- `app/tools.py` tools + decision matrix
- `app/server.py` FastAPI, streams agent events as NDJSON
- `app/static/index.html` decision-matrix UI
- `pipeline/` live data ingestion (FRED + police)
- `app/jev.py` Jev decision layer, `app/live.py` snapshot loader
- `data/` sample listings (`make_data.py`) and `data/live/` snapshots
- `tests/` pytest suite, `.github/workflows/` CI + scheduled data refresh

## Live data pipeline (v1.1)
`python -m pipeline.run [--strict]` fetches, validates, transforms and atomically writes `data/live/*.json` (the app hot-reloads them).

| Source | What | Used for |
|---|---|---|
| FRED MORTGAGE30US / 15US | weekly mortgage rates | monthly payment + affordability math |
| FRED ATNHPIUS39580Q / 20500Q | FHFA house price index, Raleigh & Durham MSAs | market context |
| FRED RALE537URN | Raleigh MSA unemployment | market context |
| Raleigh PD NIBRS incidents (ArcGIS) | last 90 days, geocoded | per-neighborhood safety (1-mile radius, severity-weighted) |
| Cary PD incidents (Opendatasoft) | 12-month window, scaled to 90 days | town-wide safety for Cary |

A failing source keeps its last good snapshot; `manifest.json` records status, warnings and timings.

## Jev decision layer
Math (payment, affordability, value, commute) stays deterministic. Jev (TypeSafe AI) makes the functional calls per home in parallel:
safety (Score over the live incident mix), lifestyle fit (Score) and a verdict (Choice: tour_now / consider / pass).
Set `TYPESAFE_API_KEY` to enable; without it a rule-based fallback with the same output shape runs.

## Tests and CI/CD
- `pytest` runs 30 hermetic tests (finance math, matrix invariants, pipeline contracts, Jev parsing/fallback, end-to-end API with Nemotron down).
- `pytest -m live` runs smoke tests against the real FRED and police APIs.
- `.github/workflows/ci.yml`: ruff + tests on Python 3.11/3.12 + validation of committed snapshots on every push/PR.
- `.github/workflows/data-refresh.yml`: daily live smoke tests, strict ingest, regression tests, then commits the new snapshot.
