"""Home Match Toolkit: questionnaire -> buyer profile + weights, secure document intake, portal sign-in links.

Flow
  1. Buyer fills the questionnaire at /start (financing, purchasing power, must-haves, 5 priority questions).
  2. POST /api/intake validates it, maps it to a buyer profile + the 10 metric weights (`to_profile`), and stores:
       * the record (contact, answers, profile; never the document contents) in data/intake/records/<id>.json
         and, when Databricks is configured, in workspace.estatepulse_secure.client_intake
       * the pre-approval letter / proof of funds in the Unity Catalog volume
         /Volumes/<catalog>/estatepulse_secure/client_documents/<id>/ (dev fallback: a private local folder, owner-only permissions)
  3. After the kickoff meeting an advisor marks the intake (POST /api/admin/intake/<id>/kickoff, X-Admin-Token).
  4. scripts/send_portal_invites.py emails the client a signed, expiring sign-in link to their portal. No password is
     ever generated or emailed.
"""
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED = {b"%PDF": ("application/pdf", ".pdf"), b"\x89PNG": ("image/png", ".png"), b"\xff\xd8\xff": ("image/jpeg", ".jpg")}
FINANCING = {"preapproved": "Pre-approved by a lender", "cash": "Paying cash", "need_lender": "Needs a lender recommendation"}
GOALS = {"cash_flow", "appreciation", "below_market"}
STYLES = {"urban", "suburban", "balanced"}
RENOVATION = {"move_in_ready", "light", "fixer"}
PRIORITIES = ("safety", "commute", "budget", "lifestyle", "property")
_LOCK = threading.Lock()
_DEV_SECRET = secrets.token_hex(32)


class IntakeError(ValueError):
    pass


def intake_dir():
    return os.getenv("INTAKE_DIR", os.path.join(ROOT, "data", "intake"))


# ------------------------------------------------------------------ validation + mapping
def _int(v, lo, hi, name, default=None):
    if v in (None, ""):
        if default is None:
            raise IntakeError(f"{name} is required")
        return default
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        raise IntakeError(f"{name} must be a number")
    if not lo <= n <= hi:
        raise IntakeError(f"{name} must be between {lo:,} and {hi:,}")
    return n


def validate(a):
    """Clean questionnaire answers. Raises IntakeError with a message the form can show."""
    name = (a.get("name") or "").strip()
    email = (a.get("email") or "").strip()
    if not name or len(name) > 120:
        raise IntakeError("name is required")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", email) or len(email) > 200:
        raise IntakeError("a valid email is required")
    if a.get("financing") not in FINANCING:
        raise IntakeError("choose a financing status")
    pr = a.get("priorities") or {}
    out = {
        "name": name, "email": email, "phone": (a.get("phone") or "").strip()[:40] or None,
        "financing": a["financing"],
        "max_price": _int(a.get("max_price"), 50_000, 20_000_000, "max purchase price"),
        "max_monthly_payment": _int(a.get("max_monthly_payment"), 300, 100_000, "desired monthly payment", default=0) or None,
        "down_payment_pct": _int(a.get("down_payment_pct"), 0, 100, "down payment %", default=20),
        "min_beds": _int(a.get("min_beds"), 0, 10, "min bedrooms", default=3),
        "min_baths": float(_int(a.get("min_baths"), 0, 10, "min bathrooms", default=2)),
        "areas": (a.get("areas") or "").strip()[:300],
        "priorities": {k: _int(pr.get(k), 0, 5, f"{k} priority", default=3) for k in PRIORITIES},
        "work_location": (a.get("work_location") or "").strip()[:200] or None,
        "max_commute_min": _int(a.get("max_commute_min"), 5, 180, "max commute", default=0) or None,
        "investment_goal": a.get("investment_goal") if a.get("investment_goal") in GOALS else "below_market",
        "living_style": a.get("living_style") if a.get("living_style") in STYLES else "balanced",
        "renovation_tolerance": a.get("renovation_tolerance") if a.get("renovation_tolerance") in RENOVATION else "light",
        "max_hoa": _int(a.get("max_hoa"), 0, 5000, "max HOA", default=-1),
        "must_have_features": [str(f)[:40] for f in (a.get("must_have_features") or [])][:12],
        "notes": (a.get("notes") or "").strip()[:1000] or None,
    }
    if out["max_hoa"] < 0:
        out["max_hoa"] = None
    return out


def to_profile(q):
    """Validated answers -> the buyer profile + 10 metric weights the agents and matrix use."""
    p = q["priorities"]
    w = {"safety": p["safety"], "environment": round(p["safety"] * 0.6), "commute": p["commute"]}
    b = p["budget"]
    w.update({"below_market": {"deal": b, "forecast": round(b * 0.6)}, "appreciation": {"deal": round(b * 0.6), "forecast": b},
              "cash_flow": {"deal": b, "forecast": round(b * 0.8)}}[q["investment_goal"]])
    life = p["lifestyle"]
    w.update({"urban": {"urban": life, "school": round(life * 0.5), "lifestyle": life},
              "suburban": {"urban": round(life * 0.4), "school": life, "lifestyle": life},
              "balanced": {"urban": round(life * 0.8), "school": round(life * 0.8), "lifestyle": life}}[q["living_style"]])
    prop = p["property"]
    w.update({"move_in_ready": {"maintenance": prop, "rooms": prop}, "light": {"maintenance": round(prop * 0.7), "rooms": prop},
              "fixer": {"maintenance": round(prop * 0.3), "rooms": prop}}[q["renovation_tolerance"]])
    profile = {
        "max_price": q["max_price"], "down_payment_pct": q["down_payment_pct"], "min_beds": q["min_beds"],
        "min_baths": q["min_baths"], "location": q["areas"] or None, "max_monthly_payment": q["max_monthly_payment"],
        # a payment cap at the standard 28% front-end ratio implies the income the affordability math needs
        "annual_income": round(q["max_monthly_payment"] * 12 / 0.28) if q["max_monthly_payment"] else None,
        "commute_destination": q["work_location"] or "downtown raleigh", "max_commute_min": q["max_commute_min"],
        "urban_target": (q["work_location"] or "downtown raleigh") if q["living_style"] == "urban" else "cary",
        "min_school_rating": 7 if q["living_style"] == "suburban" else None,
        "max_hoa": q["max_hoa"], "risk_tolerance": "high" if q["investment_goal"] == "appreciation" else "medium",
        "must_have_features": q["must_have_features"], "financing": q["financing"],
        "weights": w,
    }
    profile["summary"] = (f"{FINANCING[q['financing']]}; up to ${q['max_price']:,}"
                          + (f" (max ${q['max_monthly_payment']:,}/mo)" if q["max_monthly_payment"] else "")
                          + f"; {q['min_beds']}+ bd / {q['min_baths']:g}+ ba" + (f" in {q['areas']}" if q["areas"] else "")
                          + f"; {q['living_style']} living, goal: {q['investment_goal'].replace('_', ' ')}")
    return {k: v for k, v in profile.items() if v not in (None, "", [])} | {"weights": w}


# ------------------------------------------------------------------ documents
def check_document(b64, filename):
    """Decode + validate an uploaded document. Returns (bytes, content_type, safe_name)."""
    try:
        data = base64.b64decode(b64, validate=True)
    except Exception:
        raise IntakeError("document upload is not valid base64")
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise IntakeError("document must be between 1 byte and 10 MB")
    for magic, (ctype, ext) in ALLOWED.items():
        if data.startswith(magic):
            stem = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.splitext(os.path.basename(filename or "document"))[0])[:60] or "document"
            return data, ctype, f"{stem}{ext}"
    raise IntakeError("document must be a PDF, PNG or JPEG")


def _dbx_configured():
    return bool(os.getenv("DATABRICKS_HOST") and os.getenv("DATABRICKS_TOKEN"))


def volume_path(intake_id, name):
    cat = os.getenv("DATABRICKS_CATALOG", "workspace")
    return f"/Volumes/{cat}/estatepulse_secure/client_documents/{intake_id}/{name}"


def _upload_volume(path, data, transport=None):
    if transport:
        return transport(path, data)
    host = os.environ["DATABRICKS_HOST"].rstrip("/")
    host = host if host.startswith("http") else "https://" + host
    req = urllib.request.Request(f"{host}/api/2.0/fs/files{urllib.parse.quote(path)}?overwrite=false", data=data, method="PUT",
                                 headers={"Authorization": f"Bearer {os.environ['DATABRICKS_TOKEN']}",
                                          "Content-Type": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status


def store_document(intake_id, data, name, transport=None):
    """Unity Catalog volume when Databricks is configured, else a private local folder (dev only)."""
    meta = {"name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    if transport or _dbx_configured():
        path = volume_path(intake_id, name)
        _upload_volume(path, data, transport)
        return {**meta, "stored_in": "databricks_volume", "path": path}
    d = os.path.join(intake_dir(), "files", intake_id)
    os.makedirs(d, mode=0o700, exist_ok=True)
    p = os.path.join(d, name)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return {**meta, "stored_in": "local_dev_storage", "path": os.path.relpath(p, ROOT)}


# ------------------------------------------------------------------ records
def _record_path(intake_id):
    if not re.fullmatch(r"[0-9a-f]{16}", intake_id or ""):
        raise IntakeError("bad intake id")
    return os.path.join(intake_dir(), "records", f"{intake_id}.json")


def save(rec):
    p = _record_path(rec["id"])
    os.makedirs(os.path.dirname(p), mode=0o700, exist_ok=True)
    with _LOCK:
        with open(p + ".tmp", "w") as f:
            json.dump(rec, f, indent=1)
        os.chmod(p + ".tmp", 0o600)
        os.replace(p + ".tmp", p)
    return rec


def load(intake_id):
    try:
        with open(_record_path(intake_id)) as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def all_records():
    d = os.path.join(intake_dir(), "records")
    out = []
    for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if n.endswith(".json"):
            with open(os.path.join(d, n)) as f:
                out.append(json.load(f))
    return out


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def create(answers, document=None, upload_transport=None, sink=None):
    """Validate + store one questionnaire submission. Returns the record (without document contents)."""
    q = validate(answers)
    doc_bytes = None
    if document and document.get("data"):
        if q["financing"] == "need_lender":
            raise IntakeError("no document needed when you want a lender recommendation")
        doc_bytes, ctype, name = check_document(document["data"], document.get("name"))
    rec = {"id": secrets.token_hex(8), "created_at": _now(), "answers": q, "profile": to_profile(q), "document": None,
           "status": "submitted", "kickoff_completed_at": None, "invite_sent_at": None,
           "showings_unlocked": q["financing"] in ("preapproved", "cash") and doc_bytes is not None}
    if doc_bytes is not None:
        rec["document"] = {**store_document(rec["id"], doc_bytes, name, upload_transport), "content_type": ctype,
                           "kind": "pre_approval_letter" if q["financing"] == "preapproved" else "proof_of_funds"}
    save(rec)
    (sink or log_to_databricks)(rec)
    return rec


def log_to_databricks(rec, client=None):
    """Best effort: one row per intake in workspace.estatepulse_secure.client_intake (no document contents)."""
    if not (client or all(os.getenv(k) for k in ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_WAREHOUSE_ID"))):
        return False
    try:
        from pipeline import databricks_sink as D
        c = client or D.Client()
        cat = D.q(D.catalog())
        c.sql(f"INSERT INTO {cat}.`estatepulse_secure`.`client_intake` (intake_id, created_at, client_name, client_email, financing, "
              "max_price, document_path, profile_json, status) VALUES (:id, current_timestamp(), :name, :email, :fin, CAST(:price AS INT), "
              ":doc, :profile, :status)",
              {"id": rec["id"], "name": rec["answers"]["name"], "email": rec["answers"]["email"], "fin": rec["answers"]["financing"],
               "price": str(rec["answers"]["max_price"]), "doc": (rec.get("document") or {}).get("path") or "",
               "profile": json.dumps(rec["profile"]), "status": rec["status"]})
        return True
    except Exception:
        return False


def mark_kickoff(intake_id, advisor):
    rec = load(intake_id)
    if not rec:
        return None
    rec.update(kickoff_completed_at=_now(), kickoff_advisor=(advisor or "advisor")[:80], status="kickoff_complete")
    return save(rec)


def public_view(rec):
    """What the client's own portal may show: no document path or hash, no internal fields."""
    a = rec["answers"]
    return {"id": rec["id"], "name": a["name"], "financing": FINANCING[a["financing"]], "status": rec["status"],
            "showings_unlocked": rec["showings_unlocked"], "document_received": bool(rec.get("document")),
            "kickoff_completed": bool(rec.get("kickoff_completed_at")), "profile": rec["profile"]}


# ------------------------------------------------------------------ portal sign-in links
def _secret():
    return (os.getenv("PORTAL_SECRET") or _DEV_SECRET).encode()


def portal_token(intake_id, ttl_days=7, now=None):
    exp = int((now or time.time()) + ttl_days * 86400)
    sig = hmac.new(_secret(), f"{intake_id}.{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{intake_id}.{exp}.{sig}"


def verify_token(token, now=None):
    """Returns the intake id for a valid, unexpired token, else None."""
    try:
        intake_id, exp, sig = (token or "").split(".")
        good = hmac.new(_secret(), f"{intake_id}.{exp}".encode(), hashlib.sha256).hexdigest()[:32]
        if hmac.compare_digest(sig, good) and int(exp) >= (now or time.time()):
            return intake_id
    except ValueError:
        pass
    return None
