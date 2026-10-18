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
# Groups already exist (scripts/databricks_groups.py) - this cell only GRANTs, it never creates groups.
# Databricks Free Edition has workspace-level groups only; Unity Catalog can't grant to those (PRINCIPAL_DOES_NOT_EXIST),
# so each failed group grant is re-applied to every member of that workspace group. Re-run after adding people.
from databricks.sdk import WorkspaceClient
w = WorkspaceClient()
_members = {}

def members(group):
    if group not in _members:
        found = list(w.groups.list(filter=f'displayName eq "{group}"', attributes="id,members"))
        users = []
        for m in (found[0].members or []) if found else []:
            if (m.ref or "").startswith("Users/"):
                users.append(w.users.get(m.value).user_name)
        _members[group] = users if found else None
    return _members[group]

filt = rbac.row_filter_sql(CATALOG)
grants = rbac.uc_grants(CATALOG) + [(rbac.ROLES["client"]["group"], s) for s in filt if s.startswith("GRANT")]
results = []
for stmt in [s for s in filt if not s.startswith("GRANT")]:  # row filter function + ALTER TABLE
    try:
        spark.sql(stmt)
        results.append(("row_filter", "ok", stmt))
    except Exception as e:
        results.append(("row_filter", "skipped: " + str(e).splitlines()[0][:160], stmt))
for group, stmt in grants:
    try:
        spark.sql(stmt)
        results.append((group, "granted to group", stmt))
        continue
    except Exception as e:
        if not rbac.principal_missing(e):
            results.append((group, "failed: " + str(e).splitlines()[0][:160], stmt))
            continue
    users = members(group)
    if not users:
        results.append((group, "group not found" if users is None else "no members yet - add people, re-run", stmt))
        continue
    for user, s in zip(users, rbac.for_principals(group, stmt, users)):
        try:
            spark.sql(s)
            results.append((user, f"granted (member of {group})", s))
        except Exception as e:
            results.append((user, "failed: " + str(e).splitlines()[0][:160], s))
display(spark.createDataFrame(results, "principal STRING, status STRING, statement STRING"))
print({st.split(" (")[0].split(":")[0]: sum(1 for r in results if r[1].split(" (")[0].split(":")[0] == st.split(" (")[0].split(":")[0]) for st in {r[1] for r in results}})
