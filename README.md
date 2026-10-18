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


## 🧮 How each sub-score is calculated

All scores are 0–100 (higher is better), computed in `app/metrics.py`.

| Score | Formula | Buyer controls |
|---|---|---|
| Lifestyle | 60% share of must-haves present + 40% amenity richness (features / 7, capped at 1). Replaced by Jev's 0–4 fit judgement (×25) when Jev is on. | must-have features |
| Commute | 100 − 2.5 × (minutes − 10). Minutes = 6 + 1.9 × straight-line miles to the destination (a rough Triangle drive-time estimate, not live traffic). Halved if over the max commute. | destination, max minutes |
| Environment | 100 − flood penalty (40 for FEMA zones A/AE, 45 for V/VE, 15 for the 500-year floodplain) − 0.7 × (30-day average AQI − 30) − min(20, 0.5 × heat days ≥35 °C + 2 × heavy-rain days ≥50 mm + 1 × wind days ≥75 km/h, over the last 12 months). Scored at the neighborhood centre. | weight |
| Maintenance | 100 − min(50, 0.9 × age in years) − min(30, HOA ÷ 10), and −20 more if the HOA is above the buyer's cap. | HOA cap |
| Rooms | 60 + 15 × (beds − beds wanted) + (sqft − min sqft) ÷ 40 | beds, min sqft |

## 🐳 Docker

| Piece | What it is |
|---|---|
| `Dockerfile` | 193 MB Python 3.12 image for the app and the pipeline (non-root, health check). A `test` stage runs lint + the full test suite inside the image. |
| `docker-compose.yml` | `vllm` (official `vllm/vllm-openai:v0.31.0` image, GPU, Nemotron 3.5 Lightning, reuses your Hugging Face cache) · `app` · `app-hostnet` · `pipeline` (one-shot ingestion job) |
| CI | builds both stages, smoke-tests the container and validates the compose file on every push |

```bash
docker compose --profile gpu up -d                      # whole stack: vLLM + app (stop ./start_nemotron.sh first, they share the GPU)
docker compose --profile hostnet up -d app-hostnet      # app only, using the vLLM already running on the host (Brev)
docker compose run --rm pipeline                        # refresh live data (+ Databricks sync if configured)
```

Secrets (`DATABRICKS_*`, optional `TYPESAFE_API_KEY`, `HF_TOKEN`) are read from your shell or a local `.env` file, which is git- and docker-ignored.


## 🧱 Databricks ELT (bronze → silver → gold)

Deployed as a **Databricks Asset Bundle** (`databricks.yml`) that creates a 3-task serverless job, `estatepulse-elt`, scheduled daily. The job reuses the repo's own Python, so the Databricks tables and the app compute identical numbers. The logic lives in `pipeline/medallion.py` and is unit-tested in CI.

| Layer | Schema `workspace.estatepulse_*` | What's in it |
|---|---|---|
| **Bronze** (raw) | `fred_observations`, `police_incidents`, `cary_offense_counts`, `environment_raw`, `neighborhoods_raw`, `listings_raw` | Data exactly as received, stamped with `run_id`, `ingested_at` and `source_mode` (`live_api` / `snapshot` / `unavailable`) |
| **Silver** (calculations) | `listing_factors` + one view per factor (`factor_lifestyle` … `factor_maintenance`), `neighborhood_indexes` | One row per listing × factor: the score, the formula and the exact inputs used; neighborhood safety and environment indexes |
| **Gold** (Jev-ready) | `jev_ready_listings`, `pipeline_audit` | One row per listing: 10 scores, composite and rank, live-rate math, and the exact Jev `state` + `questions` payload |

Governance: table comments, tags (`layer`, `pii=none`, `consumer=jev`), a `CHECK (score BETWEEN 0 AND 100)` constraint, an audit table, and automatic Unity Catalog lineage between the layers.

Deploy once from the Brev terminal (create a personal access token in Databricks under **Settings → Developer → Access tokens**, and paste it only into your own terminal):

```bash
curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh   # Databricks CLI
export DATABRICKS_HOST=https://dbc-421bb698-9803.cloud.databricks.com
export DATABRICKS_TOKEN=<your token>
databricks bundle validate && databricks bundle deploy && databricks bundle run estatepulse_elt
```

## 🖥 Pages

| Page | Path | What it does |
|---|---|---|
| Find a home | `/` | Describe your dream home; 14 Nemotron agents build the decision matrix live. Every home links to its math. |
| Free value calculator | `/value` | Estimate a home's value (range + 12-month outlook), monthly cost at the live FRED rate, the asking-price check, and all 10 factor scores. No sign-up. |
| How we score | `/how` | Pick any listing and see each factor's formula, inputs and score, plus data freshness and the bronze → silver → gold flow. |
| Architecture | `/architecture` | Judging dashboard: live system status, weights, failure injection, NeMo Gym results, judging criteria. |


## ⚡ Jev on vLLM (fast decisions, no external API)

Jev's decision pattern runs on the same vLLM server as the agents (Nemotron 3.5 Lightning 30B-A3B, about 3B active parameters, well under the 33B limit). Each Jev question costs one forward pass:

1. The listing's state (live FRED math, live police safety text, buyer profile) plus one question with lettered options (A–E, or Yes/No) is sent to `/v1/chat/completions` with `max_tokens=1`, `structured_outputs: {"choice": [letters]}`, `logprobs` and `top_logprobs=20`, with thinking off.
2. vLLM constrains the single output token to the option letters. The app reads the log-probabilities, renormalizes them over the letters, applies `JEV_TEMPERATURE`, and returns a calibrated distribution: an expected 0–100 score, a choice plus confidence, or a yes/no probability.
3. Every home gets 3 questions (safety, fit, verdict), all fired in parallel. Live on the L40S: 5 homes × 3 questions in about 0.4 s.

The app also exposes this as a Jev-compatible endpoint, so other tools can call it:

```bash
curl -s localhost:8501/v1/systemone -H 'content-type: application/json' -d '{
  "state": "3-bed in Cary, $480k, housing ratio 27%, 12 incidents per 1k residents in 90 days",
  "questions": {"safety": {"type": "score", "instructions": "How safe is this area for a family?", "criteria": ["very unsafe","below average","average","safe","very safe"]},
                "tour":   {"type": "noul",  "instructions": "Should the buyer tour this home this week?"}}}'
```

| `JEV_BACKEND` | What runs |
|---|---|
| `vllm` (default) | The Jev pattern on your own Nemotron vLLM (`JEV_VLLM_BASE_URL`, defaults to `NEMOTRON_BASE_URL`) |
| `typesafe` | TypeSafe's hosted Jev (needs `TYPESAFE_API_KEY`; its weights are closed, so it can't be self-hosted) |
| `off` | Deterministic rule engine only |

If vLLM is unreachable, every decision falls back to the rule engine and the UI labels it "rules fallback". Check it with `env/bin/python scripts/jev_probe.py`.


## 🏗 Architecture v3: 6 Nemotron agents + Zillow + Home Match Toolkit

This section supersedes the earlier "10 custom agents" layout. The 10 scores still exist (they are what NeMo Gym evaluates and what the matrix ranks), but they are now owned by 6 agents, matching the architecture slide.

```
Data ingestion                         vLLM · NVIDIA Nemotron 3.5 Lightning (30B total / ~3B active)
  Questionnaire (Home Match Toolkit)     6 agents: parse data, delete duplicate / non-existent listings,
  FRED · Google Maps commute             write the first files for Databricks to ingest
  Zillow listings (parser)                         │
  Safety: police + weather                       Databricks ELT: BRONZE → SILVER → GOLD
                                                    │
                                        vLLM · JEB: fast quick-math decisions (Jev pattern) → matrix of homes
Docker microservices · UI · API integrations
```

| # | Agent | Questionnaire question that sets its weight | Scores it owns | Data |
|---|---|---|---|---|
| 1 | Zillow Listing Agent | Target neighborhoods / ZIP codes + must-haves | candidate set | Zillow saved-page parser, live feed (RentCast), sample fallback; dedupe + off-market removal |
| 2 | Safety & Environment Agent | How important is low crime rate and neighborhood stability? | safety, environment | Raleigh / Cary PD incidents (FBI NIBRS categories), FEMA flood, Open-Meteo |
| 3 | Commute & Location Agent | What is your daily work location and target max commute time? | commute | Google Maps Distance Matrix (`GOOGLE_MAPS_API_KEY`) or drive-time estimate |
| 4 | Budget & Investment Agent | Cash flow, appreciation, or below-market value? | deal, forecast (NeMo Gym eval + RL) | FRED rates + FHFA HPI, price vs neighborhood $/sqft |
| 5 | Lifestyle & Amenities Agent | Vibrant urban living or suburban space and schools? | urban, school, lifestyle | GreatSchools ratings from the Zillow listing, walkability, features |
| 6 | Property Condition Agent | Tolerance for renovation, HOA fees and age of home? | maintenance, rooms | Listing description + facts, year built, HOA, beds / baths / sqft |

Agents 2-6 run in parallel; each calls one tool (`get_<agent>_evidence`) that returns every score it owns for the shortlist. The JEB layer then asks Jev-style questions on the same vLLM, builds the matrix, and Nemotron writes the recommendation.

### Zillow parser

`pipeline/sources/zillow.py` reads Zillow search-results and home-detail pages and walks every embedded JSON blob (including the JSON-in-a-string caches), so it survives layout changes. `pipeline/listings.py` then cleans the data:

1. drops homes that are sold, pending, off-market or for rent
2. drops records missing price, beds, square feet, coordinates or address, or with impossible values
3. de-duplicates on normalized street + ZIP; the richer record (detail page over search card) wins
4. maps each home to the nearest Triangle neighborhood profile (homes more than 8 miles from any profile are dropped)
5. extracts features (fenced yard, home office, garage …) from the description and Zillow facts

```bash
# save Zillow pages from your browser into data/zillow/saved/, then:
env/bin/python -m pipeline.listings                       # -> data/zillow/listings.json + quality report
env/bin/python -m pipeline.listings --location "Cary, NC"  # also pull live listings (RENTCAST_API_KEY)
```

Or upload a saved page in the app ("Add Zillow listings"). `LISTINGS_SOURCE=zillow|sample|both` (default `both`) picks what the app serves.

**Why the app doesn't crawl Zillow:** Zillow's Terms of Use prohibit automated queries and scraping, and Zillow blocks bots with CAPTCHAs, so a server-side crawler would break mid-demo and put the project at legal risk. When a buyer types a location, the app gives them Zillow's own search link for it; they save the page and we parse it. Live listings by location come from a licensed feed: RentCast today (MLS-sourced, free plan of 50 requests a month, cached 24 hours per area).

### Home Match Toolkit (`/start`)

A questionnaire that comes before any search:

1. **Financing status**: pre-approved (upload letter), paying cash (upload proof of funds), or needs a lender recommendation
2. **Verified purchasing power**: max purchase price, desired max monthly payment
3. **Must-haves**: min bedrooms, min bathrooms, target neighborhoods / ZIP codes
4. **What matters most**: the 5 priority questions above, each 0-5, plus work location, max commute, investment goal, urban vs suburban, renovation tolerance and max HOA

`app/intake.py` turns the answers into the buyer profile and all 10 weights (for example, "appreciation" weights forecast over deal; "suburban" weights schools over urban). A payment cap implies income at the standard 28% housing ratio.

**Documents and client follow-up**

- Uploads are checked (PDF / PNG / JPEG only, 10 MB max) and stored in the Unity Catalog volume `/Volumes/<catalog>/estatepulse_secure/client_documents/<intake id>/`, created by the bundle.
- One row per client goes to `estatepulse_secure.client_intake`, tagged `pii=client_contact`. Document contents are never copied into tables, logs or the client's record.
- Without Databricks, documents fall back to a private local folder (`data/intake/`, owner-only permissions, git- and docker-ignored).
- After the kickoff meeting, an advisor marks it done: `POST /api/admin/intake/<id>/kickoff` with header `X-Admin-Token: $ADMIN_TOKEN`.
- `scripts/send_portal_invites.py --send` then emails each client a signed sign-in link to their portal. The link expires in 7 days (`PORTAL_SECRET` signs it).
- No password is ever created or emailed. Run the script on a schedule (cron, GitHub Actions, or `docker compose --profile jobs run --rm invites --send`).

New settings: `RENTCAST_API_KEY`, `GOOGLE_MAPS_API_KEY`, `LISTINGS_SOURCE`, `ADMIN_TOKEN`, `PORTAL_SECRET`, `PORTAL_BASE_URL`, `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM`.

## 🛣 Data roadmap

| Stage | Listing data |
|---|---|
| **Now (hackathon)** | Zillow parser for pages buyers save, a licensed live feed by location (RentCast free tier), 61 synthetic sample homes as a fallback |
| **As we make profit** | Buy API credits from a large listing data hub covering **all open homes** in our markets: MLS access through a RESO Web API / IDX feed, Zillow's Bridge API, or a commercial aggregator. The Zillow Listing Agent already has a provider slot for it, so this is a configuration change rather than a rewrite. |
| **Scale** | Nightly full-market sync into Databricks bronze, price-history and days-on-market signals in silver, county tax and permit records feeding the maintenance and deal scores |

## ⚖️ Responsible use

Scores are decision support, not financial advice. Neighborhood data describes places, never people. The buyer sets how much safety and schools count, and every score shows its source and formula (`/how`), to stay clear of steering under the Fair Housing Act.


## 🔐 New UI + role-based access (RBAC)

The Estate Pulse site is now the front door (`/`): landing page, sign-in, the **Realtor workspace** (12-step process) and the **Client portal** (6-month plan). It is wired to the EstatePulse AI features:

- **Realtors:** Step 03 sends the Home Match Toolkit and shows submissions, with "Mark kickoff done". They also get a "Home Match intakes" view, the AI decision matrix, how-we-score and the value calculator.
- **Clients:** see their Home Match profile, showing status, matched homes and portal.

One RBAC catalog, `app/rbac.py`, defines the three groups for both the app and Unity Catalog:

| Role | Databricks group | Lands on | Can |
|---|---|---|---|
| **admin** (tester) | `estatepulse_admins` | `/app`, the original tester UI (matrix, value, how we score, architecture), plus both workspaces via `/?view=realtor` and `/?view=client` | everything, incl. diagnostics, failure injection, NeMo Gym, raw Jev endpoint |
| **realtor** | `estatepulse_realtors` | Realtor workspace | run matches, import Zillow pages, see client intakes (status only), mark kickoff meetings done |
| **client** | `estatepulse_clients` | Client portal | their own questionnaire, matches and portal |

**How sign-in works**

- Accounts are checked on the server. Passwords live only in environment variables and are hashed with PBKDF2 in memory; none are in the page.
- Set `ADMIN_PASSWORD`, `REALTOR_PASSWORD` and `CLIENT_PASSWORD`. Emails default to `admin@`, `realtor@` and `client@estatepulse.com`. For more people, use `EP_USERS='[{"email":"…","role":"realtor","password":"…"}]'`.
- An account with no password is disabled.
- Sessions use a signed HttpOnly cookie (`SESSION_SECRET`, 12 hours), and sign-in locks out after 5 failed attempts.
- A client can also sign in with the signed link from their Home Match email.
- Every API checks the role. `/start`, `/value` and the landing page stay public.

**Unity Catalog**

- `python scripts/databricks_groups.py` creates the three groups.
- The ELT job's new `rbac` task (`databricks/04_rbac_grants.py`) applies the grants:
  - **Admins:** all privileges on bronze, silver, gold and secure.
  - **Realtors:** read silver and gold, plus read-only access to `client_intake` and the documents volume.
  - **Clients:** only `gold.jev_ready_listings`, plus their own `client_intake` row through a row filter on `current_user()`.
- `python -m app.rbac --sql` prints the SQL.
