# RedHat_NAVIDIA_Hack_a_Thon_Track_3_Tool-Calling

# EstatePulse AI

**Sub-10-Second Real Estate Matching Engine Powered by Small Models, Open Data, and Enterprise Infrastructure**

EstatePulse AI is an agentic real estate matching engine built to eliminate the multi-tab headache of home buying. By pairing open real estate datasets with dynamic neighborhood safety logs, EstatePulse AI scores and ranks candidate properties in milliseconds based on user preference weightings.

*Built during the NVIDIA x Red Hat Small Models Hack (Raleigh, NC | Oct 17–18, 2026).*

---

## Key Features

* **Multi-Criteria Weighting:** Users customize preference sliders (Budget, Safety, Location) to dynamically recalculate fit scores.
* **Open Data Integration:** Blends open county GIS real estate records with live local police department blotter logs.
* **Tool-Calling Architecture (Track 3):** Small models reliably call local database endpoints, web scrapers, and fallback handlers with graceful error recovery.
* **Sub-100ms Inference:** Powered by Red Hat vLLM on NVIDIA Brev GPU instances for low-latency agent routing and narrative generation.
* **Interactive Telemetry UI:** Live system logs show vLLM inference times, tool-execution states, and deterministic scoring in real time.

---

## Architecture

```text
[ User Inputs: Weights & Target Cities ]
                    │
                    ▼
       [ Streamlit / FastAPI UI ]
                    │
                    ▼
  [ Tool-Calling Agent: Small Model on Red Hat vLLM ]
                    │
    ┌───────────────┼───────────────┐
    ▼               ▼               ▼
[ Tool 1 ]      [ Tool 2 ]      [ Tool 3 ]
 DuckDB        Live Police     Fallback Cached
Housing DB       Scraper        Safety Matrix
    │               │               │
    └───────────────┼───────────────┘
                    ▼
       [ Jev / JEB Decision Layer ]
  (Multi-Criteria Scoring Engine)
                    │
                    ▼
     [ Ranked Properties + Maps ]
Enterprise Tech Stack

Layer	Technology	Purpose
Model Serving	Red Hat vLLM	High-throughput, low-latency local model execution
Model Brain	Nemotron / Small Model (≤33B)	Agent routing, tool parameter formatting, and narrative synthesis
Compute	NVIDIA Brev	Accelerated GPU instance execution
Data Warehouse	DuckDB / Open Data	In-memory relational queries over open housing datasets
Decision Engine	Jev / JEB	Deterministic multi-criteria scoring & reranking
Frontend	Streamlit	Interactive UI with real-time system logs and Google Maps embeds


Main Challenge Track & Bounties

Main Challenge Track: Track 3 — Tool-Calling (Demonstrating reliable tool execution, parameter parsing, and fallback recovery).

Bounties Targeted:

Best Use of Red Hat vLLM: Core model served via vLLM for high-throughput, low-latency inference.

Best Use of NVIDIA Nemotron: Utilizing Nemotron for agent routing and tool execution.

Open Data Sources

Housing Records: Open County GIS & Property Assessment Data.

Safety Context: Public Police Department Crime Blotters & Local Incident Reports.

Getting Started

Prerequisites

Python 3.10+

Docker / NVIDIA Container Toolkit (for vLLM)

NVIDIA GPU Instance (via Brev)

### Installation & Execution

1. Clone the repository:

   ```bash
   git clone https://github.com/nez6/RedHat_NVIDIA_Hack.git
   cd RedHat_NVIDIA_Hack
   ```

2. Set up a virtual environment:

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Start the vLLM server with Nemotron 3.5 Lightning (Brev instance, FP8 on a 48 GB GPU):

   ```bash
   ./start_nemotron.sh        # wraps: vllm serve nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16 --quantization fp8 ...
   ```

5. Run the application (FastAPI + web UI on port 8501):

   ```bash
   ./run_app.sh
   ```

   Or run the whole stack in containers: `docker compose up -d` (see **Docker** below).

## License

Distributed under the MIT License. Feel free to modify and build upon this project.

---

# Real Estate Investment & Scoring Platform

An advanced real estate evaluation platform that processes property data, analyzes listings, and provides customized scoring models for both home buyers and real estate investors.

**Status key:** ✅ live in this repo · 🟡 partial (simplified version live) · ⏳ planned

## 🎯 Scoring Matrices & Metrics

The platform uses two scoring matrices tailored to different buyer profiles. Each matrix combines a set of scoring factors to give a deep view of every property.

### 1. Homebuyer Scoring Matrix

Focuses on personal fit, convenience, safety, and long-term livability.

| Metric | Source & Calculation | Key Parameters / Logic | User Controls | Status |
|---|---|---|---|---|
| Lifestyle Score | Zillow listing images vs. text description analysis | Evaluates qualitative features, aesthetics and amenities. Highest-variance score. | Preference weights | 🟡 Jev "fit" score from listing text + buyer description; image analysis ⏳ |
| School Score | Direct extraction from property listing | Pulls the rating directly from listing details; raw data, no normalization. | Threshold filters | 🟡 neighborhood rating from sample data; listing extraction ⏳ |
| Safety Score | Local police records & crime-rate databases | Analyzes historical crime statistics and neighborhood safety records. | Customizable weight | ✅ live Raleigh PD (1-mile radius, 90 days) and Cary PD feeds, blended with a Jev safety judgement |
| Environment / Weather Score | Climate, hazard and weather-pattern data | Flood risk, climate trends, air quality, local weather severity. | Customizable weight | ✅ live FEMA flood zone + Open-Meteo 30-day AQI + 12-month heat/rain/wind extremes |
| Urban Score | Geographic proximity analysis | How close the property is to the city center or key commercial hubs, based on user input. | Desired target area | ✅ distance to the buyer's chosen hub (70%) + walk score (30%) |
| Commute Score | Distance & transit route calculations | Travel time and distance to work, transit stops or key personal points of interest. | Max commute time & locations | 🟡 distance-based drive-time estimate to 9 Triangle destinations |
| Value / Forecasting Score | NeMo Gym-tracked variables & macroeconomic data | Macro trends; compares historic actual closing prices and value over time against estimates to auto-optimize accuracy. | Risk tolerance | 🟡 live FRED FHFA HPI momentum + mean reversion to neighborhood price/sqft; NeMo Gym RL rollouts collected; realized-price feedback ⏳ |
| Deal Calculator Score | NeMo Gym-tracked variables & financial engine | Self-improving algorithm tracking market pricing vs. valuation models over time to score deal quality. | Expected return targets | 🟡 PITI + HOA at the live FRED 30-yr rate, 28% housing-ratio check, discount to market; NeMo Gym RL rollouts collected |
| Rooms / Layout Score | Floorplan analysis & property specs | Usable interior space, room count, layout efficiency; links into investment viability. | Min room / sqft requirements | 🟡 beds + sqft "space" score; floorplan analysis ⏳ |
| Maintenance Score | Property history & public records | Property age, recent system/structural replacements, presence and cost of an HOA. | HOA tolerance & age limits | ✅ property age + HOA, extra penalty above the buyer's HOA cap (system-level history ⏳) |

### 2. Investor Scoring Matrix

Focuses on financial returns, risk mitigation, asset longevity and growth potential.

| Metric | Source & Calculation | Key Parameters / Logic | User Controls | Status |
|---|---|---|---|---|
| Value / Forecasting Score | NeMo Gym macroeconomic engine | **Critical core metric.** Evaluates macro trends, comparing actual closing prices and appreciation against model forecasts to refine future predictions. | Growth model weights | ⏳ (FRED HPI + unemployment already ingested) |
| Deal Calculator Score | NeMo Gym investment tracking | **Critical core metric.** Purchase terms, entry cap rate, cash-on-cash return, discount to true market value. Auto-improves via historic closing comparisons. | Target ROI / IRR | ⏳ |
| Maintenance Score | Capital expenditure (CapEx) modeling | Property age, remaining life of major systems (roof, HVAC, plumbing), HOA fees/restrictions affecting cash flow. | CapEx reserve thresholds | ⏳ |
| Rooms / Layout Score | Yield & rentable-space analysis | Spatial efficiency, tenant suitability, multi-unit or room-by-room rental potential. | Unit configuration preferences | ⏳ |
| Lifestyle Score | Listing description & visual match | High variability; tenant appeal, neighborhood amenities, aesthetic desirability for rental positioning. | Weight in tenant appeal | ⏳ |
| School Score | Direct listing pull | Unnormalized school rating from listing data; gauges tenant demand for family rentals. | Minimum score filter | ⏳ |
| Safety Score | Crime rates & municipal records | Neighborhood stability, crime trends, insurance liability factors. | Adjustable safety weight | ✅ shares the live police pipeline |
| Environment / Weather Score | Climate-risk & hazard modeling | Insurance costs, environmental risk, long-term weather durability. | Adjustable weather weight | 🟡 shares the live environment pipeline |
| Urban Score | City proximity & density metrics | Proximity to employment hubs, commercial centers and downtowns defined by user input. | Target location / region | 🟡 shares the homebuyer urban score |
| Commute Score | Transit & job accessibility | Travel distance to employment centers, transit nodes and highways. | Radius / travel-time caps | 🟡 shares the homebuyer commute estimate |

## 🛠 Features & Capabilities

- **Automated property ingestion:** pulls from MLS, Zillow listings, public records and crime databases. *Live today:* FRED macro series and Raleigh/Cary police feeds via `python -m pipeline.run`, validated against data contracts. MLS/Zillow/public-records connectors are ⏳ (Zillow use must respect its terms of service).
- **Self-improving forecasting:** integrates NeMo Gym tracking to compare predicted valuations with actual historical closing prices, continuously updating accuracy through macroeconomic feedback loops. 🟡 NeMo Gym environment + RL rollouts for the Forecast and Deal agents are live; swapping targets to realized closing prices is next.
- **Dynamic user controls:** users tune weights for safety, commute, lifestyle, value and more; the matrix re-ranks instantly on the server without an LLM call. ✅

## Architecture & judging dashboard

Open **/architecture** on the running app (e.g. `https://dreamhome-sfdndu5im.gobrev.dev/architecture`). Every number on it comes from a real call:

1. **Ingestion pipeline**: last run status, record counts and the live 30-yr rate from `data/live/manifest.json`.
2. **Databricks Unity Catalog**: validated snapshots synced to bronze → silver → gold tables with tags, CHECK constraints, lineage and audit tables.
3. **Jev decision layer**: weight sliders call `/api/rescore` and show the measured round-trip latency.
4. **vLLM · Nemotron**: "Generate with Nemotron" calls `/api/narrate` and reports real latency and tokens/sec.
5. **Failure injection**: forces the police API offline and makes the Jev call time out (`/api/fallback-demo`), then shows the recovery path.

## 🤖 The 10 metric agents

One Nemotron tool-calling agent per homebuyer metric (`app/metric_agents.py`). Each owns one tool, one deterministic
scorer (`app/metrics.py`) and one weight; all 10 run in parallel after the Listings Scout. If an agent errors or skips
its tool, the tool runs directly so the matrix is always complete. `GET /api/agents` returns the registry and a
configuration check (tool, scorer, weight, prompt, NeMo Gym mode) that CI also enforces.

| Agent | Tool | Data | NeMo Gym |
|---|---|---|---|
| Lifestyle | get_lifestyle_inputs | listing features + Jev fit | eval |
| School | get_school_inputs | neighborhood rating (raw) | eval |
| Safety | get_safety_inputs | live Raleigh/Cary PD + Jev | eval |
| Environment & Weather | get_environment_inputs | FEMA NFHL, Open-Meteo AQI + archive | eval |
| Urban | get_urban_inputs | distance to target hub + walk score | eval |
| Commute | get_commute_inputs | drive-time estimate | eval |
| Value & Forecast | get_forecast_inputs | FRED FHFA HPI + price vs median | eval + RL |
| Deal Calculator | get_deal_inputs | live FRED rate, PITI + HOA, discount | eval + RL |
| Rooms & Layout | get_rooms_inputs | beds, baths, sqft | eval |
| Maintenance | get_maintenance_inputs | year built + HOA | eval |

## 🗄 Data governance (Databricks Unity Catalog)

`python -m pipeline.run` validates every source against data contracts, then (when `DATABRICKS_*` is set) syncs to Unity Catalog:

- **Bronze** `estatepulse_bronze`: raw FRED observations, per-neighborhood police/environment snapshots, listings — every row stamped with `run_id` and `ingested_at`.
- **Silver** `estatepulse_silver`: `neighborhood_risk` and `macro_latest`, built with SQL from bronze so Unity Catalog records table and column lineage.
- **Gold** `estatepulse_gold`: `listing_scores`, `listing_scorecard` (view), `pipeline_audit` (who ran what, contract results) and `agent_decisions` (every AI recommendation: model, weights, verdicts).
- Table/column comments, tags (`project`, `domain`, `layer`, `pii=none`) and Delta CHECK constraints on every score.
- No incident-level or personal data leaves the pipeline: police data is aggregated to neighborhood counts before storage.

Setup (paste your own values in the terminal; never commit them):

```bash
export DATABRICKS_HOST=https://<workspace>.cloud.databricks.com
export DATABRICKS_TOKEN=<personal access token>
export DATABRICKS_WAREHOUSE_ID=<sql warehouse id>
export DATABRICKS_CATALOG=workspace            # or your catalog
python -m pipeline.databricks_sink --check       # verify access
python -m pipeline.run                           # ingest + sync
```

## 🧪 NeMo Gym evaluation & RL rollouts

`resources_servers/estatepulse` is a NeMo Gym resources server exposing the same 10 tools. `scripts/gym_eval.sh` builds
datasets from the live scorers, starts the environment against the local vLLM Nemotron server, runs the **8 eval harnesses**
(Lifestyle, School, Safety, Environment, Urban, Commute, Rooms, Maintenance) and collects **eval + RL rollouts** (4 samples per
task) for the **Value/Forecast** and **Deal Calculator** agents. Results land in `results/gym/` and on the dashboard.

## 🏆 Judging criteria & evidence

| Criterion | What we show | Where |
|---|---|---|
| Tool calling (Track 3) | 14 Nemotron agents; 10 specialist agents each with its own tool and a CI-enforced registry check | `app/metric_agents.py`, `/api/agents` |
| Nemotron + vLLM | Nemotron 3.5 Lightning (30B total / ~3B active, FP8) on vLLM with tool + reasoning parsers | `start_nemotron.sh` |
| **Data governance** | Data contracts with last-good fallback; Unity Catalog medallion with lineage, tags, CHECK constraints, pipeline and AI-decision audit tables; no PII; provenance shown next to every score | `pipeline/`, `pipeline/databricks_sink.py` |
| **Design** | Decision-matrix heatmap, live per-agent telemetry, verdict badges, light/dark themes, mobile layout, plain-English explanation of every score | `/`, `/architecture` |
| Evaluation | NeMo Gym: 8 eval harnesses + RL rollouts for forecasting and deal finding | `resources_servers/estatepulse`, `results/gym` |
| Reliability | Hermetic test suite + live smoke tests in GitHub Actions, failure-injection demo | `tests/`, `.github/workflows/` |
| Real data | FRED, Raleigh PD, Cary PD, FEMA NFHL, Open-Meteo (listings are still sample data) | `data/live/` |

