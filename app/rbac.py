"""RBAC catalog: the three Estate Pulse user groups, what each may do in the app, and their Unity Catalog grants.

One catalog drives both layers, so the app and Databricks can't drift apart:

  role      Databricks group       home in the app                  can
  admin     estatepulse_admins     /app  (tester UI: matrix, value,  everything, incl. diagnostics + architecture
                                   how we score, architecture)
  realtor   estatepulse_realtors   / realtor workspace (12 steps)    run matches, import Zillow pages, see client
                                                                     intakes, mark kickoff meetings done
  client    estatepulse_clients    / client portal (6-month plan)    their own questionnaire, matches and portal

    python -m app.rbac --sql [--catalog workspace]   # print the Unity Catalog GRANT statements
"""
import argparse

CATALOG_DEFAULT = "workspace"

ROLES = {
    "admin": {
        "label": "Admin (tester)", "group": "estatepulse_admins", "home": "app",
        "pages": {"app", "how", "value", "architecture", "start", "portal", "realtor_view", "client_view"},
        "permissions": {"match:run", "match:narrate", "listings:import", "intake:list", "intake:kickoff", "system:diagnostics"},
    },
    "realtor": {
        "label": "Realtor", "group": "estatepulse_realtors", "home": "realtor",
        "pages": {"app", "how", "value", "start"},
        "permissions": {"match:run", "match:narrate", "listings:import", "intake:list", "intake:kickoff"},
    },
    "client": {
        "label": "Client", "group": "estatepulse_clients", "home": "client",
        "pages": {"app", "value", "start", "portal"},
        "permissions": {"match:run", "intake:self"},
    },
}

PERMISSION_HELP = {
    "match:run": "run the 6-agent crew and re-rank the decision matrix",
    "match:narrate": "ask Nemotron for a narrative on one home",
    "listings:import": "upload saved Zillow pages into the listing store",
    "intake:list": "see Home Match Toolkit submissions (contact + profile, never document contents)",
    "intake:kickoff": "mark a client's kickoff meeting done so their portal invite goes out",
    "intake:self": "see only their own questionnaire, profile and portal",
    "system:diagnostics": "architecture dashboard, failure injection, NeMo Gym results, raw Jev endpoint",
}


def allowed(role, permission):
    return permission in ROLES.get(role, {}).get("permissions", set())


def can_view(role, page):
    return page in ROLES.get(role, {}).get("pages", set())


# ------------------------------------------------------------------ Unity Catalog
def uc_grants(catalog=CATALOG_DEFAULT):
    """(group, statement) pairs. Clients never touch the secure schema; their own intake row is exposed through a row
    filter that matches current_user() to the client's email."""
    q = f"`{catalog}`"
    s = lambda name: f"{q}.`{name}`"  # noqa: E731
    adm, rea, cli = (ROLES[r]["group"] for r in ("admin", "realtor", "client"))
    out = [(g, f"GRANT USE CATALOG ON CATALOG {q} TO `{g}`") for g in (adm, rea, cli)]
    for layer in ("estatepulse_bronze", "estatepulse_silver", "estatepulse_gold", "estatepulse_secure"):
        out.append((adm, f"GRANT ALL PRIVILEGES ON SCHEMA {s(layer)} TO `{adm}`"))
    for layer in ("estatepulse_silver", "estatepulse_gold"):
        out += [(rea, f"GRANT USE SCHEMA ON SCHEMA {s(layer)} TO `{rea}`"), (rea, f"GRANT SELECT ON SCHEMA {s(layer)} TO `{rea}`")]
    out += [(rea, f"GRANT USE SCHEMA ON SCHEMA {s('estatepulse_secure')} TO `{rea}`"),
            (rea, f"GRANT SELECT ON TABLE {s('estatepulse_secure')}.`client_intake` TO `{rea}`"),
            (rea, f"GRANT READ VOLUME ON VOLUME {s('estatepulse_secure')}.`client_documents` TO `{rea}`"),
            (cli, f"GRANT USE SCHEMA ON SCHEMA {s('estatepulse_gold')} TO `{cli}`"),
            (cli, f"GRANT SELECT ON TABLE {s('estatepulse_gold')}.`jev_ready_listings` TO `{cli}`")]
    return out


def row_filter_sql(catalog=CATALOG_DEFAULT):
    """Clients may only read their own intake row; admins and realtors see all rows."""
    sec = f"`{catalog}`.`estatepulse_secure`"
    adm, rea, cli = (ROLES[r]["group"] for r in ("admin", "realtor", "client"))
    return [
        f"CREATE OR REPLACE FUNCTION {sec}.`client_intake_filter`(email STRING) RETURNS BOOLEAN "
        # is_member() = workspace-level groups (Databricks Free Edition); is_account_group_member() = account groups
        f"RETURN is_member('{adm}') OR is_member('{rea}') OR is_account_group_member('{adm}') OR is_account_group_member('{rea}') "
        f"OR lower(email) = lower(current_user())",
        f"ALTER TABLE {sec}.`client_intake` SET ROW FILTER {sec}.`client_intake_filter` ON (client_email)",
        f"GRANT USE SCHEMA ON SCHEMA {sec} TO `{cli}`",
        f"GRANT SELECT ON TABLE {sec}.`client_intake` TO `{cli}`",
        f"GRANT EXECUTE ON FUNCTION {sec}.`client_intake_filter` TO `{cli}`",
    ]


def describe():
    return [{"role": r, "label": v["label"], "group": v["group"], "home": v["home"], "pages": sorted(v["pages"]),
             "permissions": [{"name": p, "help": PERMISSION_HELP[p]} for p in sorted(v["permissions"])]} for r, v in ROLES.items()]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sql", action="store_true")
    ap.add_argument("--catalog", default=CATALOG_DEFAULT)
    a = ap.parse_args(argv)
    if a.sql:
        for _, stmt in uc_grants(a.catalog):
            print(stmt + ";")
        for stmt in row_filter_sql(a.catalog):
            print(stmt + ";")
    else:
        import json
        print(json.dumps(describe(), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def for_principals(group, statement, principals):
    """Re-target one group GRANT at individual users. Databricks Free Edition has workspace-level groups only, and
    Unity Catalog grants need an account-level principal, so each member of the workspace group is granted directly."""
    return [statement.replace(f"TO `{group}`", f"TO `{p}`") for p in principals]


def principal_missing(err):
    return "PRINCIPAL_DOES_NOT_EXIST" in str(err) or "Could not find principal" in str(err)
