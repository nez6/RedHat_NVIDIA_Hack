# Databricks notebook source
# MAGIC %md
# MAGIC # EstatePulse ELT · 04 RBAC grants
# MAGIC Applies the RBAC catalog in `app/rbac.py` to Unity Catalog for the three groups:
# MAGIC **estatepulse_admins** (tester / full access), **estatepulse_realtors** (silver + gold, client intake + documents
# MAGIC read-only) and **estatepulse_clients** (gold listings, plus only their own intake row via a row filter).
# MAGIC Create the groups first with `python scripts/databricks_groups.py` (or in Settings → Identity and access → Groups).

# COMMAND ----------
dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("bundle_root", "")
CATALOG = dbutils.widgets.get("catalog")
import os, sys
ROOT = dbutils.widgets.get("bundle_root") or os.path.abspath(os.path.join(os.getcwd(), ".."))
sys.path.insert(0, ROOT)
from app import rbac

# COMMAND ----------
results = []
for group, stmt in rbac.uc_grants(CATALOG):
    try:
        spark.sql(stmt)
        results.append((group, "ok", stmt))
    except Exception as e:  # a missing group or securable should not fail the whole ELT run
        results.append((group, "skipped: " + str(e).splitlines()[0][:160], stmt))
for stmt in rbac.row_filter_sql(CATALOG):
    try:
        spark.sql(stmt)
        results.append(("row_filter", "ok", stmt))
    except Exception as e:
        results.append(("row_filter", "skipped: " + str(e).splitlines()[0][:160], stmt))
display(spark.createDataFrame(results, "principal STRING, status STRING, statement STRING"))
skipped = [r for r in results if r[1] != "ok"]
print(f"{len(results) - len(skipped)} grants applied, {len(skipped)} skipped")
