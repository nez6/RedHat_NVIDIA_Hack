"""Email each client a secure sign-in link to their Home Match portal after their kickoff meeting.

    python scripts/send_portal_invites.py            # dry run: prints the emails it would send
    python scripts/send_portal_invites.py --send     # sends over SMTP and records invite_sent_at

Picks up every intake whose kickoff is marked complete (POST /api/admin/intake/<id>/kickoff) and that has not been
invited yet. The email holds a signed link that expires (PORTAL_INVITE_DAYS, default 7). No password is created or
emailed, and the email never repeats the client's financial details.

Env: PORTAL_SECRET (shared with the app), PORTAL_BASE_URL, SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM.
Schedule it (cron, GitHub Actions, or `docker compose --profile jobs run --rm invites --send`) to run automatically.
"""
import argparse
import os
import smtplib
import sys
from email.message import EmailMessage

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app import intake as I  # noqa: E402


def pending(records):
    return [r for r in records if r.get("kickoff_completed_at") and not r.get("invite_sent_at")]


def build_email(rec, base_url, days):
    link = f"{base_url.rstrip('/')}/portal?token={I.portal_token(rec['id'], ttl_days=days)}"
    msg = EmailMessage()
    msg["Subject"] = "Your Home Match portal is ready"
    msg["From"] = os.getenv("SMTP_FROM", "EstatePulse AI <no-reply@example.com>")
    msg["To"] = rec["answers"]["email"]
    first = rec["answers"]["name"].split()[0]
    msg.set_content(
        f"Hi {first},\n\nThanks for meeting with us. Your Home Match portal is ready: it has your saved search, your "
        f"matched homes and the showings we unlock for you.\n\nSign in here (this link is just for you and expires in {days} days):\n"
        f"{link}\n\nIf you didn't expect this email, you can ignore it.\n\nEstatePulse AI")
    return msg, link


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="actually send (default is a dry run)")
    a = ap.parse_args(argv)
    if a.send and not os.getenv("PORTAL_SECRET"):
        print("PORTAL_SECRET must be set (and match the app's) so the links verify", file=sys.stderr)
        return 2
    base, days = os.getenv("PORTAL_BASE_URL", "http://localhost:8501"), int(os.getenv("PORTAL_INVITE_DAYS", "7"))
    todo = pending(I.all_records())
    smtp = None
    if a.send and todo:
        smtp = smtplib.SMTP(os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "587")), timeout=30)
        smtp.starttls()
        if os.getenv("SMTP_USER"):
            smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
    for rec in todo:
        msg, _ = build_email(rec, base, days)
        if smtp:
            smtp.send_message(msg)
            rec.update(invite_sent_at=I._now(), status="invited")
            I.save(rec)
            print(f"sent: {rec['id']} -> {msg['To']}")
        else:
            print(f"--- dry run: {rec['id']} -> {msg['To']}\n{msg.get_content()}")
    if smtp:
        smtp.quit()
    print(f"{len(todo)} invite(s) {'sent' if a.send else 'pending (dry run)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
