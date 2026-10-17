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

Ranking is a deterministic weighted decision matrix (`app/tools.py::score_matrix`). Every agent has a fallback.

## Layout
- `app/agents.py` agent loop + orchestrator
- `app/tools.py` tools + decision matrix
- `app/server.py` FastAPI, streams agent events as NDJSON
- `app/static/index.html` decision-matrix UI
- `data/` synthetic NC Triangle sample data (`make_data.py` regenerates it)
