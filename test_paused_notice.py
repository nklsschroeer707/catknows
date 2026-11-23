"""Self-check: a signed-in account whose tools are paused is told so on the page.

Li Briki, 2026-11-23: new account, email confirmed, Skool connected, not on
Premium or VIP. The hourly check paused the tools and sent the mail, but the
page said nothing. The big buttons still read "Create my account", she clicked
them six times, met "email in use" and wrote "I can't log in".

What must hold now:
* /auth/status carries `paused` for exactly the accounts the tools refuse, and
  only while the check is armed and the list can be trusted. Founders, payers,
  gift exceptions, accounts without a Skool session: never. Dry run, an old or
  untrusted list, a broken file: no notice rather than a wrong one.
* The landing page has the notice and takes every signed-in visitor off the
  registration link.
* /connect says the same and shows no green dot for a paused account.

Run: python test_paused_notice.py    (no network, no pytest)
"""

import asyncio
import base64
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet

tmp = tempfile.TemporaryDirectory()
os.environ["CATKNOWS_SESSION_DIR"] = str(Path(tmp.name, "sessions"))
os.environ["CATKNOWS_SESSION_KEY"] = Fernet.generate_key().decode()
os.environ["CATKNOWS_BILLING_DIR"] = str(Path(tmp.name, "billing"))
os.environ.pop("CATKNOWS_KASSE_AB", None)

from catknows import billing, dashboard, sessions  # noqa: E402


def _cookie(uid):
    enc = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")  # noqa: E731
    return f"auth_token={enc(b'{}')}.{enc(json.dumps({'user_id': uid}).encode())}.s; aws-waf-token=w"


class Req:
    def __init__(self, sid=None):
        self.cookies = {dashboard.COOKIE: sid} if sid else {}
        self.query_params = {}


def _status(subject=None):
    sid = dashboard._new_session(subject, f"{subject}@x.y") if subject else None
    return json.loads(asyncio.run(dashboard.status(Req(sid))).body)


now = datetime.now(timezone.utc)
bdir = Path(os.environ["CATKNOWS_BILLING_DIR"])
bdir.mkdir()
fresh = {"list_time": (now - timedelta(hours=1)).isoformat(), "trusted": True,
         "payers": {"u-pay": {"state": "active"}},
         "exceptions": [{"account": "acc-gift", "until": "2099-01-01"}]}


def _list(doc):
    (bdir / "payers.json").write_text(doc if isinstance(doc, str) else json.dumps(doc))


_list(fresh)
(bdir / "founders.txt").write_text("acc-founder\n")
for sub, uid in (("acc-pay", "u-pay"), ("acc-free", "u-free"), ("acc-founder", "u-f"),
                 ("acc-gift", "u-g"), ("acc-was", "u-was")):
    sessions.save(sub, _cookie(uid))

# Dry run: nobody is told anything.
assert _status("acc-free")["paused"] is False, "dry run must show no notice"

os.environ["CATKNOWS_KASSE_AB"] = "1"

# Armed, fresh list: the notice follows the tools exactly.
free = _status("acc-free")
assert free["paused"] is True and free["signed_in"] and free["skool_connected"], free
assert billing.refusal("acc-free", "u-free") == billing.REFUSAL, "the notice and the tools must agree"
for sub in ("acc-pay", "acc-founder", "acc-gift"):
    assert _status(sub)["paused"] is False, f"{sub} must never see the notice"
assert _status("acc-nobody")["paused"] is False, "no Skool session: nothing is paused yet"
assert _status() == {"signed_in": False, "skool_connected": False, "paused": False}

# A list that cannot be trusted shows no notice, even for an account that was
# paused under the last good one: a wrong notice is worse than none.
(bdir / "blocked.json").write_text(json.dumps({"blocked": {"acc-was": {"since": "x"}}}))
for doc in ({**fresh, "list_time": (now - timedelta(hours=37)).isoformat()},
            {**fresh, "trusted": False}, "{broken"):
    _list(doc)
    for sub in ("acc-free", "acc-was"):
        assert _status(sub)["paused"] is False, (sub, doc)
_list(fresh)
(bdir / "founders.txt").write_text("")
assert _status("acc-free")["paused"] is False, "no founders list: no notice"
(bdir / "founders.txt").write_text("acc-founder\n")
assert _status("acc-was")["paused"] is True, "back on a good list the notice returns"

# The check itself failing must not take /auth/status down.
real = billing.paused
billing.paused = lambda *a, **k: 1 / 0
assert _status("acc-free")["paused"] is False
billing.paused = real

# -- the landing page ----------------------------------------------------------
page = (dashboard.WEB / "index.html").read_text()
note = re.search(r'<aside class="paused" id="paused-note" hidden>(.*?)</aside>', page, re.S)
assert note, "the landing page lost its paused notice (hidden until /auth/status says so)"
text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", note[1]))
for must in ("paused", "Premium or VIP", "catnose", "within the hour", "account", "login", "Skool connection"):
    assert must in text, f"the notice no longer says: {must}"
assert f'href="{billing.PLANS_URL}"' in note[1], "the button goes straight to Skool's plan picker"
assert f'href="{billing.COMMUNITY_URL}"' in note[1], "the notice needs the link to the community"
assert "—" not in note[1] and "–" not in note[1], "no dashes in Niklas' voice"
assert "st.paused" in page, "the page script must read `paused` from /auth/status"
# Signed in means the account exists: no branch of the script may leave a
# button on the registration form (before: only the not-connected branch did).
m = re.search(r"if \(!st \|\| !st\.signed_in\) return;(.*?)\.catch\(", page, re.S)
assert m, "account script not found"
script = m[1]
swap = script.index('a.cta[href*="register=1"]')
first_branch = min(i for i in (script.find("st.paused"), script.find("st.skool_connected")) if i >= 0)
assert swap < first_branch, "the registration buttons must be swapped for every signed-in visitor"
assert f"var plans = '{billing.PLANS_URL}'" in script, "paused buttons lead to the plan picker"

# -- /connect ------------------------------------------------------------------
html = dashboard._page_connect("a@b.c", stored=True, skool="Ada L.", cleared=True, paused=True)
assert 'class="dot on"' not in html, "no green dot while the tools are refused"
assert "paused" in html and billing.COMMUNITY_URL in html and "within the hour" in html
assert billing.PLANS_URL in html, "/connect links the plan picker too"
ok = dashboard._page_connect("a@b.c", stored=True, skool="Ada L.", cleared=True)
assert 'class="dot on"' in ok and billing.COMMUNITY_URL not in ok, "a payer's page is unchanged"

os.environ.pop("CATKNOWS_KASSE_AB", None)
tmp.cleanup()
print("paused notice OK (status follows the tools, safe when unsure, landing page, /connect)")
