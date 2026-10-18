"""One-shot Unity Catalog setup over the SQL warehouse (no notebooks / bundle deploy needed).

    python scripts/databricks_setup.py            # secure schema + client_intake table + client_documents volume + RBAC grants
    python scripts/databricks_setup.py --dry-run  # print the SQL

Same statements as databricks/01_bronze_ingest.py (secure part) and databricks/04_rbac_grants.py. Run
`python -m pipeline.databricks_sink` first (creates the bronze/silver/gold schemas) and scripts/databricks_groups.py
(creates the three groups); grants for anything missing are reported as skipped, never fatal.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app import rbac  # noqa: E402
from pipeline import databricks_sink as D  # noqa: E402


def statements(cat):
    c = D.q(cat)
    sec = f"{c}.`estatepulse_secure`"
    return [
        f"CREATE SCHEMA IF NOT EXISTS {sec} COMMENT 'Client intake - restricted to advisors'",
        f"""CREATE TABLE IF NOT EXISTS {sec}.`client_intake` (
  intake_id STRING NOT NULL, created_at TIMESTAMP, client_name STRING, client_email STRING, financing STRING,
  max_price INT, document_path STRING COMMENT 'path in the client_documents volume; contents never copied into tables',
  profile_json STRING, status STRING) COMMENT 'Home Match Toolkit submissions. PII: contact details + financing status.'""",
        f"ALTER TABLE {sec}.`client_intake` SET TAGS ('pii' = 'client_contact', 'project' = 'estatepulse')",
        f"CREATE VOLUME IF NOT EXISTS {sec}.`client_documents` COMMENT 'Pre-approval letters / proof of funds (advisors only)'",
    ] + [s for _, s in rbac.uc_grants(cat)] + rbac.row_filter_sql(cat)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    stmts = statements(D.catalog())
    if a.dry_run:
        print(";\n".join(stmts) + ";")
        return 0
    client, ok, skipped = D.Client(), 0, []
    for s in stmts:
        try:
            client.sql(s)
            ok += 1
        except Exception as e:
            skipped.append((s.splitlines()[0][:110], str(e).splitlines()[0][:200] if str(e) else type(e).__name__))
    print(f"{ok} statements applied, {len(skipped)} skipped")
    for s, why in skipped:
        print(f"  skipped: {s}\n     why: {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
