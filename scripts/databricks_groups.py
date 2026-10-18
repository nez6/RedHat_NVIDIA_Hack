"""Create the three Estate Pulse RBAC groups in your Databricks workspace (idempotent).

    export DATABRICKS_HOST=https://<workspace>.cloud.databricks.com DATABRICKS_TOKEN=<your token>
    python scripts/databricks_groups.py              # create estatepulse_admins / _realtors / _clients
    python scripts/databricks_groups.py --dry-run    # show what it would do

Add people to each group in Settings -> Identity and access -> Groups, then run the ELT job: its rbac task applies
the Unity Catalog grants from app/rbac.py.
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app import rbac  # noqa: E402

SCIM = "/api/2.0/preview/scim/v2/Groups"


def call(method, path, body=None, transport=None):
    if transport:
        return transport(method, path, body)
    host = os.environ["DATABRICKS_HOST"].rstrip("/")
    host = host if host.startswith("http") else "https://" + host
    req = urllib.request.Request(host + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {os.environ['DATABRICKS_TOKEN']}", "Content-Type": "application/scim+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "{}")


def ensure_groups(transport=None, dry_run=False):
    out = {}
    for role, spec in rbac.ROLES.items():
        name = spec["group"]
        found = call("GET", SCIM + "?filter=" + urllib.parse.quote(f'displayName eq "{name}"'), transport=transport)
        if found.get("Resources"):
            out[name] = "exists"
        elif dry_run:
            out[name] = "would create"
        else:
            call("POST", SCIM, {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"], "displayName": name}, transport=transport)
            out[name] = "created"
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    print(json.dumps(ensure_groups(dry_run=a.dry_run), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
