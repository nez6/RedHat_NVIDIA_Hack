# Databricks notebook source
# MAGIC %md
# MAGIC # EstatePulse ELT · 02 Silver (calculations per factor)
# MAGIC Reads the latest bronze run, computes neighborhood safety/environment indexes and then every one of the
# MAGIC 10 factor calculations for every listing — score, formula and inputs — with the same code the app uses.

# COMMAND ----------
dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("bundle_root", "")
CATALOG = dbutils.widgets.get("catalog")
import os, sys
ROOT = dbutils.widgets.get("bundle_root") or os.path.abspath(os.path.join(os.getcwd(), ".."))
sys.path.insert(0, ROOT)
from pipeline import medallion
from pyspark.sql import functions as F

B = f"`{CATALOG}`.`estatepulse_bronze`"
RUN_ID = spark.sql(f"SELECT max(run_id) AS r FROM {B}.`listings_raw`").first()["r"]

def latest(table):
    if not spark.catalog.tableExists(f"{CATALOG}.estatepulse_bronze.{table}"):
        return []
    rid = spark.sql(f"SELECT max(run_id) AS r FROM {B}.`{table}`").first()["r"]
    return [r.asDict() for r in spark.sql(f"SELECT * FROM {B}.`{table}` WHERE run_id = '{rid}'").collect()]

bronze = {t: latest(t) for t in ("fred_observations", "police_incidents", "cary_offense_counts", "environment_raw", "neighborhoods_raw", "listings_raw")}
for r in bronze["police_incidents"]:
    r["lat"], r["lon"] = float(r["lat"]), float(r["lon"])
for r in bronze["neighborhoods_raw"]:
    for k in ("lat", "lon"):
        r[k] = float(r[k])
    for k in ("safety_index", "school_rating", "median_ppsf", "walk_score"):
        r[k] = int(float(r[k]))
for r in bronze["cary_offense_counts"]:
    r["n"] = int(r["n"])

hood_rows, factor_rows, live_dir = medallion.silver(bronze)
dbutils.jobs.taskValues.set("live_dir", live_dir)

# COMMAND ----------
S = f"`{CATALOG}`.`estatepulse_silver`"
(spark.createDataFrame(hood_rows).withColumn("run_id", F.lit(RUN_ID)).withColumn("computed_at", F.current_timestamp())
 .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{S}.`neighborhood_indexes`"))
(spark.createDataFrame(factor_rows).withColumn("run_id", F.lit(RUN_ID)).withColumn("computed_at", F.current_timestamp())
 .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{S}.`listing_factors`"))
spark.sql(f"COMMENT ON TABLE {S}.`neighborhood_indexes` IS 'Per-neighborhood safety (police) and environment (FEMA, Open-Meteo) indexes computed from bronze.'")
spark.sql(f"COMMENT ON TABLE {S}.`listing_factors` IS 'One row per listing x factor: score (0-100), the formula and the exact inputs used.'")
try:
    spark.sql(f"ALTER TABLE {S}.`listing_factors` ADD CONSTRAINT score_0_100 CHECK (score BETWEEN 0 AND 100)")
except Exception as e:  # already exists after the first run
    print("constraint:", str(e)[:100])
# one view per factor so each calculation is browsable on its own
for f in sorted({r["factor"] for r in factor_rows}):
    spark.sql(f"CREATE OR REPLACE VIEW {S}.`factor_{f}` COMMENT 'Calculation for the {f} factor' AS "
              f"SELECT listing_id, score, formula, inputs_json, run_id FROM {S}.`listing_factors` WHERE factor = '{f}'")
print(len(hood_rows), "neighborhoods,", len(factor_rows), "factor rows")
