"""RBAC: three groups (admin = tester UI, realtor, client), server-side sessions, and the Unity Catalog grants."""
from fastapi.testclient import TestClient

from app import auth as A, intake as I, rbac as R
from app.server import app
from conftest import login
from test_intake import ANSWERS


def anon():
    return TestClient(app, follow_redirects=False)


def as_role(role):
    return login(TestClient(app, follow_redirects=False), role)


def test_catalog_defines_three_groups_with_least_privilege():
    assert {r: v["group"] for r, v in R.ROLES.items()} == {"admin": "estatepulse_admins", "realtor": "estatepulse_realtors",
                                                           "client": "estatepulse_clients"}
    assert R.ROLES["realtor"]["permissions"] < R.ROLES["admin"]["permissions"]
    for p in ("listings:import", "intake:list", "intake:kickoff", "system:diagnostics"):
        assert not R.allowed("client", p)
    assert not R.allowed("realtor", "system:diagnostics") and R.can_view("admin", "architecture")


def test_unity_catalog_grants_keep_clients_out_of_raw_and_secure_data():
    client_stmts = [q for g, q in R.uc_grants() if g == "estatepulse_clients"]
    assert not any("bronze" in q or "silver" in q or "VOLUME" in q for q in client_stmts)
    assert any("jev_ready_listings" in q for q in client_stmts)
    rf = " ".join(R.row_filter_sql())
    assert "current_user()" in rf and "SET ROW FILTER" in rf
    assert any("READ VOLUME" in q for g, q in R.uc_grants() if g == "estatepulse_realtors")


def test_login_rejects_wrong_password_wrong_role_and_locks_out():
    c = anon()
    assert c.post("/api/login", json={"email": "realtor@test.estatepulse", "password": "nope"}).status_code == 401
    assert c.post("/api/login", json={"email": "realtor@test.estatepulse", "password": "realtor-test-password", "role": "admin"}).status_code == 401
    for _ in range(5):
        c.post("/api/login", json={"email": "ghost@test.estatepulse", "password": "x"})
    r = c.post("/api/login", json={"email": "ghost@test.estatepulse", "password": "x"})
    assert "too many attempts" in r.json()["detail"]


def test_sessions_are_signed():
    tok = A.make_session({"email": "a@b.c", "role": "client"})
    assert A.read_session(tok)["role"] == "client"
    body, sig = tok.split(".")
    import base64, json
    forged = base64.urlsafe_b64encode(json.dumps({"e": "a@b.c", "r": "admin", "x": 9e12}).encode()).rstrip(b"=").decode()
    assert A.read_session(f"{forged}.{sig}") is None and A.read_session("garbage") is None


def test_signed_out_visitors_get_public_pages_only():
    c = anon()
    for path in ("/", "/start", "/value", "/portal", "/api/meta", "/api/health"):
        assert c.get(path).status_code == 200, path
    for path in ("/app", "/how", "/architecture"):
        r = c.get(path)
        assert r.status_code == 303 and "login=1" in r.headers["location"], path
    assert c.post("/api/match", json={}).status_code == 401
    assert c.post("/api/rescore", json={"ids": [1001], "weights": {}}).status_code == 401
    assert c.get("/api/intakes").status_code == 401 and c.get("/api/gym").status_code == 401
    assert c.get("/api/me").json() == {"signed_in": False}


def test_client_role():
    c = as_role("client")
    me = c.get("/api/me").json()
    assert me["role"] == "client" and me["group"] == "estatepulse_clients" and "architecture" not in me["pages"]
    assert c.get("/app").status_code == 200 and c.get("/architecture").status_code == 303 and c.get("/how").status_code == 303
    assert c.post("/api/zillow/import", json={"html": "<html></html>"}).status_code == 403
    assert c.get("/api/intakes").status_code == 403 and c.get("/api/gym").status_code == 403
    assert c.post("/api/rescore", json={"ids": [1001], "weights": {}}).status_code == 200


def test_realtor_role():
    c = as_role("realtor")
    rec = I.create(ANSWERS, None, sink=lambda r: None)
    rows = c.get("/api/intakes").json()["intakes"]
    assert any(r["id"] == rec["id"] for r in rows) and "path" not in str(rows)
    assert c.post(f"/api/admin/intake/{rec['id']}/kickoff", json={}).status_code == 200
    assert I.load(rec["id"])["kickoff_advisor"] == "realtor@test.estatepulse"
    assert c.get("/how").status_code == 200 and c.get("/architecture").status_code == 303
    assert c.get("/api/gym").status_code == 403 and c.get("/api/rbac").status_code == 403


def test_admin_role_sees_tester_ui_and_rbac_catalog():
    c = as_role("admin")
    assert c.get("/architecture").status_code == 200 and c.get("/app").status_code == 200
    d = c.get("/api/rbac").json()
    assert len(d["roles"]) == 3 and d["unity_catalog_grants"]


def test_client_can_sign_in_with_portal_link_and_sees_only_their_intake():
    rec = I.create({**ANSWERS, "email": "pat@example.com", "name": "Pat Buyer"}, None, sink=lambda r: None)
    c = anon()
    assert c.post("/api/login/portal", json={"token": "bad.1.x"}).status_code == 401
    assert c.post("/api/login/portal", json={"token": I.portal_token(rec["id"])}).status_code == 200
    me = c.get("/api/me").json()
    assert me["role"] == "client" and me["intake"]["id"] == rec["id"] and I.verify_token(me["portal_token"]) == rec["id"]
    assert c.post("/api/logout").status_code == 200


def test_portal_link_still_unlocks_matches_without_an_account():
    rec = I.create(ANSWERS, None, sink=lambda r: None)
    c = anon()
    assert c.get("/app", params={"token": I.portal_token(rec["id"])}).status_code == 200
    r = c.post("/api/rescore", json={"ids": [1001], "weights": {}}, headers={"X-Portal-Token": I.portal_token(rec["id"])})
    assert r.status_code == 200


def test_home_page_ships_no_passwords():
    html = anon().get("/").text
    assert "RealtorDemo2026" not in html and "ClientDemo2026" not in html and "Admin (tester)" in html


def test_free_edition_grants_fall_back_to_workspace_group_members():
    import importlib.util, os
    spec = importlib.util.spec_from_file_location("dbsetup", os.path.join(os.path.dirname(__file__), "..", "scripts", "databricks_setup.py"))
    S = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(S)
    ran = []

    class Warehouse:
        def sql(self, stmt):
            if "TO `estatepulse_" in stmt:  # Free Edition: workspace groups aren't UC principals
                raise RuntimeError("[PRINCIPAL_DOES_NOT_EXIST] Could not find principal with name estatepulse_admins")
            ran.append(stmt)

    members = {"estatepulse_admins": ["nat@example.com"], "estatepulse_realtors": ["agent@example.com"], "estatepulse_clients": []}
    setup, grants = S.statements("workspace")
    assert not any("CREATE GROUP" in s.upper() for s in setup + [g for _, g in grants])  # groups already exist
    report = S.apply_grants(Warehouse(), grants, members=lambda g: members[g])
    assert any("TO `nat@example.com`" in s and "ALL PRIVILEGES" in s for s in ran)
    assert any("TO `agent@example.com`" in s and "client_intake" in s for s in ran)
    assert {r["status"] for r in report if r["principal"] == "estatepulse_clients"} == {"workspace group has no members yet (add people, then re-run)"}
    assert "is_member('estatepulse_admins')" in " ".join(R.row_filter_sql())
