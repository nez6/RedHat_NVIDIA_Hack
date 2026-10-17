"""Run the ingestion pipeline: python -m pipeline.run [--strict] [--only fred|police|environment]

Fetch -> validate -> transform -> atomically write data/live/*.json.
A source that fails or breaks its contract keeps its last good snapshot; the manifest records why.
"""
import argparse, datetime as dt, json, os, sys, time

from . import transform, validate
from .config import DATA_DIR, LIVE_DIR, LOOKBACK_DAYS
from .sources import environment, fred, police


def _write(name, obj):
    os.makedirs(LIVE_DIR, exist_ok=True)
    path = os.path.join(LIVE_DIR, name)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, default=str)
    os.replace(tmp, path)  # atomic swap so the app never reads a half-written file


def _hoods():
    with open(os.path.join(DATA_DIR, "neighborhoods.json")) as f:
        return json.load(f)


def run_fred(manifest):
    t0 = time.time()
    data, errors = fred.fetch_all()
    problems = validate.check_fred(data)
    _write("fred.json", {"fetched_at": manifest["run_at"], "series": data})
    manifest["sources"]["fred"] = {"status": "ok" if not errors else "partial", "errors": errors,
                                   "warnings": problems, "series": len(data), "secs": round(time.time() - t0, 2),
                                   "mortgage_30y": data["mortgage_30y"]["value"]}


def run_police(manifest):
    t0 = time.time()
    hoods = _hoods()
    safety, errors = {}, {}
    try:
        incidents = police.fetch_raleigh()
        safety.update(transform.raleigh_profiles(hoods, incidents))
        manifest["sources"].setdefault("police", {})["raleigh_records"] = len(incidents)
    except Exception as e:
        errors["raleigh"] = str(e)[:200]
    try:
        safety.update(transform.cary_profiles(hoods, police.fetch_cary()))
    except Exception as e:
        errors["cary"] = str(e)[:200]
    if not safety:
        raise validate.ContractError(f"all police feeds failed: {errors}")
    problems = validate.check_safety(safety, [h["name"] for h in hoods])
    for name, p in safety.items():
        p["jev_state"] = transform.jev_state(name, p, LOOKBACK_DAYS)
    _write("safety.json", {"fetched_at": manifest["run_at"], "lookback_days": LOOKBACK_DAYS, "neighborhoods": safety})
    manifest["sources"].setdefault("police", {}).update({"status": "ok" if not errors else "partial", "errors": errors,
                                          "warnings": problems, "neighborhoods": len(safety),
                                          "secs": round(time.time() - t0, 2)})


def run_environment(manifest):
    t0 = time.time()
    hoods = _hoods()
    env, errors = transform.environment_profiles(hoods, environment.fetch_neighborhood)
    problems = validate.check_environment(env, [h["name"] for h in hoods])
    _write("environment.json", {"fetched_at": manifest["run_at"], "neighborhoods": env})
    manifest["sources"]["environment"] = {"status": "ok" if not errors else "partial", "errors": errors,
                                          "warnings": problems, "neighborhoods": len(env), "secs": round(time.time() - t0, 2)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="exit 1 if any source fails (for CI)")
    ap.add_argument("--only", choices=["fred", "police", "environment"])
    ap.add_argument("--no-databricks", action="store_true", help="skip the Unity Catalog sync even if configured")
    args = ap.parse_args(argv)
    manifest = {"run_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "sources": {}}
    failed = False
    for name, fn in (("fred", run_fred), ("police", run_police), ("environment", run_environment)):
        if args.only and args.only != name:
            continue
        try:
            fn(manifest)
        except Exception as e:  # keep last good snapshot
            failed = True
            manifest["sources"].setdefault(name, {}).update({"status": "failed", "error": str(e)[:300]})
    if not args.only and not args.no_databricks:
        from . import databricks_sink
        if databricks_sink.configured():
            try:
                manifest["sources"]["databricks"] = {"status": "ok", **databricks_sink.sync()}
            except Exception as e:
                failed = True
                manifest["sources"]["databricks"] = {"status": "failed", "error": str(e)[:300]}
    prev = {}
    try:
        with open(os.path.join(LIVE_DIR, "manifest.json")) as f:
            prev = json.load(f)
    except Exception:
        pass
    merged = {"run_at": manifest["run_at"], "sources": {**prev.get("sources", {}), **manifest["sources"]}}
    _write("manifest.json", merged)
    print(json.dumps(merged, indent=1))
    return 1 if (failed and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
