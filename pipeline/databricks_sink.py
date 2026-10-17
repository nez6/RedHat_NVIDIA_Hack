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


def governance(c):
    for t in [("bronze", "fred_observations"), ("bronze", "neighborhood_snapshots"), ("bronze", "listings_raw"),
              ("silver", "neighborhood_risk"), ("silver", "macro_latest"), ("gold", "listing_scores"),
              ("gold", "listing_scorecard"), ("gold", "pipeline_audit"), ("gold", "agent_decisions")]:
        kind = "VIEW" if t[1] == "listing_scorecard" else "TABLE"
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


def load(c, run_id, now):
    counts = {}
    fred = _load("fred.json")["series"]
    obs = [{"series_id": s["series_id"], "name": k, "obs_date": d, "value": v} for k, s in fred.items() for d, v in s.get("history", [])]
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


def log_decision(model, jev_engine, profile, rows, client=None):
    """Append one AI recommendation to gold.agent_decisions (best effort; never raises)."""
    if not (client or configured()):
        return None
    try:
        c = client or Client()
        now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        rec = [{"decision_id": uuid.uuid4().hex, "model": model, "jev_engine": jev_engine,
                "profile_summary": (profile.get("summary") or "")[:500], "weights": json.dumps(profile.get("weights", {})),
                "top_listing_ids": json.dumps([r["listing"]["id"] for r in rows[:5]]),
                "verdicts": json.dumps({str(r["listing"]["id"]): (r.get("jev") or {}).get("verdict") for r in rows[:5]})}]
        _insert_json(c, fq("gold", "agent_decisions"), rec,
                     "ARRAY<STRUCT<decision_id:STRING,model:STRING,jev_engine:STRING,profile_summary:STRING,weights:STRING,top_listing_ids:STRING,verdicts:STRING>>",
                     f"r.decision_id, 'app', r.model, r.jev_engine, r.profile_summary, r.weights, r.top_listing_ids, r.verdicts, TIMESTAMP '{now}'")
        return rec[0]["decision_id"]
    except Exception:
        return None


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
