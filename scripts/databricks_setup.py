"""One-shot Unity Catalog setup over the SQL warehouse (no notebooks / bundle deploy needed). Works on Databricks Free Edition.

    python scripts/databricks_setup.py            # secure schema + client_intake table + client_documents volume + RBAC grants
    python scripts/databricks_setup.py --dry-run  # print the SQL

Groups: the three estatepulse_* groups already exist (scripts/databricks_groups.py); nothing here creates groups.
Each GRANT is tried on the group first. On Free Edition the groups are workspace-level, which Unity Catalog can't grant
to (PRINCIPAL_DOES_NOT_EXIST), so the script looks up the group's members and grants each user directly. Add people to
a group (Settings -> Identity and access -> Groups) and re-run: it is idempotent. The client_intake row filter checks
both workspace (is_member) and account (is_account_group_member) groups, so it works either way.
"""
import argparse
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app import rbac  # noqa: E402
from pipeline import databricks_sink as D  # noqa: E402

SCIM = "/api/2.0/preview/scim/v2"


def statements(cat):
    c = D.q(cat)
    sec = f"{c}.`estatepulse_secure`"
    setup = [
        f"CREATE SCHEMA IF NOT EXISTS {sec} COMMENT 'Client intake - restricted to advisors'",
        f"""CREATE TABLE IF NOT EXISTS {sec}.`client_intake` (
  intake_id STRING NOT NULL, created_at TIMESTAMP, client_name STRING, client_email STRING, financing STRING,
  max_price INT, document_path STRING COMMENT 'path in the client_documents volume; contents never copied into tables',
  profile_json STRING, status STRING) COMMENT 'Home Match Toolkit submissions. PII: contact details + financing status.'""",
        f"ALTER TABLE {sec}.`client_intake` SET TAGS ('pii' = 'client_contact', 'project' = 'estatepulse')",
        f"CREATE VOLUME IF NOT EXISTS {sec}.`client_documents` COMMENT 'Pre-approval letters / proof of funds (advisors only)'",
    ]
    filt = rbac.row_filter_sql(cat)
    row_filter = [s for s in filt if not s.startswith("GRANT")]
    client_grants = [(rbac.ROLES["client"]["group"], s) for s in filt if s.startswith("GRANT")]
    return setup + row_filter, rbac.uc_grants(cat) + client_grants


def group_members(group, scim=None):
    """User names (emails) in a workspace group, via the SCIM API with the same token."""
    def get(path):
        if scim:
            return scim(path)
        return D.Client()._http("GET", path)
    found = get(f"{SCIM}/Groups?filter=" + urllib.parse.quote(f'displayName eq "{group}"')).get("Resources") or []
    if not found:
        return None  # group doesn't exist at all
    out = []
    for m in found[0].get("members") or []:
        ref = m.get("$ref", "")
        if ref.startswith("Users/"):
            u = get(f"{SCIM}/{ref}")
            if u.get("userName"):
                out.append(u["userName"])
    return out


def apply_grants(client, grants, members=group_members):
    report, cache = [], {}
    for group, stmt in grants:
        try:
            client.sql(stmt)
            report.append({"principal": group, "status": "granted to group", "sql": stmt})
            continue
        except Exception as e:
            if not rbac.principal_missing(e):
                report.append({"principal": group, "status": "failed: " + str(e).splitlines()[0][:160], "sql": stmt})
                continue
        if group not in cache:
            try:
                cache[group] = members(group)
            except Exception as e:
                cache[group] = e
        users = cache[group]
        if isinstance(users, Exception) or users is None:
            report.append({"principal": group, "status": "group not found" if users is None else f"member lookup failed: {users}", "sql": stmt})
            continue
        if not users:
            report.append({"principal": group, "status": "workspace group has no members yet (add people, then re-run)", "sql": stmt})
            continue
        for user, s in zip(users, rbac.for_principals(group, stmt, users)):
            try:
                client.sql(s)
                report.append({"principal": user, "status": f"granted to member of {group}", "sql": s})
            except Exception as e:
                report.append({"principal": user, "status": "failed: " + str(e).splitlines()[0][:160], "sql": s})
    return report


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    setup, grants = statements(D.catalog())
    if a.dry_run:
        print(";\n".join(setup + [s for _, s in grants]) + ";")
        return 0
    client = D.Client()
    errors = []
    for s in setup:
        try:
            client.sql(s)
        except Exception as e:
            errors.append(f"{s.splitlines()[0][:100]} -> {str(e).splitlines()[0][:160]}")
    report = apply_grants(client, grants)
    summary = {}
    for r in report:
        key = r["status"] if not r["status"].startswith("granted to member") else "granted to group members"
        summary[key] = summary.get(key, 0) + 1
    print(json.dumps({"setup_statements": len(setup), "setup_errors": errors, "grants": summary,
                      "users_granted": sorted({r["principal"] for r in report if r["status"].startswith("granted to member")}),
                      "waiting_for_members": sorted({r["principal"] for r in report if "no members" in r["status"]})}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
