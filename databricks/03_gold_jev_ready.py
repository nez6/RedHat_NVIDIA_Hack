# Databricks notebook source
# MAGIC %md
# MAGIC # EstatePulse ELT · 03 Gold (ready for Jev)
# MAGIC One row per listing with the 10 factor scores, the pre-computed math (live FRED rate, monthly cost, housing ratio),
# MAGIC the live safety text, and the exact Jev `state` + `questions` payload — ready for Jev ingestion.

# COMMAND ----------
dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("bundle_root", "")
CATALOG = dbutils.widgets.get("catalog")
import json, os, sys
ROOT = dbutils.widgets.get("bundle_root") or os.path.abspath(os.path.join(os.getcwd(), ".."))
sys.path.insert(0, ROOT)
from pipeline import medallion
from pyspark.sql import functions as F

S, G = f"`{CATALOG}`.`estatepulse_silver`", f"`{CATALOG}`.`estatepulse_gold`"
factor_rows = [r.asDict() for r in spark.table(f"{S}.`listing_factors`").collect()]
hood_rows = [r.asDict() for r in spark.table(f"{S}.`neighborhood_indexes`").collect()]
RUN_ID = factor_rows[0]["run_id"]

# rebuild the live snapshot dir for this task (each task runs in its own process)
live_dir = None
try:
    live_dir = dbutils.jobs.taskValues.get(taskKey="silver", key="live_dir")
except Exception:
    pass
if not live_dir or not os.path.exists(live_dir):
    import tempfile
    live_dir = tempfile.mkdtemp()
    macro = {}
    for r in spark.sql(f"SELECT * FROM `{CATALOG}`.`estatepulse_bronze`.`fred_observations` WHERE run_id = (SELECT max(run_id) FROM `{CATALOG}`.`estatepulse_bronze`.`fred_observations`)").collect():
        macro.setdefault(r["name"], []).append({"name": r["name"], "series_id": r["series_id"], "obs_date": r["obs_date"], "value": float(r["value"])})
    from pipeline.medallion import _series_from_obs
    series = _series_from_obs([x for v in macro.values() for x in v])
    safety = {h["neighborhood"]: {"safety_index": h["safety_index"], "source": h["safety_source"], "scope": "silver",
                                  "incidents": h["incidents_lookback"] or 0, "jev_state": h["jev_safety_text"]}
              for h in hood_rows if h["safety_source"] != "baseline"}
    env = {h["neighborhood"]: {"environment_index": h["environment_index"], "flood": {"zone": h["flood_zone"], "sfha": False},
                               "aqi_30d": h["aqi_30d"], "weather_12m": {}, "source": "silver"}
           for h in hood_rows if h["environment_index"] is not None}
    for name, obj in (("fred.json", {"series": series}), ("safety.json", {"neighborhoods": safety}), ("environment.json", {"neighborhoods": env})):
        with open(os.path.join(live_dir, name), "w") as f:
            json.dump(obj, f, default=str)

rows = medallion.gold(factor_rows, hood_rows, live_dir)

# COMMAND ----------
(spark.createDataFrame(rows).withColumn("run_id", F.lit(RUN_ID)).withColumn("published_at", F.current_timestamp())
 .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{G}.`jev_ready_listings`"))
spark.sql(f"COMMENT ON TABLE {G}.`jev_ready_listings` IS 'Gold: one row per listing with 10 factor scores, composite, live-rate math and the Jev state/questions payload. Ready for Jev ingestion.'")
for t in ("jev_ready_listings",):
    try:
        spark.sql(f"ALTER TABLE {G}.`{t}` SET TAGS ('project' = 'estatepulse', 'layer' = 'gold', 'pii' = 'none', 'consumer' = 'jev')")
    except Exception as e:
        print("tags skipped:", str(e)[:120])
spark.sql(f"""CREATE TABLE IF NOT EXISTS {G}.`pipeline_audit` (run_id STRING, layer STRING, table_name STRING, row_count BIGINT, logged_at TIMESTAMP)
              COMMENT 'Audit trail of every ELT run'""")
spark.sql(f"""INSERT INTO {G}.`pipeline_audit` VALUES
  ('{RUN_ID}', 'silver', 'listing_factors', {len(factor_rows)}, current_timestamp()),
  ('{RUN_ID}', 'silver', 'neighborhood_indexes', {len(hood_rows)}, current_timestamp()),
  ('{RUN_ID}', 'gold', 'jev_ready_listings', {len(rows)}, current_timestamp())""")
display(spark.table(f"{G}.`jev_ready_listings`").select("rank", "address", "neighborhood", "composite_score", "monthly_cost", "mortgage_rate_pct").orderBy("rank"))
