"""Sign-in and sessions for the three RBAC roles (app/rbac.py).

Accounts come from the environment, never from the page source:
  ADMIN_EMAIL / ADMIN_PASSWORD, REALTOR_EMAIL / REALTOR_PASSWORD, CLIENT_EMAIL / CLIENT_PASSWORD
  (emails default to admin@ / realtor@ / client@estatepulse.com; an account with no password is disabled)
  or EP_USERS='[{"email": "...", "role": "realtor", "password": "..."}]' for more people.
Passwords are hashed in memory with PBKDF2 at startup. A client who filled in the questionnaire can also sign in with
the signed portal link from their email (POST /api/login/portal).

Sessions are an HMAC-signed, HttpOnly, SameSite=Lax cookie (SESSION_SECRET, else PORTAL_SECRET) that expires after
SESSION_HOURS (default 12).
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

from . import rbac

COOKIE = "ep_session"
_DEV_SECRET = secrets.token_hex(32)
_FAILS, _FAIL_LOCK = {}, threading.Lock()
MAX_FAILS, FAIL_WINDOW = 5, 900


def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)


def load_users(env=None):
    env = env if env is not None else os.environ
    raw = []
    for role in ("admin", "realtor", "client"):
        pw = env.get(f"{role.upper()}_PASSWORD")
        if pw:
            raw.append({"email": env.get(f"{role.upper()}_EMAIL", f"{role}@estatepulse.com"), "role": role, "password": pw,
                        "name": env.get(f"{role.upper()}_NAME", f"Demo {rbac.ROLES[role]['label'].split()[0]}")})
    try:
        raw += json.loads(env.get("EP_USERS") or "[]")
    except ValueError:
        pass
    users = {}
    for u in raw:
        if u.get("role") in rbac.ROLES and u.get("email") and u.get("password"):
            salt = secrets.token_bytes(16)
            users[u["email"].strip().lower()] = {"email": u["email"].strip().lower(), "role": u["role"], "salt": salt,
                                                  "hash": _hash(u["password"], salt), "name": u.get("name") or u["email"]}
    return users


USERS = load_users()


def reload_users():
    global USERS
    USERS = load_users()
    return USERS


def _locked(email):
    now = time.time()
    with _FAIL_LOCK:
        fails = [t for t in _FAILS.get(email, []) if now - t < FAIL_WINDOW]
        _FAILS[email] = fails
        return len(fails) >= MAX_FAILS


def _fail(email):
    with _FAIL_LOCK:
        _FAILS.setdefault(email, []).append(time.time())


def authenticate(email, password, role=None):
    """Returns the user dict or raises PermissionError with a message safe to show."""
    email = (email or "").strip().lower()
    if _locked(email):
        raise PermissionError("too many attempts; try again in 15 minutes")
    u = USERS.get(email)
    ok = bool(u) and hmac.compare_digest(_hash(password or "", u["salt"]), u["hash"])
    if not ok or (role and role != u["role"]):
        _fail(email)
        raise PermissionError("email, password or account type is incorrect")
    return {"email": u["email"], "role": u["role"], "name": u["name"]}


# ------------------------------------------------------------------ sessions
def _secret():
    return (os.getenv("SESSION_SECRET") or os.getenv("PORTAL_SECRET") or _DEV_SECRET).encode()


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def make_session(user, hours=None):
    hours = float(hours or os.getenv("SESSION_HOURS", "12"))
    body = _b64(json.dumps({"e": user["email"], "r": user["role"], "n": user.get("name", ""), "i": user.get("intake_id"),
                            "x": int(time.time() + hours * 3600)}).encode())
    return f"{body}.{_b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())}"


def read_session(value):
    try:
        body, sig = (value or "").split(".")
        good = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        d = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if d["x"] < time.time() or d["r"] not in rbac.ROLES:
            return None
        return {"email": d["e"], "role": d["r"], "name": d.get("n", ""), "intake_id": d.get("i")}
    except (ValueError, KeyError, TypeError):
        return None


def current_user(request):
    return read_session(request.cookies.get(COOKIE))


def set_cookie(response, request, user):
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(COOKIE, make_session(user), httponly=True, samesite="lax", secure=secure,
                        max_age=int(float(os.getenv("SESSION_HOURS", "12")) * 3600), path="/")
