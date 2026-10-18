"""Home Match Toolkit: questionnaire mapping, document checks, storage, portal links, kickoff + invites."""
import base64
import importlib.util
import os

import pytest
from fastapi.testclient import TestClient

from app import intake as I
from app.server import app

client = TestClient(app)
PDF = base64.b64encode(b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n").decode()
ANSWERS = {"name": "Ada Lovelace", "email": "ada@example.com", "financing": "preapproved", "max_price": 550000,
           "max_monthly_payment": 3500, "min_beds": 3, "min_baths": 2, "areas": "Cary, 27513",
           "priorities": {"safety": 5, "commute": 4, "budget": 3, "lifestyle": 2, "property": 1},
           "work_location": "RTP", "max_commute_min": 30, "investment_goal": "appreciation", "living_style": "suburban",
           "renovation_tolerance": "fixer", "max_hoa": 150, "must_have_features": ["fenced yard"]}


def test_questionnaire_maps_to_profile_and_all_ten_weights():
    p = I.to_profile(I.validate(ANSWERS))
    assert set(p["weights"]) == {"lifestyle", "school", "safety", "environment", "urban", "commute", "forecast", "deal", "rooms", "maintenance"}
    w = p["weights"]
    assert (w["safety"], w["environment"], w["commute"]) == (5, 3, 4)
    assert (w["forecast"], w["deal"]) == (3, 2)                 # appreciation leans on the forecast
    assert (w["school"], w["urban"], w["lifestyle"]) == (2, 1, 2)    # suburban: schools over city buzz
    assert p["min_school_rating"] == 7 and p["urban_target"] == "cary"
    assert (w["maintenance"], w["rooms"]) == (0, 1)             # fixer-upper: condition barely matters
    assert p["annual_income"] == 150000 and p["location"] == "Cary, 27513" and p["risk_tolerance"] == "high"


@pytest.mark.parametrize("bad,msg", [({"email": "nope"}, "email"), ({"financing": "loan"}, "financing"),
                                     ({"max_price": 10}, "max purchase price"), ({"name": ""}, "name")])
def test_validation_messages(bad, msg):
    with pytest.raises(I.IntakeError, match=msg):
        I.validate({**ANSWERS, **bad})


def test_document_must_be_pdf_png_or_jpeg_and_small():
    data, ctype, name = I.check_document(PDF, "../../etc/My Letter!.pdf")
    assert ctype == "application/pdf" and name == "My_Letter_.pdf"
    with pytest.raises(I.IntakeError):
        I.check_document(base64.b64encode(b"MZ\x90\x00 not a pdf").decode(), "x.exe")
    with pytest.raises(I.IntakeError):
        I.check_document("%%%", "x.pdf")


def test_documents_go_to_the_databricks_volume_when_configured():
    seen = {}
    rec = I.create(ANSWERS, {"name": "letter.pdf", "data": PDF}, upload_transport=lambda path, data: seen.update(path=path, n=len(data)),
                   sink=lambda r: None)
    assert seen["path"] == f"/Volumes/workspace/estatepulse_secure/client_documents/{rec['id']}/letter.pdf"
    assert rec["document"]["stored_in"] == "databricks_volume" and rec["showings_unlocked"]
    assert "data" not in rec["document"]


def test_local_fallback_is_private_and_record_never_holds_document_bytes():
    rec = I.create({**ANSWERS, "financing": "cash"}, {"name": "funds.pdf", "data": PDF}, sink=lambda r: None)
    path = os.path.join(I.ROOT, rec["document"]["path"])
    assert rec["document"]["stored_in"] == "local_dev_storage" and oct(os.stat(path).st_mode & 0o777) == "0o600"
    assert PDF not in open(I._record_path(rec["id"])).read()


def test_portal_tokens_are_signed_and_expire():
    t = I.portal_token("0123456789abcdef", ttl_days=1, now=1_000_000)
    assert I.verify_token(t, now=1_000_100) == "0123456789abcdef"
    assert I.verify_token(t, now=1_000_000 + 2 * 86400) is None
    assert I.verify_token(t[:-1] + ("0" if t[-1] != "0" else "1"), now=1_000_100) is None
    assert I.verify_token("garbage") is None


def test_intake_api_portal_and_kickoff_flow(monkeypatch):
    r = client.post("/api/intake", json={"answers": ANSWERS, "document": {"name": "l.pdf", "data": PDF}})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["showings_unlocked"] and d["document_stored_in"] == "local_dev_storage"
    view = client.get("/api/portal", params={"token": d["portal_token"]}).json()
    assert view["name"] == "Ada Lovelace" and "path" not in str(view) and not view["kickoff_completed"]
    assert client.get("/api/portal", params={"token": "x.1.y"}).status_code == 401
    assert client.post(f"/api/admin/intake/{d['id']}/kickoff").status_code == 403       # no ADMIN_TOKEN configured
    monkeypatch.setenv("ADMIN_TOKEN", "s3cret")
    assert client.post(f"/api/admin/intake/{d['id']}/kickoff", headers={"X-Admin-Token": "wrong"}).status_code == 403
    ok = client.post(f"/api/admin/intake/{d['id']}/kickoff", headers={"X-Admin-Token": "s3cret"}, json={"advisor": "Natalie"})
    assert ok.status_code == 200 and ok.json()["status"] == "kickoff_complete"
    assert client.post("/api/intake", json={"answers": {**ANSWERS, "email": "bad"}}).status_code == 400


def test_invite_script_dry_run_lists_kicked_off_clients_without_passwords(capsys):
    rec = I.create(ANSWERS, None, sink=lambda r: None)
    I.mark_kickoff(rec["id"], "Natalie")
    spec = importlib.util.spec_from_file_location("invites", os.path.join(I.ROOT, "scripts", "send_portal_invites.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert rec["id"] in [r["id"] for r in mod.pending(I.all_records())]
    msg, link = mod.build_email(I.load(rec["id"]), "https://app.example.com", 7)
    assert I.verify_token(link.split("token=")[1]) == rec["id"] and "password" not in msg.get_content().lower()
    assert "550,000" not in msg.get_content()
    assert mod.main([]) == 0 and "dry run" in capsys.readouterr().out
