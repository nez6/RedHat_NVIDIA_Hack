# RedHat_NAVIDIA_Hack
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

Installation & Execution

Clone the repository:

Bash




git clone [https://github.com/your-username/estatepulse-ai.git](https://github.com/your-username/estatepulse-ai.git)
cd estatepulse-ai
Set up a virtual environment:

Bash




python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
Install dependencies:

Bash




pip install -r requirements.txt
Start the vLLM Server (Brev Instance):

Bash




python -m venv_serving --model nvidia/Nemotron-3.5-Lightning --port 8000
Run the Application:

Bash




streamlit run app.py
License

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
| Environment / Weather Score | Climate, hazard and weather-pattern data | Flood risk, climate trends, air quality, local weather severity. | Customizable weight | ⏳ |
| Urban Score | Geographic proximity analysis | How close the property is to the city center or key commercial hubs, based on user input. | Desired target area | ⏳ (commute distance covers part of this) |
| Commute Score | Distance & transit route calculations | Travel time and distance to work, transit stops or key personal points of interest. | Max commute time & locations | 🟡 distance-based drive-time estimate to 9 Triangle destinations |
| Value / Forecasting Score | NeMo Gym-tracked variables & macroeconomic data | Macro trends; compares historic actual closing prices and value over time against estimates to auto-optimize accuracy. | Risk tolerance | 🟡 price vs. neighborhood median; FRED house-price index ingested; feedback loop ⏳ |
| Deal Calculator Score | NeMo Gym-tracked variables & financial engine | Self-improving algorithm tracking market pricing vs. valuation models over time to score deal quality. | Expected return targets | 🟡 PITI + HOA at the live FRED 30-yr rate, housing-ratio check; self-improvement ⏳ |
| Rooms / Layout Score | Floorplan analysis & property specs | Usable interior space, room count, layout efficiency; links into investment viability. | Min room / sqft requirements | 🟡 beds + sqft "space" score; floorplan analysis ⏳ |
| Maintenance Score | Property history & public records | Property age, recent system/structural replacements, presence and cost of an HOA. | HOA tolerance & age limits | 🟡 HOA included in monthly cost; age/systems scoring ⏳ |

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
| Environment / Weather Score | Climate-risk & hazard modeling | Insurance costs, environmental risk, long-term weather durability. | Adjustable weather weight | ⏳ |
| Urban Score | City proximity & density metrics | Proximity to employment hubs, commercial centers and downtowns defined by user input. | Target location / region | ⏳ |
| Commute Score | Transit & job accessibility | Travel distance to employment centers, transit nodes and highways. | Radius / travel-time caps | 🟡 shares the homebuyer commute estimate |

## 🛠 Features & Capabilities

- **Automated property ingestion:** pulls from MLS, Zillow listings, public records and crime databases. *Live today:* FRED macro series and Raleigh/Cary police feeds via `python -m pipeline.run`, validated against data contracts. MLS/Zillow/public-records connectors are ⏳ (Zillow use must respect its terms of service).
- **Self-improving forecasting:** integrates NeMo Gym tracking to compare predicted valuations with actual historical closing prices, continuously updating accuracy through macroeconomic feedback loops. ⏳
- **Dynamic user controls:** users tune weights for safety, commute, lifestyle, value and more; the matrix re-ranks instantly on the server without an LLM call. ✅

## Architecture & judging dashboard

Open **/architecture** on the running app (e.g. `https://dreamhome-sfdndu5im.gobrev.dev/architecture`). Every number on it comes from a real call:

1. **Ingestion pipeline**: last run status, record counts and the live 30-yr rate from `data/live/manifest.json`.
2. **Contracts & snapshots**: validated, atomically written snapshots with last-good fallback (Databricks / Unity Catalog lineage ⏳).
3. **Jev decision layer**: weight sliders call `/api/rescore` and show the measured round-trip latency.
4. **vLLM · Nemotron**: "Generate with Nemotron" calls `/api/narrate` and reports real latency and tokens/sec.
5. **Failure injection**: forces the police API offline and makes the Jev call time out (`/api/fallback-demo`), then shows the recovery path.
