"""Replace the sample neighborhood numbers with real, free public data.

    python -m pipeline.reference                 # schools + walkability + Wake County price/sqft -> data/
    python -m pipeline.reference --skip-schools  # reuse data/reference/nc_schools.json (the DPI file is ~100 MB)

Writes
  data/reference/nc_schools.json   every open NC public school: location (NCES) + % at grade level (NC DPI)
  data/neighborhoods.json          school_rating, walk_score and (Wake County) median_ppsf from real data, each with a
                                   *_source field; values that could not be fetched keep their old number, marked "sample"
"""
import argparse
import json
import os
import sys

from .config import DATA_DIR
from .sources import free_data as F

REF_DIR = os.path.join(DATA_DIR, "reference")
SCHOOLS_PATH = os.path.join(REF_DIR, "nc_schools.json")
HOODS_PATH = os.path.join(DATA_DIR, "neighborhoods.json")
WAKE_CITIES = {"Raleigh", "Cary", "Apex", "Wake Forest", "Holly Springs", "Garner", "Morrisville"}


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(path + ".tmp", path)


def build_schools(get=None, download=None):
    locs = F.nces_nc_schools(get=get)
    prof = F.ncdpi_proficiency((download or F.download)(F.NCDPI_ASSESSMENT_URL))
    rows = F.join_schools(locs, prof)
    _write(SCHOOLS_PATH, {"source": {"locations": F.NCES_SCHOOLS_URL, "proficiency": F.NCDPI_ASSESSMENT_URL},
                          "schools": rows, "matched": len(rows), "nces_total": len(locs), "dpi_total": len(prof)})
    return rows


def load_schools():
    try:
        with open(SCHOOLS_PATH) as f:
            return json.load(f)["schools"]
    except (OSError, ValueError, KeyError):
        return []


def update_neighborhoods(schools, get=None, hoods_path=HOODS_PATH):
    with open(hoods_path) as f:
        hoods = json.load(f)
    log = {}
    for h in hoods:
        got = []
        sr = F.nearby_school_rating(h["lat"], h["lon"], schools) if schools else None
        if sr:
            h.update(school_rating=sr["school_rating"], school_glp_pct=sr["glp_pct"], schools=sr["schools"], school_source=sr["source"])
            got.append("schools")
        else:
            h.setdefault("school_source", "sample")
        try:
            w = F.epa_walkability(h["lat"], h["lon"], get=get)
        except Exception:
            w = None
        if w:
            h.update(walk_score=w["walk_score"], walk_index=w["walk_index"], walk_source="EPA National Walkability Index")
            got.append("walkability")
        else:
            h.setdefault("walk_source", "sample")
        if h["city"] in WAKE_CITIES:
            try:
                p = F.wake_median_ppsf(h["lat"], h["lon"], get=get)
            except Exception:
                p = None
            if p:
                h.update(median_ppsf=p["median_ppsf"], ppsf_sales=p["sales"], ppsf_source=f"Wake County sales since {p['since']}")
                got.append("price/sqft")
        h.setdefault("ppsf_source", "sample")
        log[h["name"]] = got
    _write(hoods_path, hoods)
    return log


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-schools", action="store_true")
    a = ap.parse_args(argv)
    schools = load_schools() if a.skip_schools else build_schools()
    print(json.dumps({"schools_with_proficiency": len(schools), "neighborhoods": update_neighborhoods(schools)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
