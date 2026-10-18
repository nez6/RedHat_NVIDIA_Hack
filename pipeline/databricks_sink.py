"""Databricks Unity Catalog sink: medallion tables + governance metadata + audit trail.

    python -m pipeline.databricks_sink            # sync data/live snapshots + listings to Unity Catalog
    python -m pipeline.databricks_sink --dry-run  # print every SQL statement instead of executing
    python -m pipeline.databricks_sink --check    # verify credentials + warehouse + catalog access

Env: DATABRICKS_HOST, DATABRICKS_TOKEN, DATABRICKS_WAREHOUSE_ID, optional DATABRICKS_CATALOG (default "workspace").
Uses the SQL Statement Execution REST API (stdlib only). Silver/gold tables are built with SQL from bronze
tables, so Unity Catalog records column-level lineage automatically.
"""
import argparse, datetime as dt, json, os, sys, time, urllib.request, uuid

from .config import DATA_DIR, LIVE_DIR

SCHEMAS = {"bronze": "estatepulse_bronze", "silver": "estatepulse_silver", "gold": "estatepulse_gold"}
TAGS = {"project": "estatepulse", "domain": "real_estate", "pii": "none"}


def configured():
    return all(os.getenv(k) for k in ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_WAREHOUSE_ID"))


def catalog():
    return os.getenv("DATABRICKS_CATALOG", "workspace")


def q(name):
    return chr(96) + name + chr(96)  # backtick-quoted identifier


def fq(layer, table):
    return f"{q(catalog())}.{q(SCHEMAS[layer])}.{q(table)}"


class Client:
    """Minimal SQL Statement Execution API client. transport(method, path, body) is injectable for tests."""

    def __init__(self, transport=None, dry_run=False):
        self.dry_run = dry_run
        self.log = []
        self.transport = transport or self._http

    def _http(self, method, path, body=None):
        host = os.environ["DATABRICKS_HOST"].rstrip("/")
        if not host.startswith("http"):
            host = "https://" + host
        req = urllib.request.Request(host + path, method=method, data=json.dumps(body).encode() if body else None,
                                     headers={"Authorization": f"Bearer {os.environ['DATABRICKS_TOKEN']}",
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode() or "{}")

    def sql(self, statement, params=None, ignore=()):
        self.log.append(statement)
        if self.dry_run:
            return {"status": {"state": "DRY_RUN"}}
        body = {"warehouse_id": os.getenv("DATABRICKS_WAREHOUSE_ID", "dry"), "statement": statement, "wait_timeout": "30s",
                "on_wait_timeout": "CONTINUE"}
        if params:
            body["parameters"] = [{"name": k, "value": v if isinstance(v, str) else json.dumps(v), "type": "STRING"}
                                  for k, v in params.items()]
        r = self.transport("POST", "/api/2.0/sql/statements", body)
        while r.get("status", {}).get("state") in ("PENDING", "RUNNING"):
            time.sleep(1)
            r = self.transport("GET", f"/api/2.0/sql/statements/{r['statement_id']}", None)
        state = r.get("status", {}).get("state")
        if state != "SUCCEEDED":
            msg = (r.get("status", {}).get("error") or {}).get("message", str(r)[:300])
            if any(s in msg for s in ignore):
                return r
            raise RuntimeError(f"Databricks SQL {state}: {msg[:300]} :: {statement[:120]}")
        return r


def rows(c, statement, params=None):
    """Run a query and return [{column: value}] (inline JSON result, follows extra chunks)."""
    r = c.sql(statement, params)
    cols = [x["name"] for x in ((r.get("manifest") or {}).get("schema") or {}).get("columns") or []]
    data = list((r.get("result") or {}).get("data_array") or [])
    nxt = (r.get("result") or {}).get("next_chunk_internal_link")
    while nxt and not c.dry_run:
        chunk = c.transport("GET", nxt, None)
        data += chunk.get("data_array") or []
        nxt = chunk.get("next_chunk_internal_link")
    return [dict(zip(cols, d)) for d in data]


def _load(name):
    with open(os.path.join(LIVE_DIR, name)) as f:
        return json.load(f)


def ddl(c):
    for layer, schema in SCHEMAS.items():
        c.sql(f"CREATE SCHEMA IF NOT EXISTS {q(catalog())}.{q(schema)} COMMENT 'EstatePulse AI {layer} layer (medallion)'")
    c.sql(f"""CREATE TABLE IF NOT EXISTS {fq('bronze', 'fred_observations')} (
        series_id STRING COMMENT 'FRED series id', name STRING COMMENT 'pipeline alias', obs_date DATE, value DOUBLE,
        run_id STRING COMMENT 'pipeline run that ingested the row', ingested_at TIMESTAMP)
        COMMENT 'Raw FRED observations (St. Louis Fed). Public data, no PII.'""")
    c.sql(f"""CREATE TABLE IF NOT EXISTS {fq('bronze', 'neighborhood_snapshots')} (
        neighborhood STRING, source STRING COMMENT 'police or environment', payload STRING COMMENT 'raw JSON snapshot',
        run_id STRING, ingested_at TIMESTAMP)
        COMMENT 'Raw per-neighborhood police (Raleigh/Cary PD) and environment (FEMA NFHL, Open-Meteo) snapshots. Aggregates only, no incident-level PII.'""")
    c.sql(f"""CREATE TABLE IF NOT EXISTS {fq('bronze', 'listings_raw')} (
        listing_id BIGINT, address STRING, neighborhood STRING, city STRING, price BIGINT, beds INT, baths DOUBLE, sqft INT,
        year_built INT, hoa_monthly INT, property_type STRING, features STRING, run_id STRING, ingested_at TIMESTAMP)
        COMMENT 'Listings as ingested. Current source: synthetic sample data (data/listings.csv).'""")
    c.sql(f"""CREATE TABLE IF NOT EXISTS {fq('gold', 'listing_scores')} (
        listing_id BIGINT, metric STRING COMMENT 'one of the 10 homebuyer-matrix metrics', score INT COMMENT '0-100',
        profile STRING COMMENT 'scoring profile name', run_id STRING, scored_at TIMESTAMP)
        COMMENT 'Deterministic sub-scores produced by the 10 metric agents scorers (app/metrics.py).'""")
    c.sql(f"""CREATE TABLE IF NOT EXISTS {fq('gold', 'pipeline_audit')} (
        run_id STRING, source STRING, status STRING, rows BIGINT, warnings STRING, actor STRING, git_sha STRING, logged_at TIMESTAMP)
        COMMENT 'Audit trail: every ingestion run, its data-contract result and who ran it.'""")
    c.sql(f"""CREATE TABLE IF NOT EXISTS {fq('gold', 'agent_decisions')} (
        decision_id STRING, run_id STRING, model STRING, jev_engine STRING, profile_summary STRING, weights STRING,
        top_listing_ids STRING, verdicts STRING, decided_at TIMESTAMP)
        COMMENT 'Audit trail of AI recommendations (which model, which weights, what was recommended).'""")
    c.sql(f"""CREATE TABLE IF NOT EXISTS {fq('gold', 'match_results')} (
        decision_id STRING COMMENT 'joins gold.agent_decisions', source STRING COMMENT 'app or eval', rank INT, listing_id BIGINT,
        address STRING, neighborhood STRING, price BIGINT, fit_score DOUBLE COMMENT 'weighted decision-matrix total 0-100',
        scores STRING COMMENT 'JSON: the 10 criterion scores', jev_verdict STRING, monthly_payment BIGINT, decided_at TIMESTAMP)
        COMMENT 'Every home Nemotron + the decision matrix ranked, per recommendation.'""")
    c.sql(f"""CREATE TABLE IF NOT EXISTS {fq('gold', 'agent_eval_runs')} (
        eval_id STRING, model STRING, nemotron_ok BOOLEAN, metric STRING COMMENT 'e.g. extraction.merged.precision', value DOUBLE,
        detail STRING, evaluated_at TIMESTAMP)
        COMMENT 'scripts/agent_eval.py results against live Nemotron: one row per metric per run (long format for charts).'""")
    c.sql(f"""CREATE TABLE IF NOT EXISTS {fq('gold', 'agent_eval_cases')} (
        eval_id STRING, suite STRING COMMENT 'extraction | empty_data | pipeline', prompt STRING, expected STRING, nemotron_output STRING,
        shipped_output STRING, nemotron_exact BOOLEAN, shipped_pass BOOLEAN, latency_ms BIGINT, evaluated_at TIMESTAMP)
        COMMENT 'Per-case agent eval results: what Nemotron returned vs what the shipped (guarded) pipeline used.'""")
    c.sql(f"""CREATE TABLE IF NOT EXISTS {fq('gold', 'architecture_validation')} (
        validation_id STRING, category STRING COMMENT 'workspace | parity | agents | completeness | relevance', check_name STRING,
        status STRING COMMENT 'pass | warn | fail', observed STRING, expected STRING, detail STRING, validated_at TIMESTAMP)
        COMMENT 'scripts/architecture_validate.py: Databricks data quality, gold-vs-local parity, agents on gold, evidence completeness.'""")


def governance(c):
    for t in [("bronze", "fred_observations"), ("bronze", "neighborhood_snapshots"), ("bronze", "listings_raw"),
              ("silver", "neighborhood_risk"), ("silver", "macro_latest"), ("gold", "listing_scores"),
              ("gold", "listing_scorecard"), ("gold", "pipeline_audit"), ("gold", "agent_decisions"), ("gold", "fred_series"),
              ("gold", "match_results"), ("gold", "agent_eval_runs"), ("gold", "agent_eval_cases"), ("gold", "jev_ready_neighborhoods"),
              ("gold", "jev_ready_macro"), ("gold", "jev_ready_listings"), ("gold", "architecture_validation")]:
        kind = "VIEW" if t[1] in ("listing_scorecard", "fred_series") else "TABLE"
        tags = ", ".join(f"'{k}' = '{v}'" for k, v in {**TAGS, "layer": t[0]}.items())
        c.sql(f"ALTER {kind} {fq(*t)} SET TAGS ({tags})", ignore=("not supported", "NOT_SUPPORTED", "PERMISSION"))
    for name, cond in [("safety_index_range", "safety_index IS NULL OR safety_index BETWEEN 0 AND 100"),
                       ("environment_index_range", "environment_index IS NULL OR environment_index BETWEEN 0 AND 100")]:
        c.sql(f"ALTER TABLE {fq('silver', 'neighborhood_risk')} ADD CONSTRAINT {name} CHECK ({cond})",
              ignore=("already exists", "ALREADY_EXISTS"))
    c.sql(f"ALTER TABLE {fq('gold', 'listing_scores')} ADD CONSTRAINT score_range CHECK (score BETWEEN 0 AND 100)",
          ignore=("already exists", "ALREADY_EXISTS"))


def silver_gold(c):
    """Built with SQL from bronze -> Unity Catalog captures table + column lineage."""
    c.sql(f"""CREATE OR REPLACE TABLE {fq('silver', 'neighborhood_risk')}
        COMMENT 'Latest validated safety + environment indexes per neighborhood (from bronze snapshots)' AS
        WITH latest AS (
          SELECT *, ROW_NUMBER() OVER (PARTITION BY neighborhood, source ORDER BY ingested_at DESC) AS rn
          FROM {fq('bronze', 'neighborhood_snapshots')})
        SELECT n.neighborhood,
               CAST(get_json_object(p.payload, '$.safety_index') AS INT) AS safety_index,
               CAST(get_json_object(p.payload, '$.incidents') AS INT) AS incidents_lookback,
               get_json_object(p.payload, '$.source') AS safety_source,
               CAST(get_json_object(e.payload, '$.environment_index') AS INT) AS environment_index,
               get_json_object(e.payload, '$.flood.zone') AS flood_zone,
               CAST(get_json_object(e.payload, '$.aqi_30d') AS DOUBLE) AS aqi_30d,
               GREATEST(p.ingested_at, e.ingested_at) AS as_of
        FROM (SELECT DISTINCT neighborhood FROM latest) n
        LEFT JOIN latest p ON p.neighborhood = n.neighborhood AND p.source = 'police' AND p.rn = 1
        LEFT JOIN latest e ON e.neighborhood = n.neighborhood AND e.source = 'environment' AND e.rn = 1""")
    c.sql(f"""CREATE OR REPLACE TABLE {fq('silver', 'macro_latest')}
        COMMENT 'Most recent value of each FRED series' AS
        SELECT name, series_id, obs_date, value FROM (
          SELECT *, ROW_NUMBER() OVER (PARTITION BY name ORDER BY obs_date DESC, ingested_at DESC) AS rn
          FROM {fq('bronze', 'fred_observations')}) WHERE rn = 1""")
    # ---- gold: what the live app, the 5 scoring agents and Jev read (DATA_SOURCE=databricks, app/gold.py)
    c.sql(f"""CREATE OR REPLACE TABLE {fq('gold', 'jev_ready_neighborhoods')}
        COMMENT 'One row per neighborhood: reference profile + latest police + environment snapshots (JSON kept lossless for the app)' AS
        WITH latest AS (
          SELECT *, ROW_NUMBER() OVER (PARTITION BY neighborhood, source ORDER BY ingested_at DESC) AS rn
          FROM {fq('bronze', 'neighborhood_snapshots')})
        SELECT p.neighborhood, get_json_object(p.payload, '$.city') AS city,
               CAST(get_json_object(p.payload, '$.lat') AS DOUBLE) AS lat, CAST(get_json_object(p.payload, '$.lon') AS DOUBLE) AS lon,
               CAST(get_json_object(p.payload, '$.school_rating') AS INT) AS school_rating,
               CAST(get_json_object(p.payload, '$.walk_score') AS INT) AS walk_score,
               CAST(get_json_object(p.payload, '$.median_ppsf') AS INT) AS median_ppsf,
               CAST(get_json_object(s.payload, '$.safety_index') AS INT) AS safety_index,
               CAST(get_json_object(e.payload, '$.environment_index') AS INT) AS environment_index,
               get_json_object(e.payload, '$.flood.zone') AS flood_zone,
               p.payload AS profile_json, s.payload AS safety_json, e.payload AS environment_json,
               GREATEST(p.ingested_at, coalesce(s.ingested_at, p.ingested_at), coalesce(e.ingested_at, p.ingested_at)) AS as_of,
               p.run_id AS run_id
        FROM latest p
        LEFT JOIN latest s ON s.neighborhood = p.neighborhood AND s.source = 'police' AND s.rn = 1
        LEFT JOIN latest e ON e.neighborhood = p.neighborhood AND e.source = 'environment' AND e.rn = 1
        WHERE p.source = 'profile' AND p.rn = 1""")
    c.sql(f"""CREATE OR REPLACE TABLE {fq('gold', 'jev_ready_macro')}
        COMMENT 'Latest value + year-over-year change per FRED series (same definition as the app: N observations back)' AS
        WITH f AS (
          SELECT name, series_id, obs_date, value,
                 CASE WHEN series_id LIKE 'MORTGAGE%' THEN LAG(value, 52) OVER (PARTITION BY series_id ORDER BY obs_date)
                      WHEN series_id LIKE 'ATNHPI%' THEN LAG(value, 4) OVER (PARTITION BY series_id ORDER BY obs_date)
                      ELSE LAG(value, 12) OVER (PARTITION BY series_id ORDER BY obs_date) END AS prev,
                 ROW_NUMBER() OVER (PARTITION BY series_id ORDER BY obs_date DESC) AS rn
          FROM (SELECT * FROM {fq('bronze', 'fred_observations')}
                QUALIFY ROW_NUMBER() OVER (PARTITION BY series_id, obs_date ORDER BY ingested_at DESC) = 1))
        SELECT name, series_id, obs_date, ROUND(value, 4) AS value,
               CASE WHEN prev IS NOT NULL AND prev <> 0 THEN ROUND((value / prev - 1) * 100, 2) END AS yoy_pct,
               current_timestamp() AS built_at
        FROM f WHERE rn = 1""")
    c.sql(f"""CREATE OR REPLACE TABLE {fq('gold', 'jev_ready_listings')}
        COMMENT 'Gold: one row per listing with neighborhood features, live mortgage rate, metro HPI trend and the 10 sub-scores. Jev / client-facing.' AS
        WITH s AS (SELECT * FROM {fq('gold', 'listing_scores')} WHERE run_id = (SELECT max_by(run_id, scored_at) FROM {fq('gold', 'listing_scores')})),
             m AS (SELECT max(CASE WHEN name = 'mortgage_30y' THEN value END) AS mortgage_30y,
                          max(CASE WHEN name = 'raleigh_hpi' THEN yoy_pct END) AS raleigh_yoy,
                          max(CASE WHEN name = 'durham_hpi' THEN yoy_pct END) AS durham_yoy
                   FROM {fq('gold', 'jev_ready_macro')})
        SELECT l.listing_id, l.address, l.neighborhood, l.city, l.price, l.beds, l.baths, l.sqft, l.year_built, l.hoa_monthly, l.property_type,
               n.school_rating, n.walk_score, n.median_ppsf, n.safety_index, n.environment_index, n.flood_zone,
               ROUND(l.price / l.sqft / n.median_ppsf * 100 - 100, 1) AS price_vs_area_pct, m.mortgage_30y,
               IF(l.city IN ('Durham', 'Chapel Hill', 'Carrboro'), m.durham_yoy, m.raleigh_yoy) AS metro_hpi_yoy_pct,
               map_from_entries(collect_list(struct(s.metric, s.score))) AS scores, current_timestamp() AS published_at
        FROM (SELECT * FROM {fq('bronze', 'listings_raw')} QUALIFY ROW_NUMBER() OVER (PARTITION BY listing_id ORDER BY ingested_at DESC) = 1) l
        CROSS JOIN m
        LEFT JOIN {fq('gold', 'jev_ready_neighborhoods')} n ON n.neighborhood = l.neighborhood
        LEFT JOIN s ON s.listing_id = l.listing_id
        GROUP BY ALL""")
    c.sql(f"""CREATE OR REPLACE VIEW {fq('gold', 'fred_series')}
        COMMENT 'FRED macro data (mortgage rates, Triangle house-price indexes, unemployment): one row per series and date, latest ingest wins' AS
        SELECT name, series_id, obs_date, value, ingested_at FROM {fq('bronze', 'fred_observations')}
        QUALIFY ROW_NUMBER() OVER (PARTITION BY series_id, obs_date ORDER BY ingested_at DESC) = 1""")
    c.sql(f"""CREATE OR REPLACE VIEW {fq('gold', 'listing_scorecard')}
        COMMENT 'One row per listing: facts + live risk indexes + the 10 sub-scores (latest run)' AS
        WITH s AS (SELECT * FROM {fq('gold', 'listing_scores')} WHERE run_id = (SELECT max_by(run_id, scored_at) FROM {fq('gold', 'listing_scores')}))
        SELECT l.listing_id, l.address, l.neighborhood, l.city, l.price, l.beds, l.sqft,
               r.safety_index, r.environment_index, r.flood_zone,
               (SELECT value FROM {fq('silver', 'macro_latest')} WHERE name = 'mortgage_30y') AS mortgage_30y,
               map_from_entries(collect_list(struct(s.metric, s.score))) AS scores
        FROM (SELECT * FROM {fq('bronze', 'listings_raw')} QUALIFY ROW_NUMBER() OVER (PARTITION BY listing_id ORDER BY ingested_at DESC) = 1) l
        LEFT JOIN {fq('silver', 'neighborhood_risk')} r ON r.neighborhood = l.neighborhood
        LEFT JOIN s ON s.listing_id = l.listing_id
        GROUP BY ALL""")


def _insert_json(c, table, rows, schema_ddl, select_cols):
    if not rows:
        return 0
    c.sql(f"INSERT INTO {table} SELECT {select_cols} FROM (SELECT explode(from_json(:payload, '{schema_ddl}')) AS r)",
          {"payload": json.dumps(rows, default=str)})
    return len(rows)


def fred_history(snapshot, years=None, fetch=None):
    """Full FRED history (default 10 years, FRED_HISTORY_YEARS) straight from FRED's keyless CSV; falls back to the
    12-point history kept in data/live/fred.json when FRED can't be reached."""
    from .sources import fred
    years = int(years or os.getenv("FRED_HISTORY_YEARS", "10"))
    cutoff = (dt.date.today() - dt.timedelta(days=365 * years)).isoformat()
    obs = []
    live = fetch or (fred.fetch_series if os.getenv("FRED_FULL_HISTORY", "1") != "0" else None)
    for name, s in snapshot.items():
        try:
            if not live:
                raise ValueError("full history disabled")
            rows = live(s["series_id"])
            rows = [(d, v) for d, v in rows if d >= cutoff]
            if not rows:
                raise ValueError("empty")
        except Exception:
            rows = s.get("history", [])
        obs += [{"series_id": s["series_id"], "name": name, "obs_date": d, "value": v} for d, v in rows]
    return obs


def load(c, run_id, now):
    counts = {}
    fred = _load("fred.json")["series"]
    obs = fred_history(fred)
    counts["fred_observations"] = _insert_json(
        c, fq("bronze", "fred_observations"), obs, "ARRAY<STRUCT<series_id:STRING,name:STRING,obs_date:STRING,value:DOUBLE>>",
        f"r.series_id, r.name, to_date(r.obs_date), r.value, '{run_id}', TIMESTAMP '{now}'")
    snaps = []
    for fname, source in (("safety.json", "police"), ("environment.json", "environment")):
        try:
            for hood, payload in _load(fname)["neighborhoods"].items():
                snaps.append({"neighborhood": hood, "source": source, "payload": json.dumps(payload)})
        except FileNotFoundError:
            pass
    try:  # neighborhood reference profiles (school / walkability / price per sq ft, with their sources)
        with open(os.path.join(DATA_DIR, "neighborhoods.json")) as f:
            snaps += [{"neighborhood": h["name"], "source": "profile", "payload": json.dumps(h)} for h in json.load(f)]
    except FileNotFoundError:
        pass
    counts["neighborhood_snapshots"] = _insert_json(
        c, fq("bronze", "neighborhood_snapshots"), snaps, "ARRAY<STRUCT<neighborhood:STRING,source:STRING,payload:STRING>>",
        f"r.neighborhood, r.source, r.payload, '{run_id}', TIMESTAMP '{now}'")
    import csv
    with open(os.path.join(DATA_DIR, "listings.csv")) as f:
        listings = list(csv.DictReader(f))
    counts["listings_raw"] = _insert_json(
        c, fq("bronze", "listings_raw"), listings,
        "ARRAY<STRUCT<id:STRING,address:STRING,neighborhood:STRING,city:STRING,price:STRING,beds:STRING,baths:STRING,sqft:STRING,"
        "year_built:STRING,hoa_monthly:STRING,property_type:STRING,features:STRING>>",
        "CAST(r.id AS BIGINT), r.address, r.neighborhood, r.city, CAST(r.price AS BIGINT), CAST(r.beds AS INT), CAST(r.baths AS DOUBLE), "
        f"CAST(r.sqft AS INT), CAST(r.year_built AS INT), CAST(r.hoa_monthly AS INT), r.property_type, r.features, '{run_id}', TIMESTAMP '{now}'")
    return counts


def default_scores():
    sys.path.insert(0, os.path.abspath(os.path.join(DATA_DIR, "..")))
    from app import metrics, tools
    prof = {"max_price": 550000, "annual_income": 165000, "min_beds": 3}
    return [{"listing_id": r["id"], "metric": m, "score": s}
            for r in tools.LISTINGS for m, s in metrics.scores(r["id"], prof).items()]


def sync(client=None, dry_run=False, actor=None):
    c = client or Client(dry_run=dry_run)
    run_id = "run-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    ddl(c)
    counts = load(c, run_id, now)
    counts["listing_scores"] = _insert_json(
        c, fq("gold", "listing_scores"), default_scores(), "ARRAY<STRUCT<listing_id:BIGINT,metric:STRING,score:INT>>",
        f"r.listing_id, r.metric, r.score, 'default_family', '{run_id}', TIMESTAMP '{now}'")
    silver_gold(c)
    governance(c)
    manifest = {}
    try:
        manifest = _load("manifest.json")
    except FileNotFoundError:
        pass
    audit = [{"source": k, "status": v.get("status"), "rows": counts.get({"fred": "fred_observations"}.get(k, "neighborhood_snapshots"), 0),
              "warnings": json.dumps(v.get("warnings", []))} for k, v in manifest.get("sources", {}).items()]
    audit.append({"source": "databricks_sync", "status": "ok", "rows": sum(counts.values()), "warnings": "[]"})
    _insert_json(c, fq("gold", "pipeline_audit"), audit, "ARRAY<STRUCT<source:STRING,status:STRING,rows:BIGINT,warnings:STRING>>",
                 f"'{run_id}', r.source, r.status, r.rows, r.warnings, '{actor or os.getenv('USER', 'pipeline')}', "
                 f"'{os.getenv('GITHUB_SHA', 'local')}', TIMESTAMP '{now}'")
    return {"run_id": run_id, "catalog": catalog(), "counts": counts, "statements": len(c.log)}


def log_decision(model, jev_engine, profile, rows, client=None, source=None):
    """Append one AI recommendation to gold.agent_decisions + its ranked homes to gold.match_results (best effort; never raises)."""
    if not (client or configured()):
        return None
    source = source or os.getenv("EP_DECISION_SOURCE", "app")
    try:
        c = client or Client()
        now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        rec = [{"decision_id": uuid.uuid4().hex, "model": model, "jev_engine": jev_engine,
                "profile_summary": (profile.get("summary") or "")[:500], "weights": json.dumps(profile.get("weights", {})),
                "top_listing_ids": json.dumps([r["listing"]["id"] for r in rows[:5]]),
                "verdicts": json.dumps({str(r["listing"]["id"]): (r.get("jev") or {}).get("verdict") for r in rows[:5]})}]
        _insert_json(c, fq("gold", "agent_decisions"), rec,
                     "ARRAY<STRUCT<decision_id:STRING,model:STRING,jev_engine:STRING,profile_summary:STRING,weights:STRING,top_listing_ids:STRING,verdicts:STRING>>",
                     f"r.decision_id, '{source}', r.model, r.jev_engine, r.profile_summary, r.weights, r.top_listing_ids, r.verdicts, TIMESTAMP '{now}'")
        try:  # ranked homes: separate insert so a bad row never loses the decision itself
            ranked = [{"rank": i + 1, "listing_id": r["listing"].get("id"), "address": r["listing"].get("address"),
                       "neighborhood": r["listing"].get("neighborhood"), "price": r["listing"].get("price"), "fit": r.get("total"),
                       "scores": json.dumps(r.get("scores") or {}), "verdict": (r.get("jev") or {}).get("verdict"),
                       "monthly": (r.get("finance") or {}).get("monthly_total")} for i, r in enumerate(rows[:10])]
            _insert_json(c, fq("gold", "match_results"), ranked,
                         "ARRAY<STRUCT<rank:INT,listing_id:BIGINT,address:STRING,neighborhood:STRING,price:BIGINT,fit:DOUBLE,scores:STRING,verdict:STRING,monthly:BIGINT>>",
                         f"'{rec[0]['decision_id']}', '{source}', r.rank, r.listing_id, r.address, r.neighborhood, r.price, r.fit, r.scores, "
                         f"r.verdict, r.monthly, TIMESTAMP '{now}'")
        except Exception:
            pass
        return rec[0]["decision_id"]
    except Exception:
        return None


def _safe(v):
    """Literal-safe identifier text for values we inline into SQL (model names)."""
    import re
    return re.sub(r"[^A-Za-z0-9_.:/-]", "", str(v or "unknown"))[:80]


def log_eval(summary, detail, client=None):
    """scripts/agent_eval.py results -> gold.agent_eval_runs (one row per metric) + gold.agent_eval_cases. Returns eval_id."""
    c = client or Client()
    ddl(c)
    eval_id = "eval-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    metrics = []
    for suite in ("extraction", "empty_data", "pipeline"):
        block = summary.get(suite) or {}
        for k, v in block.items():
            if isinstance(v, dict):
                metrics += [{"metric": f"{suite}.{k}.{m}", "value": float(x), "detail": ""} for m, x in v.items() if isinstance(x, (int, float))]
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                metrics.append({"metric": f"{suite}.{k}", "value": float(v), "detail": ""})
    _insert_json(c, fq("gold", "agent_eval_runs"), metrics, "ARRAY<STRUCT<metric:STRING,value:DOUBLE,detail:STRING>>",
                 f"'{eval_id}', '{_safe(summary.get('model'))}', {'true' if summary.get('nemotron_ok') else 'false'}, r.metric, r.value, r.detail, "
                 f"TIMESTAMP '{now}'")
    cases = [{"suite": "extraction", "prompt": r["prompt"], "expected": json.dumps(r["expected"]), "nemotron": json.dumps(r["llm"]),
              "shipped": json.dumps(r["merged"]), "n_exact": r["llm_score"]["exact"], "pass": r["merged_score"]["exact"], "ms": r["llm_ms"]}
             for r in detail.get("extraction", [])]
    cases += [{"suite": "empty_data", "prompt": r["prompt"], "expected": "no_match + relax suggestion", "nemotron": "",
               "shipped": r["recommendation"], "n_exact": None, "pass": r["pass"], "ms": int(r["secs"] * 1000)} for r in detail.get("empty_data", [])]
    cases += [{"suite": "pipeline", "prompt": r["prompt"], "expected": "all checks true", "nemotron": r["recommendation"],
               "shipped": json.dumps(r["checks"]), "n_exact": None,
               "pass": all(v is True or (isinstance(v, str) and v.split("/")[0] == v.split("/")[-1]) for v in r["checks"].values()),
               "ms": int(r["secs"] * 1000)} for r in detail.get("pipeline", [])]
    _insert_json(c, fq("gold", "agent_eval_cases"), cases,
                 "ARRAY<STRUCT<suite:STRING,prompt:STRING,expected:STRING,nemotron:STRING,shipped:STRING,n_exact:BOOLEAN,pass:BOOLEAN,ms:BIGINT>>",
                 f"'{eval_id}', r.suite, r.prompt, r.expected, r.nemotron, r.shipped, r.n_exact, r.pass, r.ms, TIMESTAMP '{now}'")
    return eval_id


def check():
    c = Client()
    c.sql("SELECT 1")
    c.sql(f"SHOW SCHEMAS IN {q(catalog())}")
    return {"ok": True, "catalog": catalog(), "warehouse_id": os.getenv("DATABRICKS_WAREHOUSE_ID")}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    if a.dry_run:
        c = Client(dry_run=True)
        print(json.dumps(sync(client=c), indent=1))
        print("\n\n".join(s.strip() for s in c.log))
        return 0
    if not configured():
        print("Set DATABRICKS_HOST, DATABRICKS_TOKEN and DATABRICKS_WAREHOUSE_ID (and optionally DATABRICKS_CATALOG).", file=sys.stderr)
        return 2
    print(json.dumps(check() if a.check else sync(), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
