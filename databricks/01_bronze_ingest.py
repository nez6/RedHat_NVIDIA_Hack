# Databricks notebook source
# MAGIC %md
# MAGIC # EstatePulse ELT · 01 Bronze (raw)
# MAGIC Pulls raw data exactly as received: FRED series, Raleigh PD incidents, Cary PD offense counts,
# MAGIC FEMA flood + Open-Meteo payloads, listings and neighborhoods. Any source that cannot be reached
# MAGIC falls back to the last committed snapshot and is flagged in `source_mode`.

# COMMAND ----------
dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("bundle_root", "")
CATALOG = dbutils.widgets.get("catalog")
import os, sys
ROOT = dbutils.widgets.get("bundle_root") or os.path.abspath(os.path.join(os.getcwd(), ".."))
sys.path.insert(0, ROOT)

from pipeline import medallion
from pipeline.sources import environment, fred, police
from pyspark.sql import functions as F

RUN_ID = medallion.new_run_id()
dbutils.jobs.taskValues.set("run_id", RUN_ID)
for layer in ("bronze", "silver", "gold"):
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{CATALOG}`.`estatepulse_{layer}` COMMENT 'EstatePulse AI {layer} layer'")

# COMMAND ----------
tables, log = medallion.bronze(fred.fetch_all, police.fetch_raleigh, police.fetch_cary, environment.fetch_neighborhood,
                               snapshot_dir=os.path.join(ROOT, "data", "live"))
print(log)

COMMENTS = {
    "fred_observations": "Raw FRED observations (mortgage rates, FHFA HPI, unemployment). Public data.",
    "police_incidents": "Raw Raleigh PD NIBRS incidents (last 90 days): type, category, date, lat/lon. No names or addresses.",
    "cary_offense_counts": "Cary PD offense counts over the last 12 months (server-side aggregate).",
    "environment_raw": "Raw FEMA NFHL flood zone + Open-Meteo AQI and weather extremes per neighborhood centroid (JSON).",
    "neighborhoods_raw": "Neighborhood reference data (centroid, school rating, walk score, baseline safety).",
    "listings_raw": "Listings as ingested. Current source: synthetic sample data.",
}
for name, rows in tables.items():
    if not rows:
        print(f"skip {name}: no rows")
        continue
    df = (spark.createDataFrame([{k: (None if v is None else str(v)) for k, v in r.items()} for r in rows])
          .withColumn("run_id", F.lit(RUN_ID)).withColumn("ingested_at", F.current_timestamp()))
    full = f"`{CATALOG}`.`estatepulse_bronze`.`{name}`"
    df.write.mode("append").option("mergeSchema", "true").saveAsTable(full)
    spark.sql(f"COMMENT ON TABLE {full} IS '{COMMENTS[name]}'")
    print(f"{name}: {len(rows)} rows -> {full}")
