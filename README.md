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
