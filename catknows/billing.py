"""Who may use the hosted tools: paid membership in the catnose community on Skool.

catknows hosted is billed through Skool (decided 2026-11-13): a Premium or VIP
tier in catnose unlocks it. Skool is the merchant of record, so there is no
checkout here and no payment data — just a list of who currently pays.

How the list gets here, and why it is not fetched from this box:

* The operator's own automation (one long-lived browser session, not this
  server) reads catnose's ordinary members page with the ``tiers=premium,vip``
  filter, on every join and otherwise hourly. No billing or payout endpoint, ever.
* It pushes the result over SSH with a key that can do nothing else
  (``command=`` in authorized_keys runs ``deploy/billing/receive.sh``). That
  script calls ``python -m catknows.billing receive``, which stores the list
  and runs the sync below.
* This server never asks Skool who pays. Each user's own stored session only
  tells us *which* Skool account they connected: the ``auth_token`` cookie is a
  JWT whose ``user_id`` is the member id on that list (verified 2026-11-14).

Who keeps access regardless: founders (accounts created before 2026-11-18 09:00 Berlin,
``founders.txt``), gift exceptions with an end date (sent along with the list),
and accounts without a connected Skool session (they cannot use any tool yet;
the check applies the moment they connect).

Nothing is ever deleted. A refused account keeps its login, its dashboard and
its stored session; the tools answer with a pointer to the community instead.

Safety: until ``CATKNOWS_KASSE_AB`` is set this is a dry run — the sync reports
how many accounts *would* be refused and refuses no one. And a list that is
older than 36 hours, incomplete, or suddenly empty never refuses anyone new;
accounts refused under the last good list stay refused.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

COMMUNITY_URL = "https://www.skool.com/cat-knows-1423/about"

# Shown to the AI client (and so to the person) when a tool is refused.
REFUSAL = (
    "catknows hosted runs on the paid tiers (Premium or VIP) of catnose, Niklas' "
    "community on Skool, and the Skool account connected here is not on one right "
    "now. Nothing was deleted: the catknows account, the login and the connected "
    "Skool session are all still there. Upgrade in catnose with the same Skool "
    "account and the tools work again within the hour: " + COMMUNITY_URL
)

MAX_LIST_AGE = timedelta(hours=36)
MAX_INPUT = 2 * 1024 * 1024
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def billing_dir() -> Path:
    """Root-owned (root:catknows 2750) under root-owned /var/lib: the receiver writes
    here as root, the MCP server only reads. Nothing in the path is the service
    user's, so it can neither swap the directory nor plant a link in it."""
    return Path(os.environ.get("CATKNOWS_BILLING_DIR", "/var/lib/catknows-billing"))


def armed() -> bool:
    """Is the paid check switched on? Missing means dry run."""
    return os.environ.get("CATKNOWS_KASSE_AB", "").strip().lower() in ("1", "true", "yes", "on")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# -- which Skool account is connected ------------------------------------------


def skool_user_id(cookie_header: str) -> str:
    """The Skool user id inside a stored cookie header, or "" if there is none.

    Read from the ``auth_token`` JWT's payload without verifying it: the cookie
    came out of this user's own encrypted session, and the id is only compared
    against a list — it grants nothing a forged value could abuse beyond the
    session it already sits in.
    """
    m = re.search(r"(?:^|;\s*)auth_token=([^;]+)", cookie_header or "")
    if not m:
        return ""
    parts = m.group(1).strip().split(".")
    if len(parts) != 3:
        return ""
    try:
        raw = parts[1] + "=" * (-len(parts[1]) % 4)
        uid = json.loads(base64.urlsafe_b64decode(raw)).get("user_id")
    except Exception:
        return ""
    return uid if isinstance(uid, str) and _ID.fullmatch(uid) else ""


# -- the stored state -----------------------------------------------------------


class State:
    """payers.json + founders.txt + blocked.json, as one snapshot."""

    def __init__(self, payers: dict | None, founders: set[str], blocked: dict[str, dict]):
        self.payers_doc = payers or {}
        self.founders = founders
        self.blocked = blocked

    @property
    def payers(self) -> dict[str, dict]:
        return self.payers_doc.get("payers") or {}

    def exception(self, subject: str, skool_id: str, now: datetime) -> bool:
        today = now.date().isoformat()
        for e in self.payers_doc.get("exceptions") or []:
            if (e.get("until") or "") < today:
                continue
            if (subject and e.get("account") == subject) or (skool_id and e.get("skool_id") == skool_id):
                return True
        return False

    def list_trusted(self, now: datetime) -> tuple[bool, str]:
        """May this list refuse anyone new? (ok, reason when not)."""
        d = self.payers_doc
        if not d:
            return False, "no payer list yet"
        if not self.founders:
            # An empty or unreadable founders.txt would refuse every founder at once
            # (measured 2026-11-14: a CRLF export filtered to zero lines). Never that.
            return False, "no founders list"
        if not d.get("trusted"):
            return False, "list not trusted: " + (d.get("reason") or "no reason given")
        t = _parse(d.get("list_time") or "")
        if t is None:
            return False, "list without a time"
        if now - t > MAX_LIST_AGE:
            return False, f"list older than {int(MAX_LIST_AGE.total_seconds() // 3600)} h"
        return True, ""

    def entitled(self, subject: str, skool_id: str, now: datetime) -> str:
        """Why this account may use the tools ("" = no reason found)."""
        if subject in self.founders:
            return "founder"
        if self.exception(subject, skool_id, now):
            return "exception"
        if skool_id and skool_id in self.payers:
            return "payer"
        return ""

    def refused(self, subject: str, skool_id: str, now: datetime) -> bool:
        if self.entitled(subject, skool_id, now):
            return False
        ok, _ = self.list_trusted(now)
        return True if ok else subject in self.blocked


def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_state(d: Path | None = None) -> State:
    d = d or billing_dir()
    payers = _read_json(d / "payers.json")
    try:
        founders = {l.strip() for l in (d / "founders.txt").read_text(encoding="utf-8").splitlines()
                    if _ID.fullmatch(l.strip())}
    except OSError:
        founders = set()
    blocked = (_read_json(d / "blocked.json") or {}).get("blocked") or {}
    return State(payers if isinstance(payers, dict) else None, founders, blocked)


_cache: dict[str, Any] = {"key": None, "state": None}


def _cached_state() -> State:
    """Re-read only when one of the three files changed (the check runs on every tool call)."""
    d = billing_dir()
    key = []
    for name in ("payers.json", "founders.txt", "blocked.json"):
        try:
            st = (d / name).stat()
            key.append((name, st.st_mtime_ns, st.st_size))
        except OSError:
            key.append((name, None, None))
    key = (str(d), tuple(key))
    if _cache["key"] != key:
        _cache["state"], _cache["key"] = load_state(d), key
    return _cache["state"]


def refusal(subject: str, skool_id: str, now: datetime | None = None) -> str:
    """The message for a refused tool call, or "" to go ahead. Never raises.

    Called by the MCP server before every tool that needs Skool. Dry run (switch
    off) always goes ahead. A broken state file goes ahead too: refusing a paying
    user because a file did not parse is the worse failure.
    """
    if not armed():
        return ""
    try:
        state = _cached_state()
        if state.refused(subject, skool_id, now or _now()):
            return REFUSAL
    except Exception as e:  # never take the tools down over the paid check
        print(f"catknows: billing check failed open — {type(e).__name__}", file=sys.stderr)
    return ""


# -- receiving the list -----------------------------------------------------------


def validate(doc: Any) -> dict:
    """The pushed list, checked and reduced to what we store. Raises ValueError."""
    if not isinstance(doc, dict):
        raise ValueError("not an object")
    t = _parse(doc.get("list_time") or "")
    if t is None:
        raise ValueError("list_time missing or not a time")
    payers = {}
    for uid, v in (doc.get("payers") or {}).items():
        if not _ID.fullmatch(str(uid)) or not isinstance(v, dict):
            raise ValueError("payer id malformed")
        payers[uid] = {"tier": str(v.get("tier") or "")[:20], "state": str(v.get("state") or "")[:20]}
    exceptions = []
    for e in doc.get("exceptions") or []:
        if not isinstance(e, dict) or not _DAY.fullmatch(str(e.get("until") or "")):
            raise ValueError("exception needs an until date")
        x = {"until": e["until"]}
        for k in ("account", "skool_id"):
            if e.get(k):
                if not _ID.fullmatch(str(e[k])):
                    raise ValueError("exception id malformed")
                x[k] = e[k]
        if len(x) == 1:
            raise ValueError("exception without account or skool_id")
        exceptions.append(x)
    return {"list_time": _iso(t), "trusted": bool(doc.get("trusted")), "reason": str(doc.get("reason") or "")[:300],
            "payers": payers, "exceptions": exceptions}


def _write(p: Path, data: dict) -> None:
    """Atomic write: random temp name created with O_EXCL (mkstemp), then rename.
    The group comes from the directory's setgid bit, so no chown is needed."""
    import tempfile

    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", dir=p.parent)
    try:
        os.fchmod(fd, 0o640)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def sync(state: State, accounts: Iterable[dict], skool_ids: dict[str, str], now: datetime,
         send_mail: Callable[[dict], None] | None = None) -> tuple[dict, dict]:
    """Decide for every account; returns (numbers, new blocked map).

    ``accounts``: Keycloak users ({id, email, email_verified}); ``skool_ids``:
    account id -> connected Skool user id ("" = session present, id unreadable;
    absent = not connected). Mails go out only when armed, once per refusal.
    """
    trusted, reason = state.list_trusted(now)
    accounts = list(accounts)
    n = {"armed": armed(), "list_trusted": trusted, "reason": reason, "list_time": state.payers_doc.get("list_time"),
         "accounts": len(accounts), "founders": 0, "exceptions": 0, "connected": 0, "connected_without_id": 0,
         "payers": len(state.payers), "payers_matched": 0, "would_refuse": 0, "refused": 0, "newly_refused": 0,
         "released": 0, "mails": 0, "mail_errors": 0}
    blocked = dict(state.blocked) if n["armed"] else {}
    for a in accounts:
        sub = a["id"]
        why = state.entitled(sub, skool_ids.get(sub, ""), now)
        n["founders"] += why == "founder"
        n["exceptions"] += why == "exception"
        if sub not in skool_ids:
            continue  # not connected: nothing to check until they connect
        n["connected"] += 1
        n["connected_without_id"] += not skool_ids[sub]
        n["payers_matched"] += why == "payer"
        if why:
            if blocked.pop(sub, None) is not None:
                n["released"] += 1
            continue
        if not trusted:
            continue  # an untrusted list refuses no one new; the already refused stay refused
        n["would_refuse"] += 1
        if not n["armed"] or sub in blocked:
            continue
        entry = {"since": _iso(now), "mailed": ""}
        if send_mail and a.get("email") and a.get("email_verified"):
            try:
                send_mail(a)
                entry["mailed"] = _iso(now)
                n["mails"] += 1
            except Exception as e:
                n["mail_errors"] += 1
                print(f"catknows: refusal mail failed — {type(e).__name__}", file=sys.stderr)
        blocked[sub] = entry
        n["newly_refused"] += 1
    known = {a["id"] for a in accounts}
    blocked = {k: v for k, v in blocked.items() if k in known}  # deleted accounts drop out
    n["refused"] = len(blocked)
    return n, blocked


# -- the box side: Keycloak, sessions, mail, rejected hosts ----------------------
# Only used by `receive`, which runs as root through the restricted SSH key.


# docker runs from a root process: a fixed, minimal environment, nothing inherited.
_SUBPROCESS_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"}


def _psql(sql: str) -> str:
    import subprocess

    db = os.environ.get("CATKNOWS_KC_DB_CONTAINER", "catknows-auth-db-1")
    r = subprocess.run(["docker", "exec", db, "psql", "-U", "keycloak", "-d", "keycloak", "-tAF", "\t", "-c", sql],
                       capture_output=True, text=True, timeout=60, env=_SUBPROCESS_ENV)
    if r.returncode:
        raise RuntimeError("keycloak db query failed")
    return r.stdout


_REALM = "(select id from realm where name = 'catknows')"


def keycloak_accounts() -> list[dict]:
    out = []
    for line in _psql(f"select id, coalesce(email, ''), email_verified from user_entity where realm_id = {_REALM} "
                      "and service_account_client_link is null").splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and _ID.fullmatch(parts[0]):
            out.append({"id": parts[0], "email": parts[1], "email_verified": parts[2] == "t"})
    return out


def connected_skool_ids(account_ids: Iterable[str]) -> dict[str, str]:
    from . import sessions

    out = {}
    for sub in account_ids:
        cookie = sessions.load(sub)
        if cookie:
            out[sub] = skool_user_id(cookie)
    return out


def _smtp_config() -> dict:
    cfg = {}
    for line in _psql(f"select name, value from realm_smtp_config where realm_id = {_REALM}").splitlines():
        k, _, v = line.partition("\t")
        cfg[k] = v
    if not cfg.get("host") or not cfg.get("from"):
        raise RuntimeError("realm has no smtp config")
    return cfg


MAIL_SUBJECT = "catknows hosted is paused for your account"
MAIL_BODY = """Hey :)

catknows hosted now runs on the paid tiers of catnose, my Skool community
(Premium or VIP). Your Skool account isn't on one of them right now, so the
tools are paused.

Nothing got deleted. Your catknows account, your login and your connected
Skool session are all still there.

To turn the tools back on, upgrade in catnose with the same Skool account you
connected to catknows. It works again within the hour:
{url}

Questions? Just reply to this mail.

Niklas
"""


def mailer() -> Callable[[dict], None]:
    """Send through the SMTP account Keycloak already uses for its own mails."""
    import smtplib
    import ssl
    from email.message import EmailMessage
    from email.utils import formataddr

    cfg = _smtp_config()

    def send(account: dict) -> None:
        m = EmailMessage()
        m["From"] = formataddr((cfg.get("fromDisplayName") or "catknows", cfg["from"]))
        m["To"] = account["email"]
        if cfg.get("replyTo"):
            m["Reply-To"] = cfg["replyTo"]
        m["Subject"] = MAIL_SUBJECT
        m.set_content(MAIL_BODY.format(url=COMMUNITY_URL))
        port = int(cfg.get("port") or (465 if cfg.get("ssl") == "true" else 587))
        ctx = ssl.create_default_context()
        cls = smtplib.SMTP_SSL if cfg.get("ssl") == "true" else smtplib.SMTP
        kw = {"context": ctx} if cls is smtplib.SMTP_SSL else {}
        with cls(cfg["host"], port, timeout=30, **kw) as s:
            if cfg.get("starttls") == "true" and cls is smtplib.SMTP:
                s.starttls(context=ctx)
            if cfg.get("auth") == "true":
                s.login(cfg.get("user") or "", cfg.get("password") or "")
            s.send_message(m, from_addr=cfg.get("envelopeFrom") or cfg["from"])

    return send


_TRUSTED_HOST = re.compile(r"KC-SERVICES01\d\d: UR[LI] '(https?://[^/']+)[^']*' doesn't match any trustedHost")


def rejected_hosts(since: str = "24h") -> list[str]:
    """Hosts Keycloak's Trusted Hosts policy turned away (client registration). Host names only."""
    import subprocess
    from urllib.parse import urlsplit

    kc = os.environ.get("CATKNOWS_KC_CONTAINER", "catknows-auth-keycloak-1")
    r = subprocess.run(["docker", "logs", "--since", since, kc], capture_output=True, text=True, timeout=60,
                       env=_SUBPROCESS_ENV)
    return sorted({urlsplit(m.group(1)).hostname or "" for m in _TRUSTED_HOST.finditer(r.stdout + r.stderr)} - {""})


def _check_dir(d: Path) -> None:
    """As root, write only into a real, root-owned directory (install.sh creates it)."""
    st = os.lstat(d)
    import stat as _stat
    if not _stat.S_ISDIR(st.st_mode):
        raise RuntimeError(f"{d} must be a real directory (deploy/billing/install.sh creates it)")
    if os.geteuid() == 0 and (st.st_uid != 0 or st.st_mode & 0o002):
        raise RuntimeError(f"{d} must be root-owned and not world-writable")


def receive(stdin=None, now: datetime | None = None) -> dict:
    """Store a pushed list, sync, report numbers (no names, no ids)."""
    stdin = stdin or sys.stdin
    raw = stdin.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        raise ValueError("input too large")
    doc = validate(json.loads(raw))
    now = now or _now()
    d = billing_dir()
    _check_dir(d)
    doc["received"] = _iso(now)
    _write(d / "payers.json", doc)
    state = load_state(d)
    accounts = keycloak_accounts()
    send = None
    if armed():
        try:
            send = mailer()
        except Exception as e:
            print(f"catknows: no mailer — {type(e).__name__}", file=sys.stderr)
    numbers, blocked = sync(state, accounts, connected_skool_ids(a["id"] for a in accounts), now, send)
    if armed() or (d / "blocked.json").exists():
        _write(d / "blocked.json", {"updated": _iso(now), "blocked": blocked})
    try:
        numbers["rejected_hosts"] = rejected_hosts()
    except Exception:
        numbers["rejected_hosts"] = None
    return numbers


# Never a directory to write into (CATKNOWS_BILLING_DIR): the file belongs to the service user.
ENV_KEYS = ("CATKNOWS_KASSE_AB", "CATKNOWS_SESSION_DIR", "CATKNOWS_SESSION_KEY")


def load_env_file(path: str, keys: Iterable[str] = ENV_KEYS) -> None:
    """The few KEY=value lines `receive` needs from the service's env file, without overriding what is set.

    The forced command runs as root outside systemd, but must see the same
    switch (CATKNOWS_KASSE_AB) and the same session key as the MCP server.
    Only these keys: the file belongs to the service user, and a line like
    LD_PRELOAD= must never reach the root process or the docker calls it makes.
    """
    keys = set(keys)
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        if k in keys:
            os.environ.setdefault(k, v)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 3 and argv[0] == "receive" and argv[1] == "--env-file":
        load_env_file(argv[2])
        argv = ["receive"]
    if argv == ["receive"]:
        try:
            print(json.dumps(receive(), sort_keys=True))
            return 0
        except Exception as e:
            print(json.dumps({"error": f"{type(e).__name__}: {e}"[:300]}))
            return 1
    if argv == ["self-check"]:
        _self_check()
        return 0
    print("usage: python -m catknows.billing receive|self-check", file=sys.stderr)
    return 2


def _jwt(payload: dict) -> str:
    enc = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")  # noqa: E731
    return f"{enc(b'{}')}.{enc(json.dumps(payload).encode())}.sig"


def _self_check() -> None:
    """python -m catknows.billing self-check — the decisions, without a box."""
    import io
    import tempfile

    now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    cookie = lambda uid: f"aws-waf-token=x; auth_token={_jwt({'user_id': uid, 'exp': 1})}; other=1"  # noqa: E731

    assert skool_user_id(cookie("u-pay")) == "u-pay"
    assert skool_user_id("auth_token=garbage") == "" and skool_user_id("") == ""
    assert skool_user_id(cookie("../etc")) == "", "an id that is not an id must not come through"

    fresh = {"list_time": _iso(now - timedelta(hours=2)), "trusted": True, "payers": {"u-pay": {"state": "active"}},
             "exceptions": [{"account": "acc-gift", "until": "2027-11-01"}, {"skool_id": "u-old", "until": "2026-11-01"}]}
    st = State(fresh, {"acc-founder"}, {})
    assert not State(fresh, set(), {}).refused("x", "u-none", now), "no founders list refuses no one new"
    assert st.entitled("acc-founder", "", now) == "founder"
    assert st.entitled("acc-gift", "", now) == "exception"
    assert st.entitled("x", "u-old", now) == "", "an expired exception grants nothing"
    assert st.entitled("x", "u-pay", now) == "payer"
    assert st.refused("x", "u-none", now) and not st.refused("x", "u-pay", now)

    # Safety: an old, untrusted or missing list refuses no one new, but keeps who was refused.
    for doc in ({**fresh, "list_time": _iso(now - timedelta(hours=37))}, {**fresh, "trusted": False}, None):
        s2 = State(doc, {"acc-founder"}, {"acc-was": {"since": "x"}})
        assert not s2.refused("acc-new", "u-none", now), doc
        assert s2.refused("acc-was", "u-none", now), "already refused stays refused"
        assert not s2.refused("acc-was", "u-pay", now) or doc is None, "a payer is let back in"

    # The switch: off means nobody is refused at the tool call.
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CATKNOWS_BILLING_DIR"] = tmp
        Path(tmp, "payers.json").write_text(json.dumps(fresh))
        Path(tmp, "founders.txt").write_text("acc-founder\r\n")
        os.environ.pop("CATKNOWS_KASSE_AB", None)
        assert refusal("acc-new", "u-none", now) == "", "dry run refuses no one"
        os.environ["CATKNOWS_KASSE_AB"] = "1"
        assert refusal("acc-new", "u-none", now) == REFUSAL
        assert refusal("acc-new", "u-pay", now) == ""
        Path(tmp, "payers.json").write_text("{broken")
        assert refusal("acc-new", "u-none", now) == "", "a broken file fails open"
        os.environ.pop("CATKNOWS_KASSE_AB", None)

    # sync: numbers, mails once, release on payment, deleted accounts drop out.
    accounts = [{"id": "acc-founder", "email": "f@x", "email_verified": True},
                {"id": "acc-pay", "email": "p@x", "email_verified": True},
                {"id": "acc-churn", "email": "c@x", "email_verified": True},
                {"id": "acc-unverified", "email": "u@x", "email_verified": False},
                {"id": "acc-loose", "email": "l@x", "email_verified": True}]
    ids = {"acc-founder": "u-f", "acc-pay": "u-pay", "acc-churn": "u-gone", "acc-unverified": "u-gone2"}
    sent = []
    os.environ.pop("CATKNOWS_KASSE_AB", None)
    n, b = sync(State(fresh, {"acc-founder"}, {}), accounts, ids, now, sent.append)
    assert (n["would_refuse"], n["newly_refused"], n["connected"], n["founders"], b, sent) == (2, 0, 4, 1, {}, []), n
    os.environ["CATKNOWS_KASSE_AB"] = "1"
    n, b = sync(State(fresh, {"acc-founder"}, {}), accounts, ids, now, sent.append)
    assert set(b) == {"acc-churn", "acc-unverified"} and [a["id"] for a in sent] == ["acc-churn"], (b, sent)
    assert n["mails"] == 1 and n["newly_refused"] == 2
    n, b2 = sync(State(fresh, {"acc-founder"}, b), accounts, ids, now, sent.append)
    assert n["newly_refused"] == 0 and len(sent) == 1, "one mail per refusal, not per sync"
    paid = {**fresh, "payers": {**fresh["payers"], "u-gone": {}}}
    n, b3 = sync(State(paid, {"acc-founder"}, b2), accounts, ids, now, sent.append)
    assert "acc-churn" not in b3 and n["released"] == 1
    n, b4 = sync(State({**fresh, "trusted": False}, {"acc-founder"}, {}), accounts, ids, now, sent.append)
    assert n["newly_refused"] == 0 and b4 == {}, "an untrusted list refuses no one new"
    n, b5 = sync(State(fresh, {"acc-founder"}, {"acc-deleted": {}}), [], {}, now, sent.append)
    assert b5 == {}
    os.environ.pop("CATKNOWS_KASSE_AB", None)

    # validate: ids and dates are checked, nothing else is kept.
    for bad in ({"list_time": "x"}, {"list_time": _iso(now), "payers": {"../x": {}}},
                {"list_time": _iso(now), "exceptions": [{"account": "a"}]},
                {"list_time": _iso(now), "exceptions": [{"until": "2027-01-01"}]}):
        try:
            validate(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")
    v = validate({**fresh, "payers": {"u-pay": {"state": "active", "name": "leak"}}})
    assert "leak" not in json.dumps(v)
    try:
        receive(io.StringIO("x" * (MAX_INPUT + 1)))
    except ValueError:
        pass
    else:
        raise AssertionError("oversized input accepted")

    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp, "env")
        env.write_text("# comment\nCATKNOWS_TEST_A=1\nCATKNOWS_TEST_B='x=y'\nbad line\nPATH=/nope\nLD_PRELOAD=/x\n")
        load_env_file(str(env), keys=("CATKNOWS_TEST_A", "CATKNOWS_TEST_B", "PATH"))
        assert os.environ.pop("CATKNOWS_TEST_A") == "1" and os.environ.pop("CATKNOWS_TEST_B") == "x=y"
        assert os.environ["PATH"] != "/nope", "the env file never overrides what is set"
        load_env_file(str(env))
        assert "LD_PRELOAD" not in os.environ, "only the billing keys are read"
        # The temp file of a root write cannot be redirected by a planted symlink.
        target = Path(tmp, "victim"); target.write_text("keep")
        Path(tmp, "payers.json.tmp").symlink_to(target)
        _write(Path(tmp, "payers.json"), {"a": 1})
        assert target.read_text() == "keep" and json.loads(Path(tmp, "payers.json").read_text()) == {"a": 1}

    m = _TRUSTED_HOST.search("WARN KC-SERVICES0102: URL 'https://agent.meta.ai/' doesn't match any trustedHost")
    assert m and m.group(1) == "https://agent.meta.ai"
    print("billing self-check OK (mapping, entitlement, dry run, safety, mails once, validation)")


if __name__ == "__main__":
    raise SystemExit(main())
