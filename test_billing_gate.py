"""Self-check: the paid check sits in front of every Skool tool on the hosted server.

catknows hosted is billed through the catnose community on Skool (2026-11-13).
`_get_client()` is the one door every Skool tool goes through, so the check
lives there: a refused account gets the community link as the tool's error,
and keeps its login, its dashboard and its stored session. The token check
(`may_use_service`) stays email-only on purpose — a 401 there would send the AI
client into a re-login loop instead of telling the person what to do.

Run: python test_billing_gate.py    (no network, no pytest)
"""

import base64
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet

tmp = tempfile.TemporaryDirectory()
os.environ["CATKNOWS_SESSION_DIR"] = str(Path(tmp.name, "sessions"))
os.environ["CATKNOWS_SESSION_KEY"] = Fernet.generate_key().decode()
os.environ["CATKNOWS_BILLING_DIR"] = str(Path(tmp.name, "billing"))
os.environ.pop("CATKNOWS_KASSE_AB", None)

from catknows import billing, mcp_server, sessions  # noqa: E402


def _cookie(uid):
    enc = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")  # noqa: E731
    return f"auth_token={enc(b'{}')}.{enc(json.dumps({'user_id': uid}).encode())}.s; aws-waf-token=w"


def _call_as(subject):
    mcp_server._subject = lambda: subject
    try:
        mcp_server._get_client()
        return ""
    except RuntimeError as e:
        return str(e)


now = datetime.now(timezone.utc)
Path(os.environ["CATKNOWS_BILLING_DIR"]).mkdir()
Path(os.environ["CATKNOWS_BILLING_DIR"], "payers.json").write_text(json.dumps({
    "list_time": (now - timedelta(hours=1)).isoformat(), "trusted": True,
    "payers": {"u-pay": {"state": "active"}}, "exceptions": []}))
Path(os.environ["CATKNOWS_BILLING_DIR"], "founders.txt").write_text("acc-founder\n")
for sub, uid in (("acc-pay", "u-pay"), ("acc-free", "u-free"), ("acc-founder", "u-f")):
    sessions.save(sub, _cookie(uid))
mcp_server._refresh_session_cookie = lambda subject: None  # no Chromium in a test

# Dry run: switch off, nobody is refused.
assert _call_as("acc-free") == "", "without CATKNOWS_KASSE_AB nobody is refused"

os.environ["CATKNOWS_KASSE_AB"] = "1"
assert _call_as("acc-pay") == "", "a payer goes through"
assert _call_as("acc-founder") == "", "a founder goes through"
msg = _call_as("acc-free")
assert msg == billing.REFUSAL and billing.COMMUNITY_URL in msg, msg
assert sessions.load("acc-free"), "a refusal must not touch the stored session"
assert "No Skool session stored" in _call_as("acc-nobody"), "not connected: the old message, not the paywall"

# A cached client is checked again on the next call: a churn reaches a long-lived process.
assert _call_as("acc-pay") == ""
p = Path(os.environ["CATKNOWS_BILLING_DIR"], "payers.json")
doc = json.loads(p.read_text())
doc["payers"] = {"u-other": {"state": "active"}}
p.write_text(json.dumps(doc))
assert _call_as("acc-pay") == billing.REFUSAL, "the cached client must be re-checked"

# The token check is unchanged: email only.
from catknows.auth_oauth import may_use_service  # noqa: E402
assert may_use_service({"email_verified": True}) == ""

os.environ.pop("CATKNOWS_KASSE_AB", None)
tmp.cleanup()
print("billing gate OK (dry run, payer, founder, refusal keeps the session, re-check of cached clients)")
