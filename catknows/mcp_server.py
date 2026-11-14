"""MCP server: plug your Skool data into any AI (Claude, Codex, Cursor, ...).

    catknows-mcp        # or: python -m catknows.mcp_server

Exposes the SkoolClient endpoints as MCP tools over stdio. Register once, e.g.:

    claude mcp add catknows -- python -m catknows.mcp_server

The first tool call triggers the browser login (a window opens once); the
session persists in ~/.catknows/skool-profile so every later call runs
silently. Set CATKNOWS_COOKIE to a raw Cookie header to skip the browser
entirely (headless machines), or CATKNOWS_PROFILE_DIR to move the profile.

Speed knobs: reads are cached in-process for CATKNOWS_CACHE_TTL seconds
(default 600, 0 disables; chat channels and notifications never cached,
writes clear it), and
CATKNOWS_PAGE_DELAY tunes the politeness pause between paginated requests
(default 0.8 s — lowering it is your 403 risk).

ᓚᘏᗢ
"""

from __future__ import annotations

import mimetypes
import functools
import inspect
import json
import os
import sys
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from typing import Any

try:  # MCP SDK 2.x
    from mcp.server.mcpserver import MCPServer
except ImportError:  # MCP SDK 1.x called the same thing FastMCP
    from mcp.server.fastmcp import FastMCP as MCPServer

from . import audit, normalize, sessions, vault


def _auth_kwargs() -> dict[str, Any]:
    """OAuth wiring, or nothing at all when CATKNOWS_OAUTH_ISSUER is unset.

    Off by default so `claude mcp add catknows -- python -m catknows.mcp_server`
    keeps working: over stdio the client is already the user. It's the hosted
    HTTP endpoint that needs to ask who's calling.
    """
    from .auth_oauth import verifier_from_env

    verifier = verifier_from_env()
    if verifier is None:
        return {}

    from mcp.server.auth.settings import AuthSettings

    return {
        "token_verifier": verifier,
        # Serves the 401 discovery document (RFC 9728) that tells a client
        # where to authenticate — without it clients get a bare 401 and no
        # way to find the authorization server.
        "auth": AuthSettings(
            issuer_url=verifier.issuer,
            resource_server_url=verifier.audience,
            required_scopes=[verifier.required_scope] if verifier.required_scope else [],
        ),
    }


mcp = MCPServer("catknows", **_auth_kwargs())

# The write-audit log (board D8) is written down in http._write_api2, the one
# place every write passes through. That layer knows the URL and the status but
# not which tool ran or which community it aimed at, so the tool boundary hands
# it that context. Wrapping mcp.tool() once covers every tool — including the
# ones somebody adds later, who would otherwise have to remember to do this.
# Read tools set the context too and simply never trigger a line; that costs a
# dict and keeps the rule "no tool is special" true.
_AUDIT_ARGS = {  # tool argument -> audit field
    "community_slug": "community",
    "post_id": "post_id",
    "comment_id": "comment_id",
    "item_id": "course_item_id",
    "course_id": "course_item_id",
    "channel_id": "dm_channel_id",
    "member_id": "member_id",
    "event_id": "calendar_event_id",
    "category_id": "category_id",
}


def _with_audit_context(fn):
    """Wrap one tool function so its call names itself and its target."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
        bound.apply_defaults()
        fields = {dest: bound.arguments.get(src, "")
                  for src, dest in _AUDIT_ARGS.items()}
        with audit.context(fn.__name__, **fields):
            return fn(*args, **kwargs)

    return wrapper


def _audited_tool(*d_args, **d_kwargs):
    """`mcp.tool()`, plus the audit context around every call."""
    register = _raw_tool(*d_args, **d_kwargs)
    return lambda fn: register(_with_audit_context(fn))


_raw_tool = mcp.tool
mcp.tool = _audited_tool

_client = None  # lazy: log in only when the first tool actually needs Skool
_clients: dict[str, Any] = {}  # per-user clients, keyed by OAuth subject
_client_built: dict[str, float] = {}  # subject -> monotonic build time

# WAF-token refresh (hosted): a stored session's aws-waf-token ages while the
# year-long login stays valid, and a stale token degrades members.json into
# 202-challenges/403s (measured 2026-08-15 — the likely cause of Dan's
# skoolers 403). Sessions older than this get a silent headless re-visit of
# the user's persisted Chromium profile before a client is built from them.
_REFRESH_AGE_S = float(os.environ.get("CATKNOWS_SESSION_REFRESH_AGE", str(4 * 3600)))
_REFRESH_RETRY_S = 900  # a failed refresh isn't retried for 15 min
_refresh_lock = None  # created lazily — one Chromium at a time on a 4 GB box
_refresh_last_try: dict[str, float] = {}


def _refresh_session_cookie(subject: str) -> str | None:
    """Best-effort fresh cookie header for this subject, or None to keep the
    stored one. Never raises: the stored cookie may still work fine."""
    import threading
    import time as _time

    age = sessions.age(subject)
    if age is None or age < _REFRESH_AGE_S:
        return None
    global _refresh_lock
    if _refresh_lock is None:
        _refresh_lock = threading.Lock()
    with _refresh_lock:
        last = _refresh_last_try.get(subject, float("-inf"))
        if _time.monotonic() - last < _REFRESH_RETRY_S:
            return None
        _refresh_last_try[subject] = _time.monotonic()
        try:
            import asyncio

            from .remote_login import refresh_cookie

            return asyncio.run(asyncio.wait_for(refresh_cookie(subject), 90))
        except Exception:
            return None


def _http_mode() -> bool:
    """True when the server should speak streamable HTTP instead of stdio."""
    return os.environ.get("CATKNOWS_HTTP", "") == "1"


def _profile_dir() -> Path:
    return Path(
        os.environ.get("CATKNOWS_PROFILE_DIR", Path.home() / ".catknows" / "skool-profile")
    )


def _client_from_cookie(cookie_header: str):
    """SkoolClient for one stored cookie header. Raises if it carries no session."""
    from . import SkoolClient, session_from_cookie

    session = session_from_cookie(cookie_header)
    if not session.is_valid:
        raise RuntimeError("That cookie header has no auth_token in it.")
    return SkoolClient(session)


def _subject() -> str:
    """Who is calling, per the verified OAuth token. Empty over stdio."""
    try:
        from mcp.server.auth.middleware.auth_context import get_access_token
    except ImportError:  # SDK without auth support: stdio only anyway
        return ""
    token = get_access_token()
    return (token.subject or "") if token else ""


def _get_client():
    """Return a logged-in SkoolClient for the *calling user*.

    With per-user sessions on (CATKNOWS_SESSION_DIR), every request is served
    from the session stored under its own OAuth subject — never a shared one.
    Without it, behaviour is the old single-session server: correct for stdio,
    where the client already is the user.

    stdout is the MCP protocol channel — login() prints instructions, so
    everything here runs with stdout redirected to stderr.
    """
    global _client

    if sessions.enabled():
        subject = _subject()
        if not subject:
            # Refusing beats falling back to the shared session: that fallback
            # is exactly the tenancy bug this store exists to close.
            raise RuntimeError(
                "No verified user on this request, so there is no session to use. "
                "The hosted server requires OAuth."
            )
        import time as _time

        # Rebuild after _REFRESH_AGE_S so a long-lived process picks up
        # refreshed cookies instead of serving a client built days ago.
        stale = _time.monotonic() - _client_built.get(subject, 0) > _REFRESH_AGE_S
        if subject not in _clients or stale:
            # ponytail: evict-oldest at 50; an LRU matters once concurrent users
            # outnumber that, and each entry holds an open HTTP session.
            if len(_clients) >= 50:
                _clients.pop(next(iter(_clients)))
            cookie = sessions.load(subject)
            if not cookie:
                # Points at the self-service page, not at a shell command: since
                # registration opened, the person reading this usually has no
                # access to the box. There is deliberately no tool that takes a
                # cookie — it is a year-long bearer token for the whole account
                # and must not go through a chat.
                raise RuntimeError(
                    "No Skool session stored for you yet. Open "
                    "https://catknows.app/connect and log in to Skool there — a "
                    "browser runs on the server and is streamed to you, so your "
                    "password goes to Skool and the session cookie never passes "
                    "through this chat."
                )
            fresh = _refresh_session_cookie(subject)
            if fresh:
                sessions.save(subject, fresh)
                cookie = fresh
            _clients[subject] = _client_from_cookie(cookie)
            _client_built[subject] = _time.monotonic()
        return _clients[subject]

    if _client is None:
        from . import SkoolClient, login, session_from_cookie

        with redirect_stdout(sys.stderr):
            cookie = os.environ.get("CATKNOWS_COOKIE", "")
            if cookie:
                session = session_from_cookie(cookie)
                if not session.is_valid:
                    raise RuntimeError("CATKNOWS_COOKIE has no auth_token in it.")
            else:
                try:
                    # Fast path: persisted profile -> headless, no window.
                    session = login(profile_dir=_profile_dir(), headless=True, timeout_ms=30_000)
                except Exception:
                    if _http_mode():
                        # No display on a server — a visible window would hang
                        # the request instead of asking anyone to log in.
                        raise RuntimeError(
                            "Skool session expired and no browser can be shown in HTTP mode. "
                            "Set CATKNOWS_COOKIE to a raw Cookie header, or refresh "
                            f"{_profile_dir()} by running the stdio server once on a machine "
                            "with a display."
                        ) from None
                    # First run or expired session: visible window, user logs in.
                    session = login(profile_dir=_profile_dir(), headless=False)
        _client = SkoolClient(session)
    return _client


def _cap(limit: int, raw: bool) -> tuple[int, dict | None]:
    """Bound a list limit so results don't blow past the tool-result size cap,
    and hand back the marker to append when the caller asked for more.

    Raw records are far bigger than normalized ones, so cap them harder.

    The cap itself is deliberate, but it used to be silent: asking a 592-member
    community for 650 returned exactly 200 rows and nothing said so, which reads
    as "that is the whole community". The incomplete marker never fired here — it
    only reports Skool cutting the page walk short, not us lowering the limit.
    Both list tools append this instead, so the two stay in step.

    The returned dict is a template; `_batch_marker` decides whether it is
    appended at all and fills in the real row count and the next offset.
    """
    ceiling = 30 if raw else 200
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = ceiling
    effective = max(1, min(limit, ceiling))
    if limit <= ceiling:
        return effective, None
    return effective, {
        "limit_capped": True,
        "requested": limit,
        "returned": effective,
        "ceiling": ceiling,
        "raw": raw,
    }


def _offset(offset: int) -> int:
    try:
        return max(0, int(offset))
    except (TypeError, ValueError):
        return 0


def _batch_marker(capped: dict | None, rows: int, offset: int, *,
                  more: bool, also_cut: bool = False) -> dict | None:
    """The limit marker for one batch, or None when nothing was cut.

    The 200 cap is a batch size, not the end of the list (a user's report,
    2026-09-24: list_posts(author=...) and get_follows could never get past
    200). ``more`` says the walk filled the batch, so rows may follow — the
    marker then carries ``next_offset`` for the follow-up call. ``also_cut``
    is another cap that ended the walk first (the role cap in list_members):
    worth stating, but there is no next batch to point at.

    Asking for 650 of a 10-row list returns all 10 and no marker — claiming a
    cut that never happened is the mirror image of hiding one.
    """
    if not capped or not (more or also_cut):
        return None
    ceiling = capped.pop("ceiling", 200)
    raw = capped.pop("raw", False)
    marker = {**capped, "returned": rows}
    if offset:
        marker["offset"] = offset
    note = (f"catknows returns at most {ceiling} records per call"
            f"{' (raw records are much bigger)' if raw else ''} so the response "
            "stays inside the tool-result size limit. ")
    if more:
        marker["next_offset"] = offset + rows
        note += (f"This list is records {offset + 1}–{offset + rows}, not "
                 f"everything that matched. Call again with offset="
                 f"{offset + rows} for the next batch, or narrow the query "
                 "(filters, lifecycle, tiers or course_ids on list_members; "
                 "author or category on list_posts) so fewer rows match.")
    else:
        note += f"Only {rows} came back, see the other marker for why."
    marker["note"] = note
    return marker


def _jsonable(obj: Any) -> Any:
    """normalize.* records carry datetimes — make them JSON-safe."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonable(v) for v in obj]
    return obj


# Skool nests JSON as *strings* inside metadata; the parser lives in normalize.
_maybe_json = normalize.maybe_json


def _safe_raw(payload):
    """Strip credential-class fields from any payload we return with raw=True.

    Every ``raw=True`` path routes through here so account secrets (Zapier
    keys, Stripe payout ids, billing/payment, affiliate, and Skool's own
    client-side platform keys — docs/API.md §6.6) and the admin-side data Skool
    attaches to each member (join survey, email answer, attribution, request
    location, approver — docs/API.md §6.5) never reach an AI context or a log.
    The scrub lists live in one place: normalize.SECRET_KEYS and
    normalize.PRIVATE_KEYS.
    """
    return normalize.scrub(payload)


@mcp.tool()
def login_to_skool() -> str:
    """Log in to Skool (opens a browser window on first use). Call this if other tools report auth errors; afterwards the session is persisted and reused silently."""
    _get_client()
    return "Logged in. Session persisted — future calls run without the browser."


# -- per-user session tools ----------------------------------------------------
# Only registered on the hosted server. Storing a session is deliberately NOT a
# tool: a Skool cookie is a year-long bearer token for the whole account, and a
# tool that takes one invites pasting it into a chat, where it lands in a
# conversation log. `catknows-session store <subject>` reads it from a hidden
# prompt on the box instead. Deleting stays a tool — it carries no secret.

if sessions.enabled():

    @mcp.tool()
    def forget_skool_session() -> dict:
        """Delete YOUR stored Skool session from the server. Tools stop working until you store a new one."""
        subject = _subject()
        if not subject:
            raise RuntimeError("No verified user on this request.")
        _clients.pop(subject, None)
        _client_built.pop(subject, None)
        existed = sessions.delete(subject)
        return {"status": "deleted" if existed else "nothing stored"}


# AI-facing flag names -> members() keyword arguments. Flat strings, because
# MCP arguments are flat text (see _csv_arg) — no nested filter objects.
_MEMBER_FILTER_KWARGS = {
    "admins": "admins", "online": "online", "trials": "trials",
    "monthly": "monthly", "annual": "annual", "one_time": "one_time",
    "free": "free",
}


@mcp.tool()
def list_members(community_slug: str, limit: int = 25, raw: bool = False,
                 lifecycle: str = "", sort: str = "last_active",
                 filters: str = "", tiers: str = "",
                 course_ids: str = "", offset: int = 0) -> list[dict]:
    """List members of a Skool community: name, role, points, level, last-active, and more.

    Sorted by Skool DESC on last-active by default, so limit=25 is the most
    recently active members. community_slug is the part after skool.com/ in the
    URL. raw=True returns Skool's unmodified JSON (large — keep limit small, it's
    hard-capped to avoid exceeding the tool-result size limit).

    Your role decides how much of the list exists for you: as a regular member
    you get the FIRST PAGE only, the same as Skool's own UI gives you, and the
    last list entry is {"capped_by_role": true, ...}. Raising limit does not
    lift this. Moderators, admins and owners get every page.

    One call returns at most 200 members (30 with raw=True), whatever limit says,
    so the response fits the tool-result size limit. When more are there, the
    last list entry is {"limit_capped": true, "next_offset": ...} — call again
    with offset=next_offset for the next batch, or narrow the query with the
    filters below.

    Filtering (works for regular members too, not just admins):
    - lifecycle: active | cancelling | churned | banned (empty = default view)
    - sort: newest | last_active | most_points. As a regular member Skool may
      ignore the sort and answer with its own last-active ordering; that is a
      Skool role limit, not something catknows can override.
    - filters: comma-separated flags out of admins, online, trials, monthly,
      annual, one_time, free. Multiple flags combine as AND (Skool-native) —
      "annual OR free" needs two calls, merged by you.
    - tiers: comma-separated tier names (e.g. standard,premium,vip) — OR.
    - course_ids: comma-separated course ids (from get_classroom) — AND.

    If Skool stops serving fresh pages mid-walk (degraded/stale session), the
    last list entry is {"incomplete": true, ...} — treat the list as truncated,
    NOT as the whole community.
    """
    kwargs: dict = {}
    for name in _csv_arg(filters):
        key = _MEMBER_FILTER_KWARGS.get(name.lower())
        if key is None:
            raise ValueError(
                f"Unknown filter {name!r}. "
                f"One of: {', '.join(_MEMBER_FILTER_KWARGS)}.")
        kwargs[key] = True
    effective, capped = _cap(limit, raw)
    offset = _offset(offset)
    users = _get_client().members(
        community_slug, limit=offset + effective, lifecycle=lifecycle, sort=sort,
        tiers=_csv_arg(tiers), course_ids=_csv_arg(course_ids), **kwargs)
    batch = list(users)[offset:]
    out = _safe_raw(batch) if raw else [_jsonable(normalize.member(u)) for u in batch]
    # The role cap can end the walk at 30, and a marker claiming 200 next to
    # 30 rows is exactly the dishonesty this marker exists to prevent.
    marker = _batch_marker(capped, len(batch), offset,
                           more=len(users) >= offset + effective,
                           also_cut=users.capped_by_role)
    if marker:
        out.append(marker)
    if users.capped_by_role:
        out.append({
            "capped_by_role": True,
            "role": "member",
            "returned": len(users),
            "members_reported_by_skool": users.total_members,
            "pages_available": users.total_pages,
            "note": "You are a regular member of this community, so catknows "
                    "returns the first page only, the same as Skool's own UI "
                    "shows you. This list is NOT the full member list. Only a "
                    "moderator, admin or owner gets every page.",
        })
    if users.incomplete:
        marker = {
            "incomplete": True,
            "unique_members_returned": len(users),
            "pages_served": users.pages_walked,
            "total_pages": users.total_pages,
            "note": "Skool repeated already-served pages before the end — this "
                    "list is truncated, not the full community. Skool does this "
                    "per community/role/session (seen on large communities as a "
                    "regular member, and on stale sessions). A fresh login can "
                    "help; some communities never serve later pages to non-admins.",
        }
        if users.short_by:
            marker["members_reported_by_skool"] = users.total_members
            marker["missing"] = users.short_by
            marker["note"] = (
                f"Skool counts {users.total_members} members for this query but "
                f"only handed over {len(users)} — {users.short_by} are missing. "
                "Measured on small communities where every page repeats page 1, "
                "even for the owner, so no filter or page size gets the rest out. "
                "Treat this list as a LOWER BOUND: it is safe to say these people "
                "are members, never that someone absent is not one."
            )
        out.append(marker)
    return out


@mcp.tool()
def list_posts(community_slug: str, limit: int = 25, raw: bool = False,
               sort: str = "activity", unread_only: bool = False,
               category: str = "", author: str = "", offset: int = 0) -> list[dict]:
    """List posts of a Skool community (title, author, likes, comment count, content, category).

    author: only posts this member CREATED in the community — their @handle
    ("niklas") or full name ("Niklas Schröer"). Complete, not a name search:
    search_community("Niklas") also returns others' posts that mention them.
    With author set, sort/unread_only/category don't apply; order is Skool's
    (most recent activity first). An ambiguous name errors with the handles.

    Let Skool filter instead of reading everything — it's cheaper:
    - sort: "activity" (default, new comments bubble up), "new" (newest posts),
      "top_day" / "top_week" / "top_month" / "top_year" / "top_all" (most liked).
    - unread_only=true: only posts YOU haven't opened yet (Skool's "Unread" tab).
    - category: a feed category by name ("feedback") or id; an unknown name
      errors with the list of existing ones.
    Each post says `category` and `has_new_comments` (Skool's "new comment" badge).

    raw=True returns Skool's unmodified post trees. Keep limit small — raw trees
    are large and can exceed the tool-result size cap.

    One call returns at most 200 posts (30 with raw=True), whatever limit says.
    When more are there, the last list entry is {"limit_capped": true,
    "next_offset": ...} — call again with offset=next_offset for the next
    batch, in the same order. Works with author too, so a member's full
    history is reachable batch by batch.
    """
    effective, capped = _cap(limit, raw)
    offset = _offset(offset)
    want = offset + effective
    client = _get_client()
    if author:
        if unread_only or category or sort != "activity":
            raise ValueError("author can't be combined with sort, unread_only or category")
        handle = client.resolve_member(community_slug, author)["name"]
        trees = client.posts_by(community_slug, handle, limit=want)
    else:
        category_id = client.resolve_category(community_slug, category) if category else ""
        trees = client.posts(community_slug, limit=want, sort=sort,
                             unread_only=unread_only, category_id=category_id)
    more = len(trees) >= want
    trees = trees[offset:]
    if raw:
        out = _safe_raw(trees)
    else:
        names = {c["id"]: c["name"] for c in client.post_categories(community_slug)}
        out = []
        for t in trees:
            rec = _jsonable(normalize.post(t))
            rec["category"] = names.get(rec["category_id"], "")
            out.append(rec)
    marker = _batch_marker(capped, len(trees), offset, more=more)
    if marker:
        out = [*out, marker]
    return out


@mcp.tool()
def search_community(community_slug: str, query: str, kind: str = "posts",
                     page: int = 1) -> dict:
    """Search one community with Skool's own search — posts or members.

    Use this instead of listing everything and reading it: Skool does the
    matching, you get only the hits. kind="posts": 10 posts per page (same
    fields as list_posts); kind="members": people whose name or bio matches
    (same fields as list_members, plus member_id). `pages` says how far
    `page` can go for posts. A name matches anywhere, @mentions in comments
    included — for "all posts by X" use list_posts(author=...) instead.
    """
    client = _get_client()
    pp = client.search(community_slug, query, kind=kind, page=page)
    if kind == "members":
        hits = [_jsonable(normalize.search_member(m)) for m in pp.get("members") or []]
        return {"query": query, "kind": kind, "total": pp.get("totalMembers"),
                "page": page, "results": hits}
    names = {c["id"]: c["name"] for c in client.post_categories(community_slug)}
    hits = []
    for t in pp.get("postTrees") or []:
        rec = _jsonable(normalize.post(t))
        rec["category"] = names.get(rec["category_id"], "")
        hits.append(rec)
    return {"query": query, "kind": kind, "total": pp.get("totalPosts"),
            "page": page, "pages": pp.get("totalPostPages"), "results": hits}


@mcp.tool()
def search_members(community_slug: str, query: str, limit: int = 7, raw: bool = False) -> dict:
    """Find members of one community by name with Skool's @mention search: id, handle, name, points, level.

    A quick answer to "who is X?" when you need a person's handle or id. It
    returns the same fields as list_members and no email or other contact
    data; raw=True returns Skool's records through the secret scrub, with
    email keys removed too. `query` is required: this is a person search, not
    a way to list a community (use list_members for that, and note that
    Skool's role limits on member lists still apply).

    catknows asks Skool for 7 hits and sends only that one request (the one
    Skool's own page sends; what another limit or a paging cursor would do is
    not documented, so there is no cursor). `limit` (1 to 7, default 7) only
    shortens what you get back; a larger value is lowered to 7 and a
    `limit_capped` entry says so. To find someone further down, make the query
    more specific. `has_more` appears only when Skool sends it. Names and bios
    are personal data: use them for the task at hand, do not collect them.
    """
    from .client import SkoolClient

    effective = min(SkoolClient.check_search_limit(limit), SkoolClient.SEARCH_USERS_LIMIT)
    answer = _get_client().search_members(community_slug, query)
    users = (answer.get("users") or [])[:effective]
    if raw:
        members = normalize.strip_contact(_safe_raw(users))
    else:
        members = [_jsonable(normalize.search_user(u)) for u in users]
    out: dict = {"query": query, "community": community_slug,
                 "returned": len(members), "members": members}
    if "has_more" in answer:  # as Skool sent it; never invented. Its cursor is not passed on.
        out["has_more"] = answer["has_more"]
    if limit > effective:
        out["limit_capped"] = {
            "requested": limit, "returned": len(members), "ceiling": effective,
            "note": (f"catknows asks Skool for {effective} hits per call, the one request "
                     f"Skool's own page sends, so you get at most {effective}: limit {limit} "
                     f"was lowered to {effective}. Narrow the query to find someone further "
                     "down."),
        }
    return out


@mcp.tool()
def get_file_download_url(file_id: str, expire: int = 0) -> dict:
    """Get a download link for one uploaded FILE (document, image, other attachment) by its file id.

    The link is an ACCESS LINK: whoever holds it can fetch the file until it
    expires. Use it to fetch the file, then drop it. Do not paste it into a
    post, a DM or a note, and do not store it; catknows itself writes it
    nowhere (not into the audit log, not into an error message). A post's
    attachments already carry a ready link in get_post (`files[].url`): use
    that first, this is for a file id without one.

    expire: how long the link should stay valid. 0 (default) means not sent,
    Skool decides. Skool's own client calls it `expireSecs` (so seconds;
    28800 for chat attachments), but the unit and the allowed range were not
    run: pass it only when you know you need it.

    Files only. Downloading a VIDEO is an admin right in Skool and is not
    offered here; videos embedded from Loom, Wistia or Vimeo are not touched
    either (LEGAL.md).
    """
    url = _get_client().file_download_url(file_id, expire or None)
    return _safe_raw({
        "file_id": file_id,
        "download_url": url,
        "expire": expire or "not sent (Skool's default)",
        "note": ("This is an access link: anyone who has it can fetch the file until it "
                 "expires. Fetch what you need, do not paste it into posts, DMs or notes, "
                 "and do not keep it. How long it works is not known."),
    })


@mcp.tool()
def get_post(community_slug: str, post_name: str, raw: bool = False) -> Any:
    """Get ONE post with its file attachments (name, type, download URL) and video ids.

    Use this when a post has an attachment you need to read: the post LIST only
    carries attachment ids, and the downloadable URL exists only here. Each
    entry in `files` has a `url` you can fetch directly. `post_name` is the
    post's URL slug (the `name` field from list_posts), not its id.
    """
    pp = _get_client().post_detail(community_slug, post_name)
    if raw:
        return _safe_raw(pp)
    return _jsonable(normalize.post(pp.get("postTree") or {}))


@mcp.tool()
def get_video_transcript(community_slug: str, post_name: str) -> list[dict]:
    """Get the spoken transcript of a post's Skool-hosted video(s).

    Reads the captions Skool itself generates for its player, so it needs no
    audio download. One record per video, with `transcript` (full text) and
    timestamped `cues`.

    Limits, both measured: videos with NO audio track have no captions and come
    back has_transcript=false. Videos merely EMBEDDED from YouTube/Loom/Vimeo
    are not Skool-hosted and are not covered here — only Skool's own uploads.
    `post_name` is the post's URL slug (the `name` field from list_posts).
    """
    return _get_client().video_transcript(community_slug, post_name)


@mcp.tool()
def get_post_comments(community_slug: str, post_id: str, raw: bool = False) -> Any:
    """Get the full nested comment thread of a post. post_id is the post's Skool id (from list_posts)."""
    client = _get_client()
    merged = client.comments(post_id, client.group_id_for(community_slug))
    return _safe_raw(merged) if raw else _jsonable(normalize.comments(merged))


@mcp.tool()
def get_post_likes(community_slug: str, post_id: str, limit: int = 10, cursor: str = "",
                   tab: str = "upvotes") -> dict:
    """List who liked/upvoted a post, ONE page at a time (id, handle, first/last name).

    Returns `likes` (this page), `has_more`, `cursor`, `total_upvotes` and
    `total_downvotes`. To read the next page, call again with the same post and
    `cursor` set to the `cursor` of the previous answer, and stop when
    `has_more` is false. The first call needs no cursor; `limit` is how many
    people one page holds (the website asks for 10; the highest value Skool
    accepts is not measured). `community_slug` is not needed for the lookup any
    more but is still accepted.
    """
    data = _get_client().likes_page(post_id, limit=limit, cursor=cursor, tab=tab)
    # Raw liker objects carry full user metadata — including the caller's own
    # payout/affiliate ids on their own like. Only names leave this tool.
    return {
        "likes": [normalize.like(u, post_id) for u in data.get("users") or []],
        "has_more": bool(data.get("has_more")),
        "cursor": data.get("cursor") or "",
        "total_upvotes": int(data.get("total_upvote_users") or 0),
        "total_downvotes": int(data.get("total_downvote_users") or 0),
    }


@mcp.tool()
def get_member_profile(user_name: str, community_slug: str, raw: bool = False) -> dict | None:
    """Get one member's profile (bio, socials, stats). user_name is their Skool handle."""
    user = _get_client().profile(user_name, community_slug)
    if user is None:
        return None
    if raw:
        return _safe_raw(user)
    return _jsonable(normalize.profile(user))


@mcp.tool()
def get_follows(user_name: str, direction: str = "followers", limit: int = 1000,
                offset: int = 0) -> dict:
    """Who follows a Skool user (direction="followers") or whom they follow ("following").

    user_name is the Skool handle (skool.com/@handle), your own or anyone's —
    the lists are on every profile. Each person comes back with name, bio,
    location and you_follow (whether YOU follow them — on your own followers
    list that means "follows back").

    One call returns at most 200 people. total is Skool's own count; if it's
    larger than what has been returned so far, the result has
    "limit_capped" with next_offset — call again with offset=next_offset for
    the rest. If Skool stopped serving pages early, "incomplete" is true —
    don't treat that list as everyone. On other people's profiles Skool often
    re-serves page 1 for every page, so only the first 30 are reachable there
    (measured 2026-10-11: two profiles with 210 and 940 followers → 30 each,
    incomplete; the caller's own 115 → all 115).
    """
    limit, capped = _cap(limit, False)
    offset = _offset(offset)
    data = _get_client().follows(user_name, direction, limit=offset + limit)
    people = [normalize.follow(e, direction) for e in data["entries"][offset:]]
    out: dict = {"user_name": user_name, "direction": direction,
                 "total": data["total"], "returned": len(people),
                 "incomplete": data["incomplete"], "people": people}
    if offset:
        out["offset"] = offset
    marker = _batch_marker(capped, len(people), offset,
                           more=data["total"] > offset + len(people)
                           and len(data["entries"]) >= offset + limit)
    if marker:
        out["limit_capped"] = marker
    return out


@mcp.tool()
def get_community_about(community_slug: str, raw: bool = False) -> dict:
    """Get a community's public About info (description, pricing, size, owner) — works without joining it.

    Returns a compact summary by default. raw=True returns Skool's full page
    payload, which is very large and may exceed the tool-result size limit.
    """
    data = _get_client().community_about(community_slug)
    if raw:
        return _safe_raw(data)
    g = (((data.get("pageProps") or {}).get("currentGroup")) or {})
    md = g.get("metadata") or {}
    pricing = normalize.about_pricing(data)
    # owner arrives as a JSON *string* since ~Aug 2026 — .get() on it crashed.
    owner = _maybe_json(md.get("owner"))
    return {
        "slug": g.get("name", community_slug),
        "display_name": md.get("displayName", ""),
        "description": md.get("description", ""),
        "membership_model": pricing["membership_model"],
        "plan": md.get("plan"),
        "price": pricing["price"],
        "tiers": pricing["tiers"],
        "total_members": md.get("totalMembers"),
        "total_online": md.get("totalOnlineMembers"),
        "total_admins": md.get("totalAdmins"),
        "total_posts": md.get("totalPosts"),
        "num_courses": md.get("numCourses"),
        "privacy": md.get("privacy"),  # NOT a visibility flag — public and private groups both return 1, and one group flipped 1->0 within hours (2026-08-16)
        "owner": owner.get("name") if isinstance(owner, dict) else owner,
        "created_by": md.get("createdBy"),
    }


@mcp.tool()
def get_community_rules(community_slug: str) -> Any:
    """Get a community's rules (the list its members see and pick from when they report a post), as Skool sends them.

    Returned RAW, through the secret scrub, and not reshaped: the page code reads
    a `rules` list with a `title` and a `content` per rule, but what the call
    really answers was not run against Skool, so nothing is renamed or filled in.
    Reading the rules is what any member of the community can do. Archived
    communities stay readable. The write side (add, edit, delete, reorder a rule)
    is not available.
    """
    return _safe_raw(_get_client().group_rules(community_slug))


@mcp.tool()
def get_community_levels(community_slug: str) -> Any:
    """Get a community's level names (what each of its 9 levels is called), as Skool sends them.

    Returned RAW, through the secret scrub, and not reshaped: the Leaderboards
    page shows a `[{number, title, ...}]` list of level names to every member,
    but that this call answers with the same list was not run against Skool, so
    nothing is renamed or filled in. For the levels together with the points
    board and each level's share of members, `get_leaderboard` is the page's own
    view. Archived communities stay readable. Renaming a level is not available.
    """
    return _safe_raw(_get_client().group_levels(community_slug))


@mcp.tool()
def get_discovery(page: int = 1) -> dict:
    """Get one page (~30) of Skool's global discovery board — ranked communities across all categories.

    Returns rank, slug, name, price, members and category-tags per community,
    plus the list of categories. Skool ranks the top 1000 (page 1–34). Query
    filters other than page are ignored by Skool, so pull pages and filter
    locally. For YOUR own community's true standing (also beyond the top
    1000) use get_discovery_rank.
    """
    pp = _get_client().discovery(page)
    cats = [{"slug": c.get("slug"), "name": c.get("name")} for c in (pp.get("categories") or [])]
    groups = []
    for row in pp.get("groups") or []:
        g = row.get("group") or {}
        md = g.get("metadata") or {}
        price = _maybe_json(md.get("displayPrice"))
        groups.append({
            "rank": row.get("rank"),
            "slug": g.get("name"),
            "display_name": md.get("displayName", ""),
            "members": md.get("totalMembers"),
            "price": price,
            "tags": row.get("tags"),
        })
    return {"page": page, "total_ranked": pp.get("numGroups"), "categories": cats, "communities": groups}


@mcp.tool()
def get_discovery_rank(community_slug: str) -> dict:
    """Get YOUR community's discovery standing (owner only): overall rank + category rank.

    Works beyond the top-1000 board (e.g. overall rank 28302). Also returns
    visibility, category, language and growth-boost status. Communities you
    don't own return a 401 — for those only the top-1000 board
    (get_discovery) is available.
    """
    client = _get_client()
    d = client.discovery_rank(client.group_id_for(community_slug))
    return {
        "is_showing": d.get("is_showing"),
        "rank": d.get("rank"),
        "category": (d.get("category") or {}).get("name"),
        "category_rank": d.get("category_rank"),
        "language": d.get("language_code"),
        "boost_enabled": d.get("boost_enabled"),
        "rank_updated_at": d.get("rank_updated_at"),
    }


@mcp.tool()
def get_admin_metrics(community_slug: str, range: str = "30d") -> dict:
    """Get admin metrics for a community you own (growth, engagement). range is always 30d — Skool rejects every other value."""
    client = _get_client()
    return _safe_raw(client.admin_metrics(client.group_id_for(community_slug), range_=range))


@mcp.tool()
def get_growth(community_slug: str,
               charts: str = "signups_by_source,signups_by_day,members") -> dict:
    """Growth of a community you own, as on its admin dashboard — last 30 days.

    Visitors (people who viewed the about page), signups, conversion_rate
    (signups / visitors), new MRR and member count, plus charts:
    signups_by_source (Skool network / affiliate / direct), signups_by_day,
    members (per month since founding: new, existing, churned, total).
    Also available: retention_members, retention_cohorts, mrr, mrr_cashflow,
    mrr_unit — name them comma-separated in `charts`, "" for the tiles only.
    Skool shows only the last 30 days; the daily history lives in the vault
    (trends/growth.jsonl) if the snapshot job runs.
    """
    client = _get_client()
    gid = client.group_id_for(community_slug)
    tiles = client.growth_overview(gid)
    visitors, signups = tiles.get("num_visitors"), tiles.get("num_signups")
    out = {
        "community": community_slug,
        "period": "last 30 days",
        "members": tiles.get("num_members"),
        "visitors": visitors,
        "signups": signups,
        "conversion_rate": round(signups / visitors, 3) if visitors and signups is not None else None,
        "new_mrr": tiles.get("new_mrr"),
        "charts": {},
    }
    for chart in [c.strip() for c in charts.split(",") if c.strip()]:
        out["charts"][chart] = client.analytics_chart(gid, chart)
    return out


def _join_queue(community_slug: str, *, max_pages: int = 10) -> list[dict]:
    """The whole join queue, flattened (at most ``max_pages`` × 30).

    Email answers are masked, always: this feeds the draft of
    ``review_join_request``, and deciding on a person never needs the address.
    """
    client = _get_client()
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        pp = client.join_requests(community_slug, page=page)
        users = pp.get("users") or []
        out.extend(_jsonable(normalize.join_request(u, reveal_email=False)) for u in users)
        if not users or page >= int(pp.get("totalPages") or 1):
            break
    return out


@mcp.tool()
def list_join_requests(community_slug: str, query: str = "", page: int = 1,
                       reveal_email: bool = False) -> dict:
    """Pending join requests of a community you own or moderate.

    Each: name, bio, when, where from (location), how they found you
    (source), Skool's spam flag (risk_flag), and every answered join question.
    member_id is what review_join_request needs. query narrows to requests
    whose name, bio or answers contain it (case-insensitive), as printed: with
    the addresses masked, searching for an address finds nothing. 30 per page.

    Email addresses (the email answer and any address in an answer) come out
    masked by default, e.g. a***@example.com. reveal_email=True prints them in
    full; use it only when the owner asked for the addresses (to write to an
    applicant, say), never to "look closer".
    """
    pp = _get_client().join_requests(community_slug, page=page)
    reqs = [_jsonable(normalize.join_request(u, reveal_email=reveal_email))
            for u in pp.get("users") or []]
    if query:
        q = query.lower()
        reqs = [r for r in reqs if q in " ".join(
            [r["name"], r["first_name"], r["last_name"], r["bio"], r["email_answer"]]
            + [str(a["answer"]) for a in r["answers"]]).lower()]
    return {"community": community_slug, "total": pp.get("total"), "page": page,
            "pages": pp.get("totalPages"), "count": len(reqs), "requests": reqs}


@mcp.tool()
def get_leaderboard(community_slug: str, period: str = "all") -> dict:
    """A community's leaderboard, as on its Leaderboards page: who has the most points.

    period: "all" (all-time), "30d" or "7d". Skool shows the top 10 per
    period and no more. Each row: rank, handle, name, points in that period,
    level. me: your rank and points in the period (null when Skool doesn't
    rank you, e.g. as owner or admin), your level, total points and how many
    points until the next level. levels: the community's level names with
    the share of members on each and what a level unlocks. Private
    communities: members only. Public ones show their board to anyone.
    """
    if period not in normalize.LEADERBOARD_PERIODS:
        raise ValueError(f"period must be one of: {', '.join(normalize.LEADERBOARD_PERIODS)}")
    out = normalize.leaderboard(_get_client().leaderboards(community_slug), period)
    return {"community": community_slug, "shown": "top 10 (all Skool shows)", **out}


@mcp.tool()
def get_revenue_leaderboard(category: str = "", limit: int = 100) -> dict:
    """Skool's top-earning communities — the "games" leaderboard in the Skoolers community.

    Overall: the top 100 across Skool. category (name like "hobbies" or
    "tech", or its id) gives that category's top 50. Nothing beyond that
    exists. Each row: global and category rank, community (slug + name),
    owner with revenue badge (🍀 $3k … 🐐 $1m), mrr_usd and mrr_growth_usd
    (monthly, whole dollars), traffic. Needs an account that owns a
    community (Skoolers is gated).
    """
    from .client import _plain

    client = _get_client()
    category_id, name = "", ""
    if category:
        cats = client.revenue_leaderboard().get("categories") or []
        want = _plain(category)
        hit = next((c for c in cats if category == c.get("id")
                    or want == _plain(c.get("name", ""))), None)
        if hit is None:
            raise ValueError(f"no category '{category}'; there are: "
                             + ", ".join(c.get("name", "") for c in cats))
        category_id, name = hit["id"], hit.get("name", "")
    pp = client.revenue_leaderboard(category_id=category_id)
    rows = pp.get("rows") or []
    limit = max(1, min(int(limit), 100))
    return {
        "category": name or "all categories",
        "available": len(rows),
        "count": min(limit, len(rows)),
        "categories": [c.get("name", "") for c in pp.get("categories") or []],
        "rows": [normalize.revenue_row(r) for r in rows[:limit]],
    }


@mcp.tool()
def get_calendar(community_slug: str, cal_date: int = 0, raw: bool = False) -> dict:
    """Get the community calendar: a compact list of events (title, start/end, description, location).

    cal_date is a unix timestamp inside another month (0 = current month).
    raw=True returns Skool's full page payload, which is very large and may
    exceed the tool-result size limit.
    """
    data = _get_client().calendar(community_slug, cal_date=cal_date)
    if raw:
        return _safe_raw(data)
    events = []
    for ev in ((data.get("pageProps") or {}).get("events")) or []:
        md = ev.get("metadata") or {}
        desc = (md.get("description") or "").strip()
        events.append({
            "title": md.get("title", ""),
            "start": ev.get("startTime"),
            "end": ev.get("endTime"),
            "timezone": md.get("timezone", ""),
            "description": desc[:300] + ("…" if len(desc) > 300 else ""),
            "location": _maybe_json(md.get("location")),
        })
    return {"num_events": len(events), "events": events}


@mcp.tool()
def get_classroom(community_slug: str, raw: bool = False) -> dict:
    """Get the community classroom: the course list with titles, descriptions, module counts and access flags.

    Compact by default. raw=True returns Skool's full page payload, which is
    very large and may exceed the tool-result size limit.
    """
    data = _get_client().classroom(community_slug)
    if raw:
        return _safe_raw(data)
    courses = []
    for c in ((data.get("pageProps") or {}).get("allCourses")) or []:
        md = c.get("metadata") or {}
        desc = (md.get("desc") or "").strip()
        courses.append(
            {
                "id": c.get("id"),  # what get_course_tree / the write tools need
                "title": md.get("title", ""),
                "description": desc[:300] + ("…" if len(desc) > 300 else ""),
                "num_modules": md.get("numModules"),
                "has_access": bool(md.get("hasAccess")),
                "privacy": md.get("privacy"),  # 0=open, 1=level-locked, 2=private/tier
                "is_draft": c.get("state") == 1,
            }
        )
    return {"num_courses": len(courses), "courses": courses}


@mcp.tool()
def get_course_tree(course_id: str, community_slug: str = "", raw: bool = False) -> dict:
    """Read one course's full structure: folders and pages with ids, titles, order and draft state.

    course_id comes from get_classroom, which carries just the course tiles —
    page bodies live here. Child order in the tree is the display order.

    Pass community_slug for courses in communities you have NOT joined: Skool's
    own API refuses those outright, and only the classroom page shows what the
    community publishes (the same thing you see in a browser). Without it such
    a course comes back as a 401.
    """
    data = _get_client().course_tree(course_id, community_slug)
    if raw:
        return _safe_raw(data)

    # The two doors disagree on spelling: the API answers snake_case, the page
    # camelCase. Same tree, so read both rather than compacting twice.
    def _f(md: dict, *names, default=None):
        for n in names:
            if md.get(n) not in (None, ""):
                return md[n]
        return default

    def _compact(node):
        unit = node.get("course") or {}
        md = unit.get("metadata") or {}
        kind = _f(unit, "unit_type", "unitType")
        out = {
            "id": unit.get("id"),
            "type": kind,  # course | set (folder) | module (page)
            "title": md.get("title", ""),
            "is_draft": unit.get("state") == 1,
        }
        if kind == "module":
            out["content"] = md.get("desc", "")
            video = _f(md, "video_link", "videoLink")
            if video:
                out["video_link"] = video
        children = [_compact(ch) for ch in node.get("children") or []]
        if children:
            out["children"] = children
        return out

    return _compact(data)


@mcp.tool()
def list_chat_channels(offset: int = 0, limit: int = 30, unread_only: bool = False) -> dict:
    """List your Skool DM/chat channels (ids, participants, last message). Channel ids are what send_dm needs.

    unread_only=true lists only channels with unread messages (an empty `channels` list when there are none).
    """
    # Channel objects embed the other participant's full metadata (and, on your
    # own entries, afl/payout secrets) — scrub before returning.
    return _safe_raw(_get_client().chat_channels(offset=offset, limit=limit,
                                                 unread_only=unread_only))


@mcp.tool()
def read_dms(channel_id: str, count: int = 30, raw: bool = False) -> dict:
    """Read the messages of one DM channel (ids via list_chat_channels), newest last.

    count is how many of the most recent messages to return — there is no page
    to walk, so ask for the window you want in one call.
    """
    data = _get_client().chat_messages(channel_id, count=count)
    if raw:
        return _safe_raw(data)
    out = [_jsonable(normalize.chat_message(m)) for m in data.get("messages") or []]
    return {
        "channel_id": channel_id,
        "count": len(out),
        "has_more": bool(data.get("has_more_before")),
        "messages": out,
    }


@mcp.tool()
def get_notifications(limit: int = 30, category: str = "", community_slug: str = "",
                      cursor: str = "") -> dict:
    """Your Skool notifications (the bell), newest first — "what just happened?".

    Each one: kind (mention-comment, comment, admin-post = broadcast,
    new-post-user-following, membership-request, ...), who, when, community
    (slug) and a text preview. `target` says what it points at: read the post
    with get_post(community, target.post_name), the thread with
    get_post_comments(community, target.post_id); target.comment_id is the
    comment inside that thread (empty when the notification is about the post).

    Reading does NOT mark anything as read. limit is 1..30 (Skool's own cap);
    pass the returned cursor to go further back. category narrows to one of
    mentions, comments, following, requests, new_posts, recordings;
    community_slug to one community.
    """
    data = _get_client().notifications(limit=limit, category=category,
                                       community_slug=community_slug, cursor=cursor)
    items = [_jsonable(normalize.notification(m)) for m in data.get("messages") or []]
    return {
        "count": len(items),
        "unread": sum(1 for n in items if n["unread"]),
        "has_more": bool(data.get("has_more")),
        "cursor": data.get("cursor") or "",
        "notifications": items,
    }


@mcp.tool()
def list_my_communities(raw: bool = False) -> list[dict]:
    """List every Skool community YOUR account is in, with your role (owner/admin/moderator/member).

    Start here for cross-community questions ("which communities do I own?",
    "where am I barely active?") — every other tool needs a community_slug,
    and this is where the slugs come from. Skool omits the role for
    communities you own, so it's resolved against your own user id.

    `archived: true` means the community is read-only: you can still list its
    posts and members, but posting, commenting and liking are off. The write
    tools refuse an archived community up front rather than letting Skool
    reject the write.
    """
    client = _get_client()
    groups = client.self_groups()
    if raw:
        return _safe_raw(groups)
    my_id = (client.self_user() or {}).get("id", "")
    return [_jsonable(normalize.my_community(g, my_id)) for g in groups]


@mcp.tool()
def update_catknows(confirm: bool = False) -> dict:
    """Update the local catknows install from GitHub (pull + reinstall + self-check).

    confirm=false (default) only reports the current version and what's new —
    nothing is changed. Call again with confirm=true to install. Afterwards
    the AI client must reconnect: this running server keeps the old code
    until restarted. Works from any MCP client (ChatGPT, Claude, Cursor, …).
    """
    from . import update as _update

    if not confirm:
        state = _update.check()
        state["next_step"] = ("Nothing changed. Call again with confirm=true "
                              "to install what's listed in new_commits.")
        return state
    return _update.update()


# Writes member data to local files, so it only makes sense where the caller
# owns the filesystem. On the hosted server it would write other people's
# personal data onto my disk, where the user can't reach it anyway — and the
# processing agreement (deploy/DPA.md §3) promises member data is never
# persisted. Not registering it is what keeps that promise true.
if not sessions.enabled():

    @mcp.tool()
    def pull_to_vault(
        community_slug: str, vault_dir: str = "./vault", include_comments: bool = True
    ) -> dict:
        """Pull the whole community (members, posts, comments) into an Obsidian vault of Markdown notes with YAML frontmatter. Returns counts and the vault path.

        A post whose comments fail to load is counted in `comments_failed` and the
        pull goes on. Not so if Skool answers with HTTP 401, 403 or 429, with a 202
        again and again, or with its security question: the pull stops there with
        an error (in the comments loop it says how far it got) and requests nothing
        more. The notes already written stay. Do not start it again right away:
        tell the user.
        """
        return _pull_to_vault(community_slug, vault_dir, include_comments)


def _pull_to_vault(community_slug: str, vault_dir: str, include_comments: bool) -> dict:
    import time

    from .client import _INTER_PAGE_DELAY_S
    from .http import SkoolHTTPError, SkoolSecurityChallenge, turned_away

    client = _get_client()
    out = Path(vault_dir).expanduser().resolve()
    vault.ensure_scaffold(out)

    members = client.members(community_slug)
    for u in members:
        vault.write_member(out, community_slug, normalize.member(u))

    trees = client.posts(community_slug)
    group_id = next(
        (t["post"]["groupId"] for t in trees if (t.get("post") or {}).get("groupId")), ""
    )
    posts_written = comments_failed = 0
    fetched_comments = 0
    for tree in trees:
        prec = normalize.post(tree)
        comment_recs = None
        if include_comments and group_id and prec["comments"] > 0:
            # Pace the comment fetches. `client.comments` only sleeps BETWEEN
            # pages of one post, and almost every post has a single page, so a
            # 700-post pull used to fire ~4.5 requests/s and got CloudFront-
            # blocked after ~165 of them (comments_failed: 543 on hoomans).
            # Measured 2026-08-17: the cut-off lands at the same call count at
            # half that rate, so it is a refilling budget, not a rate — 0.8s
            # apart (the existing page delay) carried 200/200, 0.35s did not.
            if fetched_comments:
                time.sleep(_INTER_PAGE_DELAY_S)
            fetched_comments += 1
            try:
                comment_recs = normalize.comments(client.comments(prec["skool_id"], group_id))
            except SkoolSecurityChallenge as e:
                # Not "one bad post": after Skool's security question every further
                # request would be an automaton ignoring it. Stop the whole pull
                # with an error, not a partial result that looks like success.
                raise SkoolSecurityChallenge(
                    e.code, e.method, e.path,
                    stopped=(f"pull_to_vault stopped after {posts_written} of {len(trees)} "
                             "posts: Skool asked a security question while the comments "
                             "were read, so nothing more was requested. The notes already "
                             "written stay in the vault; the rest was not pulled."),
                ) from e
            except Exception as e:  # one bad post shouldn't kill the pull
                if turned_away(e):
                    # 401/403/429 or a repeated 202: the account is being turned
                    # away. Same stop as for the security question, the status
                    # (0 for the repeated 202) and its mark stay on the error.
                    stop = SkoolHTTPError(
                        f"pull_to_vault stopped after {posts_written} of {len(trees)} "
                        f"posts: Skool answered HTTP {e.status or 202} while the comments "
                        "were read, so nothing more was requested. The notes already "
                        f"written stay in the vault; the rest was not pulled. {e}",
                        e.status,
                    )
                    stop.repeated_202 = getattr(e, "repeated_202", False)
                    raise stop from e
                comments_failed += 1
        vault.write_post(out, community_slug, prec, comment_recs)
        posts_written += 1

    result = {
        "vault": str(out),
        "members": len(members),
        "posts": posts_written,
        "comments_failed": comments_failed,
    }
    if getattr(members, "incomplete", False):
        result["members_incomplete"] = True  # truncated walk — not the whole community
    # Same honesty as the list tool: a vault holding the first page of a
    # community you are only a member of must not read as the full roster.
    # Files on disk outlive the call that wrote them, so an unflagged partial
    # pull is the version that gets trusted months later.
    if getattr(members, "capped_by_role", False):
        result["members_capped_by_role"] = True
        result["members_reported_by_skool"] = members.total_members
        result["note"] = (
            "You are a regular member of this community, so only the first page "
            "of members was pulled, the same as Skool's UI shows you. The member "
            "notes in the vault are a partial roster, not the full community. "
            "Posts and comments are unaffected."
        )
    return result


# -- write tools (opt-in) ------------------------------------------------------
# Only registered when CATKNOWS_ALLOW_WRITE=1 is set in the server's env.
# Without the flag these tools don't exist at all from the AI's point of view.

def _csv_arg(value: str) -> list[str]:
    """Split a comma-separated tool argument, honouring ``\\,`` as a literal comma.

    MCP arguments are flat strings, so multi-value parameters are comma-joined.
    That silently breaks on values which legitimately contain a comma — a poll
    option like "Yes, the cat has served me" became two options and posted that
    way, because three options are still inside the valid 2-10 range. Escaping
    is the fix that keeps every existing call working: no separator changes, and
    "a,b" splits exactly as before.
    """
    out, buf, esc = [], [], False
    for ch in value:
        if esc:
            # Only \, is an escape; anything else keeps its backslash, so a
            # Windows path like C:\new stays intact.
            buf.append(ch if ch == "," else "\\" + ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ",":
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if esc:  # trailing lone backslash
        buf.append("\\")
    out.append("".join(buf))
    return [s.strip() for s in out if s.strip()]


if os.environ.get("CATKNOWS_ALLOW_WRITE", "") == "1":

    # Post/comment lockout (2026-08-22 incident). Skool un-lists comments written
    # through the API after they post — the object survives (GET → 200), it just
    # leaves the tree. Measured in two communities, two accounts. Until we know
    # the trigger, the hosted server hands the caller a copy-paste block for posts
    # and comments instead of writing them; the user pastes it in Skool's own
    # editor, so no bot-written post/comment reaches Skool. DMs, edits, deletes and
    # course tools are unaffected — no un-listing seen there.
    #   CATKNOWS_WRITE_MODE=draft_only   turns the lockout on (set it on hosted).
    #   CATKNOWS_WRITE_ALLOW_SLUGS=a,b   communities exempt from it, for the
    #                                    control experiment (e.g. hoomans-9944).
    # Deliberately env-gated, not tied to _http_mode(): the lockout is meant for
    # the hosted deployment (its data-centre egress IP is the leading suspect),
    # so it is enabled by setting the env var on the box. A local stdio install
    # leaves the var unset and keeps posting — local and hosted share the same
    # curl_cffi write path, the only measured difference is the egress IP, and no
    # local write has been observed being un-listed yet. The control experiment
    # is what decides whether local also needs it.
    _WRITE_DRAFT_ONLY = os.environ.get("CATKNOWS_WRITE_MODE", "") == "draft_only"
    _WRITE_ALLOW_SLUGS = frozenset(
        s.strip() for s in os.environ.get("CATKNOWS_WRITE_ALLOW_SLUGS", "").split(",") if s.strip()
    )

    def _post_write_locked(community_slug: str) -> bool:
        """True when a post/comment write must fall back to a copy-paste block.

        Logs the refusal on the way out (board D8). A returned block is NOT a
        write and must never be counted as one, so it gets its own status —
        but that somebody wanted to post is worth knowing, and this is the one
        function all three locked paths (paste, poll, attachment) run through.
        """
        locked = _WRITE_DRAFT_ONLY and community_slug not in _WRITE_ALLOW_SLUGS
        if locked:
            ctx = audit.current_tool()
            audit.record_blocked(ctx, community_slug, "BLOCKED_draft_only")
        return locked

    _COPY_PASTE_REASON = (
        "Posting and commenting through catknows is paused on the hosted server. "
        "Skool removes API-written comments from the thread shortly after they post, "
        "so catknows hands you the finished text to paste into Skool's own editor "
        "instead. Open the post in Skool, paste, and publish — that is a normal "
        "human post and is safe. Direct messages still send normally."
    )

    def _copy_paste(kind: str, community_slug: str, content: str, extra: dict) -> dict:
        """The hosted fallback: return the text to paste, write nothing."""
        return {
            "status": f"NOT {kind} — hosted posting is paused, paste this yourself",
            "why": _COPY_PASTE_REASON,
            "community": community_slug,
            "copy_paste_text": content,
            "before_you_show_this": _HUMANIZE,
            "next_step": (
                "Show the user copy_paste_text and tell them to paste it into the "
                "Skool editor themselves. Do NOT call again with confirm=true — the "
                "server will not post it."
            ),
            **extra,
        }

    # Every draft that carries prose to a human carries this too. The server
    # cannot rewrite anything itself (no model here), so it asks the client's
    # model to do the pass before the user ever sees the text. Patterns from
    # Wikipedia's "Signs of AI writing" via github.com/blader/humanizer (MIT).
    _HUMANIZE = (
        "Before showing this draft, rewrite it so it does not read as AI-written. "
        "Drop: em dashes (use a comma or full stop), bold label lists, "
        "'not just X but Y', forced groups of three, stock words (testament, "
        "delve, showcase, vibrant, crucial, landscape, underscore, tapestry), "
        "openers that announce the point ('Let's dive in', 'Here's the thing'), "
        "and vague sign-offs. Keep every fact, number and name exactly as it is. "
        "Match how the user writes if you have seen their writing."
    )

    def _draft(status: str, key: str, body: dict, *, prose: bool = True,
               not_verified: str = "") -> dict:
        """A draft reply: nothing written yet, here is what would be.

        `prose` is False for drafts a human never reads as text (a move, a
        delete, a course's metadata) — nothing to humanize there.
        `not_verified` is a fixed text for an action with a side effect nobody
        has checked: a warning that lives only in a docstring is not seen by the
        human who confirms, so it travels in the draft itself.
        """
        out = {
            "status": status,
            key: body,
            "next_step": "Show this draft to the user; call again with confirm=true once they approve.",
        }
        if not_verified:
            out["not_verified"] = not_verified
        if prose:
            out["before_you_show_this"] = _HUMANIZE
            voice = _voice()
            if voice:
                out["write_as_the_user"] = voice
        return out

    def _voice() -> dict | None:
        """The account owner's voice guide for prose drafts, if one is configured.

        CATKNOWS_VOICE_FILE names a Markdown file describing how the person
        behind the Skool account writes (length, language, emojis, words they
        never use). Read fresh on every draft, so an edit to the guide takes
        effect without a restart. A configured file that can't be read is
        reported in the draft: a silent fallback would post in a voice the user
        explicitly asked us not to use.
        """
        path = os.environ.get("CATKNOWS_VOICE_FILE", "").strip()
        if not path:
            return None
        try:
            text = Path(path).expanduser().read_text(encoding="utf-8")
        except OSError as e:
            return {"instruction": (
                f"The user's voice guide ({path}) could not be read: {e}. STOP: "
                "tell the user, and do not write this text without the guide.")}
        if text.startswith("---"):
            parts = text.split("---", 2)
            text = parts[2] if len(parts) == 3 else text
        return {
            "instruction": (
                "This text goes out under the user's own name, so it must read as "
                "if they wrote it. First run the humanizer skill on it if you have "
                "one (embedded mode), using voice_guide as the writing sample; "
                "without that skill, apply before_you_show_this. Then rewrite the "
                "draft to match voice_guide: language, length, openings, emojis, "
                "punctuation, words. Keep every fact. Show the user the final "
                "text exactly as it will be sent, and send only that text."
            ),
            "voice_guide": text.strip(),
        }

    def _local_paths(paths: str) -> list[str]:
        """The attachment paths, refused outright on the hosted server.

        Attachments are LOCAL file paths, read off the disk of whoever runs
        this server. Locally that is the user's own machine. On the hosted
        server (per-user sessions on) it is MY disk: any signed-in user could
        name /proc/self/environ or the session store and have the file mailed
        to their own DMs, session key included. A remote user has no files
        here anyway, so the hosted service takes no path attachments at all.
        """
        items = _csv_arg(paths)
        if items and sessions.enabled():
            raise ValueError(
                "File attachments are not available on the hosted service. "
                "Send the text without attachments, or add the file in Skool yourself."
            )
        return items

    def _attachment_preview(paths: str) -> list[dict]:
        """Describe local files for the draft, without uploading anything yet.

        The draft has to show what will be attached BEFORE it is sent, so this
        only stats the files. A missing file must fail here, at preview time —
        not halfway through a confirmed post.
        """
        out = []
        for p in _local_paths(paths):
            f = Path(p)
            if not f.is_file():
                raise ValueError(f"Attachment not found: {p}")
            out.append({
                "file": f.name,
                "type": mimetypes.guess_type(f.name)[0] or "application/octet-stream",
                "size_bytes": f.stat().st_size,
                "path": str(f),
            })
        return out

    def _upload_all(community_slug: str, paths: str) -> str:
        """Upload each local file, return the comma-joined ids Skool expects."""
        client = _get_client()
        ids = [
            client.upload_file(community_slug, p)["id"]
            for p in _local_paths(paths)
        ]
        return ",".join(ids)

    @mcp.tool()
    def create_post(
        community_slug: str,
        title: str,
        content: str,
        labels: str = "",
        video_links: str = "",
        attachments: str = "",
        poll_options: str = "",
        notify_members: bool = False,
        confirm: bool = False,
    ) -> dict:
        """Create a REAL post in the community, as the logged-in user, visible to all members.

        attachments is a comma-separated list of LOCAL FILE PATHS (images, PDFs,
        GIFs — a .gif uploads and animates fine). They are uploaded to Skool only
        when confirm=true; the draft just lists their name, type and size.
        The paths are read by THIS server, so on the hosted deployment they must
        exist on the server's disk — an AI client cannot attach a file from its
        own machine. Hosted users attach media in Skool's editor instead.
        poll_options turns the post into a native Skool poll: a comma-separated
        list of 2–10 answer options (e.g. "Yes,No"). The poll is created on
        Skool only when confirm=true. An option that contains a comma must
        escape it as \\, — otherwise it splits into two options. ALWAYS check
        the option list in the draft before confirming.

        Draft-first: with confirm=false (the default) NOTHING is posted — you get
        the exact payload back to show the user for approval. Only call again with
        confirm=true after the user explicitly approved that draft.
        notify_members=true EMAILS EVERY MEMBER (Skool broadcast) — set it only if
        the user explicitly asked to email everyone.

        NEVER run this on a schedule or in a loop. Skool removes API-written posts
        and comments from the thread some time after they post, and the one
        measured case followed a repeating task: the first two rounds survived,
        the third was removed. A single post the user asked for is ordinary use;
        a recurring one is what gets flagged. If the user wants posting as a
        routine, tell them to run catknows locally instead of asking for a
        schedule here.
        """
        options = _csv_arg(poll_options)
        if options and not 2 <= len(options) <= 10:
            raise ValueError(
                f"A poll needs 2–10 options, got {len(options)}: {options}. "
                "A comma inside an option splits it — escape it as \\,"
            )
        draft = {
            "community": community_slug,
            "title": title,
            "content": content,
            "labels": labels,
            "video_links": video_links,
            "attachments": _attachment_preview(attachments),
            "poll_options": options,
            "notify_members (emails everyone!)": notify_members,
        }
        if _post_write_locked(community_slug):
            if options or attachments or video_links:
                # A poll/attachment/video can't be reproduced by pasting text.
                return {
                    "status": "NOT posted — hosted posting is paused",
                    "why": _COPY_PASTE_REASON,
                    "note": "This post uses a poll, attachment or video, which a "
                            "paste can't reproduce. Create it in Skool directly.",
                    "would_post": draft,
                }
            return _copy_paste("posted", community_slug,
                               f"{title}\n\n{content}" if title else content,
                               {"title": title})
        if not confirm:
            return _draft("DRAFT — nothing was posted", "would_post", draft)
        client = _get_client()
        poll_id = client.create_poll(community_slug, options)["poll_id"] if options else ""
        created = client.create_post(
            community_slug,
            title,
            content,
            labels=labels,
            video_links=video_links,
            attachments=_upload_all(community_slug, attachments),
            poll=poll_id,
            notify_members=notify_members,
        )
        return {"status": "posted", "post": _safe_raw(created)}

    @mcp.tool()
    def create_comment(
        community_slug: str,
        post_id: str,
        content: str,
        parent_comment_id: str = "",
        attachments: str = "",
        confirm: bool = False,
    ) -> dict:
        """Write a REAL comment under a post, or a reply under a comment, as the logged-in user.

        Leave parent_comment_id empty to comment on the post itself; set it to
        a comment's id (from get_post_comments) to reply beneath that comment.
        post_id is always the post the thread belongs to, even for a reply.
        Skool threads are only two levels deep: a reply to a REPLY is posted
        under its top-level comment with an @-mention of the person first (what
        Skool's own UI does — hung under the reply, Skool deletes it). The
        draft shows when that happens.
        attachments is a comma-separated list of LOCAL FILE PATHS, uploaded only
        when confirm=true.

        Draft-first: with confirm=false (the default) NOTHING is written — you
        get the draft back to show the user. Only call again with confirm=true
        after the user explicitly approved it.

        NEVER run this on a schedule or in a loop. Skool removes API-written
        comments from the thread some time after they post, and the one measured
        case followed a repeating task: the first two rounds survived, the third
        was removed. A single comment the user asked for is ordinary use; a
        recurring one is what gets flagged. If the user wants commenting as a
        routine, tell them to run catknows locally instead of asking for a
        schedule here.
        """
        from .client import with_mention

        # Only a reply needs Skool to look up where it may hang.
        target = (_get_client().reply_target(community_slug, post_id, parent_comment_id)
                  if parent_comment_id else {"flattened": False, "mention": ""})
        draft = {
            "community": community_slug,
            "post_id": post_id,
            "content": with_mention(target["mention"], content),
            "replying_to_comment": parent_comment_id or "(none — top-level comment on the post)",
            "attachments": _attachment_preview(attachments),
        }
        if target["flattened"]:
            draft["threading"] = (
                f"{parent_comment_id} is a reply, and Skool threads are two levels "
                f"deep — this goes under its top-level comment {target['parent_id']} "
                "with an @-mention, like Skool's own reply button does.")
        if _post_write_locked(community_slug):
            if attachments:
                return {
                    "status": "NOT written — hosted posting is paused",
                    "why": _COPY_PASTE_REASON,
                    "note": "This comment has an attachment, which a paste can't "
                            "reproduce. Add it in Skool directly.",
                    "would_comment": draft,
                }
            return _copy_paste("written", community_slug, content,
                               {"post_id": post_id,
                                "replying_to_comment": parent_comment_id or "(top-level)"})
        if not confirm:
            return _draft("DRAFT — nothing was written", "would_comment", draft)
        created = _get_client().create_comment(
            community_slug, post_id, content,
            parent_comment_id=parent_comment_id,
            attachments=_upload_all(community_slug, attachments),
        )
        return {"status": "commented", "comment": _safe_raw(created)}

    def _edit_draft(post_id: str, title: str, content: str) -> dict:
        """Old -> new for one post/comment, without writing anything.

        Shared by edit_post and edit_comment: a comment IS a post on Skool, so
        the draft, the lookup and the write are the same in both directions.
        """
        current = _get_client().post_by_id(post_id) or {}
        meta = current.get("metadata") or {}
        change: dict = {}
        if title is not None:
            change["title"] = {"from": meta.get("title", ""), "to": title}
        if content is not None:
            change["content"] = {"from": meta.get("content", ""), "to": content}
        return {"post_id": post_id, "changes": change or "(nothing would change)"}

    @mcp.tool()
    def edit_post(community_slug: str, post_id: str, title: str = "",
                  content: str = "", confirm: bool = False) -> dict:
        """Edit the title and/or body of an EXISTING post, as the logged-in user.

        Leave title or content empty to keep the current value — an empty
        string means "unchanged", not "blank it". Fields you never mention
        (category, attachments) are preserved too.

        Draft-first: with confirm=false (the default) NOTHING is changed — you
        get the current text and the proposed text back to show the user. Only
        call again with confirm=true after they approved it.
        """
        new_title = title or None
        new_content = content or None
        if not confirm:
            return _draft("DRAFT — nothing was changed", "would_edit",
                          _edit_draft(post_id, new_title, new_content))
        client = _get_client()
        client.group_id_for(community_slug, for_write=True)  # archived guard
        updated = client.update_post(post_id, title=new_title, content=new_content)
        return {"status": "edited", "post": _safe_raw(updated)}

    @mcp.tool()
    def edit_comment(community_slug: str, comment_id: str, content: str = "",
                     confirm: bool = False) -> dict:
        """Edit the text of an EXISTING comment or reply, as the logged-in user.

        comment_id comes from get_post_comments. A comment has no title, only
        text. Same draft-first rule as edit_post: confirm=false changes nothing.
        """
        new_content = content or None
        if not confirm:
            return _draft("DRAFT — nothing was changed", "would_edit",
                          _edit_draft(comment_id, None, new_content))
        client = _get_client()
        client.group_id_for(community_slug, for_write=True)  # archived guard
        updated = client.update_post(comment_id, content=new_content)
        return {"status": "edited", "comment": _safe_raw(updated)}

    def _delete_draft(post_id: str, kind: str) -> dict:
        """What exactly would be deleted — id alone is not reviewable."""
        current = _get_client().post_by_id(post_id) or {}
        meta = current.get("metadata") or {}
        return {
            f"{kind}_id": post_id,
            "title": meta.get("title", ""),
            "content": (meta.get("content", "") or "")[:300],
            "created_at": current.get("created_at", ""),
        }

    @mcp.tool()
    def delete_post(community_slug: str, post_id: str, confirm: bool = False) -> dict:
        """PERMANENTLY delete a post. This cannot be undone.

        confirm=false (the default) deletes nothing and returns the post's
        title and text so the user can see WHAT would disappear before saying
        yes. Deleting somebody else's post needs moderator rights and is
        untested.
        """
        if not confirm:
            return {
                "status": "NOT DELETED — confirmation required",
                "would_delete": _delete_draft(post_id, "post"),
                "next_step": "Show this to the user; call again with confirm=true "
                             "only after they explicitly approved deleting it.",
            }
        _get_client().delete_post(post_id)
        return {"status": "deleted", "post_id": post_id}

    @mcp.tool()
    def delete_comment(community_slug: str, comment_id: str,
                       confirm: bool = False) -> dict:
        """PERMANENTLY delete a comment or reply. This cannot be undone.

        Same confirm-first rule as delete_post: confirm=false shows the text
        that would disappear and deletes nothing.
        """
        if not confirm:
            return {
                "status": "NOT DELETED — confirmation required",
                "would_delete": _delete_draft(comment_id, "comment"),
                "next_step": "Show this to the user; call again with confirm=true "
                             "only after they explicitly approved deleting it.",
            }
        _get_client().delete_post(comment_id)
        return {"status": "deleted", "comment_id": comment_id}

    @mcp.tool()
    def review_join_request(community_slug: str, member_id: str, decision: str,
                            confirm: bool = False) -> dict:
        """Approve or decline ONE pending join request (member_id from list_join_requests).

        decision is "approve" or "decline". Draft-first: confirm=false shows
        who the request is from and changes nothing. Only call again with
        confirm=true after the user decided on this person. Ids that are not
        in the join queue right now are refused: the same Skool call would
        change an existing member's role.
        """
        if decision not in ("approve", "decline"):
            return {"status": "REFUSED — decision must be 'approve' or 'decline'"}
        match = next((r for r in _join_queue(community_slug)
                      if r["member_id"] == member_id), None)
        if match is None:
            return {"status": "REFUSED — this member_id is not in the join queue of "
                              f"{community_slug}; nothing was changed. Re-read "
                              "list_join_requests; it may have been handled already."}
        if not confirm:
            return _draft("DRAFT — nothing was changed", f"would_{decision}",
                          match, prose=False)
        _get_client().decide_join_request(member_id, decision)
        return {"status": "approved" if decision == "approve" else "declined",
                "member_id": member_id, "name": match["name"]}

    @mcp.tool()
    def send_dm(
        channel_id: str, content: str, attachments: str = "", confirm: bool = False
    ) -> dict:
        """Send a REAL direct message into an existing chat channel (ids via list_chat_channels).

        attachments is a comma-separated list of LOCAL FILE PATHS (images, PDFs,
        GIFs, ...), uploaded only when confirm=true; the draft just lists them.

        Draft-first: with confirm=false (the default) NOTHING is sent — you get a
        preview back. Only call again with confirm=true after the user explicitly
        approved the message text.
        """
        if not confirm:
            return _draft("DRAFT — nothing was sent", "would_send", {
                "channel_id": channel_id,
                "content": content,
                "attachments": _attachment_preview(attachments),
            })
        client = _get_client()
        ids: list[str] = []
        if _local_paths(attachments):
            # A DM has no community slug — the file is owned by the group the
            # channel belongs to, so read that off the channel itself. The
            # messages endpoint returns the channel object in one call; listing
            # /self/chat-channels instead cannot work: Skool refuses limit > 30
            # there, and accounts have hundreds of channels.
            from .http import REJECTED_STATUSES, SkoolHTTPError, SkoolSecurityChallenge

            try:
                gid = (client.chat_messages(channel_id, count=1)
                       .get("channel") or {}).get("group_id") or ""
            except SkoolSecurityChallenge:
                raise  # not an "unknown channel": Skool asked a security question
            except SkoolHTTPError as e:
                # 401/403/429 or a network error: not an "unknown channel" either,
                # and nothing more may go out after them.
                if e.status in REJECTED_STATUSES or not e.status:
                    raise
                gid = ""
            if not gid:
                raise ValueError(f"Unknown channel {channel_id} — cannot attach files.")
            ids = [
                client.upload_file(gid, p)["id"]
                for p in _local_paths(attachments)
            ]
        sent = client.send_dm(channel_id, content, attachments=ids)
        return {"status": "sent", "message": _safe_raw(sent)}

    # -- classroom writes (docs/API.md §7) ------------------------------------

    @mcp.tool()
    def create_course(
        community_slug: str,
        title: str,
        description: str = "",
        privacy: int = 0,
        min_access_level: int = 0,
        min_tier: int = 0,
        draft: bool = True,
        confirm: bool = False,
    ) -> dict:
        """Create a classroom course (a top-level tile) in the community.

        description is plain text. privacy: 0 open to all members, 1 unlocks at
        gamification level min_access_level, 2 private/tier-gated (min_tier).
        draft=true (default) keeps it INVISIBLE to members until publish_course.

        Draft-first: with confirm=false NOTHING is created — you get the payload
        back to show the user. Call again with confirm=true once approved.
        """
        would = {"community": community_slug, "title": title,
                 "description": description, "privacy": privacy,
                 "min_access_level": min_access_level, "min_tier": min_tier,
                 "created_as_invisible_draft": draft}
        if not confirm:
            return _draft("DRAFT — nothing was created", "would_create", would)
        created = _get_client().create_course(
            community_slug, title, desc=description, privacy=privacy,
            min_access_level=min_access_level, min_tier=min_tier, draft=draft)
        return {"status": "created", "course": _safe_raw(created)}

    @mcp.tool()
    def create_course_item(
        course_id: str,
        title: str,
        parent_id: str = "",
        folder: bool = False,
        content: str = "",
        video_link: str = "",
        draft: bool = False,
        confirm: bool = False,
    ) -> dict:
        """Add a folder (folder=true) or a page to a course (ids via get_course_tree).

        parent_id: empty = directly in the course, or a folder's id. content is
        the page body — plain text works (it is converted to Skool's rich-text
        format), or pass a ready "[v2]…" TipTap string. video_link embeds a
        YouTube/Loom/Vimeo video on the page.

        Draft-first: confirm=false previews, confirm=true writes.
        """
        would = {"course_id": course_id, "title": title,
                 "parent_folder": parent_id or "(course root)",
                 "kind": "folder" if folder else "page",
                 "content": content[:500], "video_link": video_link, "draft": draft}
        if not confirm:
            return _draft("DRAFT — nothing was created", "would_create", would)
        created = _get_client().create_course_item(
            course_id, title, parent_id=parent_id, folder=folder,
            content=content, video_link=video_link, draft=draft)
        return {"status": "created", "item": _safe_raw(created)}

    @mcp.tool()
    def update_course_item(
        item_id: str,
        title: str = "",
        content: str = "",
        privacy: int = -1,
        min_access_level: int = -1,
        min_tier: int = -1,
        confirm: bool = False,
    ) -> dict:
        """Rename or rewrite a course, folder or page (ids via get_course_tree).

        Only the parameters you set are changed; for courses the current
        settings are read first so nothing resets (Skool wipes `privacy` on
        partial updates — handled here). For PAGES, title AND content must BOTH
        be passed: Skool drops omitted page fields (read the current values
        from get_course_tree first). content: plain text or "[v2]…" TipTap.

        Draft-first: confirm=false previews, confirm=true writes.
        """
        fields: dict = {}
        if title:
            fields["title"] = title
        if content:
            fields["desc"] = content
        if privacy >= 0:
            fields["privacy"] = privacy
        if min_access_level >= 0:
            fields["min_access_level"] = min_access_level
        if min_tier >= 0:
            fields["min_tier"] = min_tier
        if not fields:
            raise ValueError("Nothing to update — pass title, content or a setting.")
        if not confirm:
            return _draft("DRAFT — nothing was changed", "would_update",
                          {"item_id": item_id, **fields},
                          prose="desc" in fields or "title" in fields)
        updated = _get_client().update_course_item(item_id, fields)
        return {"status": "updated", "item": _safe_raw(updated)}

    @mcp.tool()
    def publish_course(course_id: str, published: bool = True, confirm: bool = False) -> dict:
        """Publish a course (visible to all members!) or pull it back to an invisible draft.

        Draft-first: confirm=false previews, confirm=true switches the state.
        """
        if not confirm:
            return {"status": "DRAFT — nothing was changed",
                    "would_set": {"course_id": course_id,
                                  "state": "published (VISIBLE to members)" if published else "draft (hidden)"},
                    "next_step": "Show this to the user; call again with confirm=true once they approve."}
        _get_client().set_course_state(course_id, published)
        return {"status": "published" if published else "unpublished", "course_id": course_id}

    @mcp.tool()
    def move_course_item(item_id: str, position: int, confirm: bool = False) -> dict:
        """Reorder a course/folder/page among its siblings (position = 0-based target index).

        This does not change the parent. To move a page into a folder or out of
        one, use move_course_to_folder (read from Skool's page code, not run live
        yet); until that has been tried, recreating the page in the target folder
        is the proven way. Draft-first: confirm=true executes.
        """
        if not confirm:
            return {"status": "DRAFT — nothing was moved",
                    "would_move": {"item_id": item_id, "to_position": position},
                    "next_step": "Call again with confirm=true to move it."}
        _get_client().move_course_item(item_id, position)
        return {"status": "moved", "item_id": item_id, "position": position}

    @mcp.tool()
    def delete_course_item(item_id: str, client_id: str = "", confirm: bool = False) -> dict:
        """DELETE a course, folder or page. Cannot be undone.

        Deleting a COURSE deletes everything in it and additionally needs
        client_id: a 32-hex id that passed Skool's email-code check
        (docs/API.md §7.6) — without it Skool answers "invalid client ID".
        Deleting a FOLDER does NOT delete its pages — they move to the course
        root. Delete the pages first if they should go too.

        Draft-first: confirm=false previews, confirm=true deletes.
        """
        if not confirm:
            return {"status": "DRAFT — nothing was deleted",
                    "would_delete": {"item_id": item_id},
                    "next_step": "Show this to the user; call again with confirm=true once they approve."}
        _get_client().delete_course_item(item_id, client_id=client_id)
        return {"status": "deleted", "item_id": item_id}

    # -- course settings: drip, folder, share key, import (docs/API.md §7.8) ------
    # Read from Skool's page code, not run live by catknows. Each draft sends
    # nothing and does not contact Skool, and carries a fixed `not_verified` text.
    # No community slug on three of them (like publish_course): the ids name the
    # target. The share key is a credential: `import_course` never shows it back,
    # `create_course_share_key` hands it to its caller and nothing else.

    _NV_DRIP = (
        "Not verified: who the server lets switch drip (the web client shows it in a "
        "page's menu); whether a course or folder id works as well as a page id; and "
        "what drip does to members who already have access. The server wants days "
        "1-999 in both directions, so switching off sends 1 when no days are given. "
        "The current setting is not read first, so the draft cannot show it."
    )
    _NV_MOVE_TO_FOLDER = (
        "Not verified, not run: where the page lands among the new folder's pages (the "
        "call sets the parent only; `move_course_item` reorders afterwards); whether "
        "Skool refuses a parent that is not a folder, or one of another course (catknows "
        "does not check it, the ids are not read back); what Skool answers. The web "
        "client moves pages into folders, and never a folder into a folder."
    )
    _NV_SHARE_KEY = (
        "Not verified, not run: what Skool answers besides `code`, and whether a second "
        "call gives the same key or a new one. The key is an ACCESS CODE: anybody who "
        "holds it can duplicate the whole course into any community (Skool's own warning "
        "in the dialog), so hand it only to the person the user names and never post or "
        "store it. The web client offers it to the owner of the community only (read from "
        "its code). catknows keeps the key out of its audit log (only a short fingerprint "
        "of the answer is logged) and out of error messages; this tool's answer is the "
        "one place it appears."
    )
    _NV_IMPORT = (
        "Not verified, not run: what Skool answers (the web client then only says 'Import "
        "in progress' and waits for a notice, only 1 course at a time), whether the copy "
        "arrives as a draft or already PUBLISHED and visible to members, where it lands in "
        "the classroom, and whether importing needs a paid plan. Check get_classroom "
        "afterwards, and unpublish the copy with publish_course if it is visible and "
        "should not be. catknows keeps the key out of its audit log and error messages, "
        "and this draft does not show it; it still sits in this conversation, so do not "
        "paste it anywhere else."
    )

    @mcp.tool()
    def set_course_drip(course_id: str, enabled: bool, days: int = 0,
                        confirm: bool = False) -> dict:
        """Switch drip (timed release) on or off for ONE page, and set after how many days it opens.

        With drip on, a member sees the page `days` days after they join. Skool's
        web client offers this in a PAGE's menu ("Drip status"), so course_id is a
        page's id (from get_course_tree). enabled=true needs days from 1 to 999;
        enabled=false needs none (Skool wants a number anyway, so 1 is sent).
        There is no default direction: say which one you mean.

        Draft-first: with confirm=false NOTHING is sent and Skool is not
        contacted. The draft shows the id, not the page's title, and not the
        current setting. Call again with confirm=true once the user approved.
        """
        from .client import SkoolClient

        days = SkoolClient.check_course_drip(enabled, days)
        SkoolClient.course_action_path(course_id, "update-drip")  # refuse a bad id up front
        if not confirm:
            return _draft(
                "DRAFT — nothing was changed", "would_set_drip",
                {"course_id": course_id, "enabled": enabled, "days": days,
                 "does": (f"release the page {days} days after a member joins" if enabled
                          else "switch drip off for the page")},
                prose=False, not_verified=_NV_DRIP)
        answer = _get_client().set_course_drip(course_id, enabled, days)
        return {"status": "drip_set", "course_id": course_id, "enabled": enabled,
                "days": days, "answer": _safe_raw(answer)}

    @mcp.tool()
    def move_course_to_folder(course_id: str, parent_id: str, confirm: bool = False) -> dict:
        """Move a page into a folder, or back out to the course root (ids via get_course_tree).

        course_id is the page's id. parent_id is the folder's id (type `set` in
        get_course_tree), or the id of the course itself to take the page out of
        its folder. Only the parent changes; the page's place among its new
        siblings is not set (reorder with move_course_item afterwards). Skool's
        web client moves pages only, never a folder into a folder.

        Draft-first: with confirm=false NOTHING is sent and Skool is not
        contacted. The draft shows the ids, not the titles. Call again with
        confirm=true once the user approved.
        """
        from .client import SkoolClient

        SkoolClient.course_parent_path(course_id, parent_id)  # bad or equal ids up front
        if not confirm:
            return _draft(
                "DRAFT — nothing was moved", "would_move",
                {"course_id": course_id, "parent_id": parent_id,
                 "does": ("make parent_id the new parent of course_id (a folder, or the "
                          "course itself for 'no folder')")},
                prose=False, not_verified=_NV_MOVE_TO_FOLDER)
        answer = _get_client().move_course_to_folder(course_id, parent_id)
        return {"status": "moved", "course_id": course_id, "parent_id": parent_id,
                "answer": _safe_raw(answer)}

    @mcp.tool()
    def create_course_share_key(course_id: str, confirm: bool = False) -> dict:
        """Get the SHARE KEY of one of your courses: an access code that lets whoever holds it duplicate the course into any community.

        This is Skool's "Share course key" (owner only in the web client); the
        key is the answer. THE KEY IS A CREDENTIAL: give it only to the person
        the user names, never post, store or log it. The other side imports it
        with import_course. course_id is the course's id (get_classroom).

        Draft-first: with confirm=false NOTHING is sent and Skool is not
        contacted. Call again with confirm=true once the user approved.
        """
        from .client import SkoolClient

        SkoolClient.course_action_path(course_id, "clone-code")  # refuse a bad id up front
        if not confirm:
            return _draft(
                "DRAFT — nothing was requested", "would_create_share_key",
                {"course_id": course_id,
                 "does": ("ask Skool for this course's share key; whoever holds the key can "
                          "duplicate the whole course into any community")},
                prose=False, not_verified=_NV_SHARE_KEY)
        answer = _get_client().create_course_share_key(course_id)
        code = answer.get("code") if isinstance(answer, dict) else None
        if not isinstance(code, str) or not code:
            from .http import SkoolHTTPError
            raise SkoolHTTPError(
                "Skool answered the share-key request without a `code` (the answer is not "
                "shown: it may hold the key). The request was sent, so a key may exist.")
        return {"status": "share_key_created", "course_id": course_id, "code": code,
                "warning": ("This key is an access code: whoever holds it can duplicate the "
                            "whole course into any community. Hand it only to the person the "
                            "user names; do not post, store or paste it anywhere else.")}

    @mcp.tool()
    def import_course(course_id: str, community_slug: str, clone_code: str,
                      confirm: bool = False) -> dict:
        """Import (duplicate) a course INTO this community with a share key its owner gave you.

        clone_code is the 64-character share key (from the owner's
        create_course_share_key). course_id is the SOURCE course: the first 32
        characters of the key (Skool's own form takes it from there), and it
        must match. The copy is made on Skool's side and can take a while; only
        1 course may be imported at a time. Whether it arrives as a draft or
        already visible to members is not known: check get_classroom afterwards.
        Use a key only if its owner gave it to the user for this.

        Draft-first: with confirm=false NOTHING is sent and Skool is not
        contacted. The draft shows the key's length, never the key. Call again
        with confirm=true once the user approved.
        """
        from .client import SkoolClient

        SkoolClient.check_import_course(course_id, clone_code)  # refuse bad input up front
        if not confirm:
            return _draft(
                "DRAFT — nothing was imported", "would_import",
                {"community": community_slug, "course_id": course_id,
                 "clone_code": f"{len(clone_code)} characters (not shown)",
                 "does": "copy the whole course this key belongs to into the community"},
                prose=False, not_verified=_NV_IMPORT)
        answer = _get_client().import_course(course_id, community_slug, clone_code)
        return {"status": "import_started", "community": community_slug,
                "course_id": course_id, "answer": _safe_raw(answer)}

    # -- calendar events ---------------------------------------------------------
    # Read from Skool's page code, not yet run live (docs/API.md §5.11). The web
    # client shows the buttons to community admins only; whether the server
    # enforces the same is not verified.

    def _calendar_event_object(
        title: str, description: str, start_time: str, end_time: str, timezone: str,
        reminder_disabled: int, location_type: int, location_info: str,
        privacy: str, recurrence: str,
    ) -> dict:
        """Skool's event object from the flat tool arguments.

        Whatever was not given (empty text, 0, -1) is left out, not sent as blank.
        Times and timezone go through as given: their wire format is not documented.
        """
        meta: dict = {}
        if title:
            meta["title"] = title
        if description:
            meta["description"] = description
        if timezone:
            meta["timezone"] = timezone
        if reminder_disabled >= 0:
            meta["reminder_disabled"] = reminder_disabled
        if location_type:
            loc: dict = {"location_type": location_type}
            if location_info:
                loc["location_info"] = location_info
            # JSON text, as Skool's form sends it (no spaces, like JSON.stringify).
            meta["location"] = json.dumps(loc, separators=(",", ":"), ensure_ascii=False)
        elif location_info:
            raise ValueError("location_info needs a location_type.")
        if privacy:
            meta["privacy"] = privacy
        event: dict = {}
        if start_time:
            event["start_time"] = start_time
        if end_time:
            event["end_time"] = end_time
        if meta:
            event["metadata"] = meta
        if recurrence:
            try:
                event["recurrence"] = json.loads(recurrence)
            except ValueError:
                raise ValueError("recurrence must be JSON text of an object.") from None
            if not isinstance(event["recurrence"], dict):
                raise ValueError("recurrence must be JSON text of an object.")
        from .client import SkoolClient
        SkoolClient.check_calendar_event(event)  # a draft must not promise what would fail
        return event

    # Fixed texts for the drafts: what only a live run can settle travels with the
    # draft, because a docstring is not seen by the human who confirms.
    # A draft does not contact Skool, so it cannot know the community's id. The
    # body it shows is built by the same call as the one that is sent
    # (`SkoolClient.calendar_event_body`), with this text in place of the id.
    _GROUP_ID_ON_CONFIRM = "(the community's id, looked up from community_slug on confirm)"
    _NV_CALENDAR_GROUP_ID = (
        " `event` is the request body as it goes out, except group_id: that is the "
        "community's id, which catknows looks up from community_slug only on confirm, "
        "so the draft shows a placeholder for it."
    )
    _NV_CALENDAR_CREATE = (
        "Not verified live: which fields Skool requires, and the format it wants for "
        "start_time, end_time and timezone (they go out exactly as shown)."
        + _NV_CALENDAR_GROUP_ID
    )
    _NV_CALENDAR_UPDATE = (
        "Not verified: whether Skool keeps or resets a field this update leaves out "
        "(its own form always sends the whole event), so every field the event should "
        "keep must be in this draft. The current event is not read back."
        + _NV_CALENDAR_GROUP_ID
    )
    _NV_CALENDAR_DELETE = (
        "This draft cannot show the event's title. Make sure the user knows which "
        "event belongs to this id before they confirm."
    )

    def _calendar_scope(from_occurrence_id: str) -> str:
        if from_occurrence_id:
            return "this occurrence and all following ones of the series"
        return "the whole event (every occurrence)"

    @mcp.tool()
    def create_calendar_event(
        community_slug: str,
        title: str = "",
        start_time: str = "",
        end_time: str = "",
        description: str = "",
        timezone: str = "",
        reminder_disabled: int = -1,
        location_type: int = 0,
        location_info: str = "",
        privacy: str = "",
        recurrence: str = "",
        confirm: bool = False,
    ) -> dict:
        """Create a calendar event in the community, as the logged-in user.

        title at most 30 characters, description at most 300. Empty / 0 / -1 means
        "not sent". start_time, end_time and timezone go to Skool exactly as you
        pass them (their format is not documented, so nothing is converted).
        reminder_disabled: 0 = reminder email 1 day before, 1 = no reminder.
        location_type: 1 Zoom, 2 Meet, 3 Address, 4 Link (location_info = the
        link or address), 5 Skool call, 6 Skool webinar (5 and 6 take NO
        location_info: Skool assigns the call).
        privacy: JSON text, {"privacy_type": 0} all members, 1 on/above a level
        (+ "min_access_level" 1-9), 2 a course (+ "access_course"), 3 on/above a
        tier (+ "min_tier" 1-3). recurrence: JSON text of {"frequency": "daily" |
        "weekly" | "monthly" | "yearly", "interval", "days", "month_day",
        "position", "until", "count"}.

        Draft-first: with confirm=false NOTHING is created and Skool is not
        contacted. The draft's `event` is the request body; its group_id is a
        placeholder, because the community's id is looked up only on confirm.
        Call again with confirm=true once the user approved.
        """
        event = _calendar_event_object(title, description, start_time, end_time, timezone,
                                       reminder_disabled, location_type, location_info,
                                       privacy, recurrence)
        if not confirm:
            from .client import SkoolClient
            return _draft("DRAFT — nothing was created", "would_create",
                          {"community": community_slug,
                           "event": SkoolClient.calendar_event_body(
                               _GROUP_ID_ON_CONFIRM, event)},
                          not_verified=_NV_CALENDAR_CREATE)
        created = _get_client().create_calendar_event(community_slug, event)
        return {"status": "created", "event": _safe_raw(created)}

    @mcp.tool()
    def update_calendar_event(
        community_slug: str,
        event_id: str,
        title: str = "",
        start_time: str = "",
        end_time: str = "",
        description: str = "",
        timezone: str = "",
        reminder_disabled: int = -1,
        location_type: int = 0,
        location_info: str = "",
        privacy: str = "",
        recurrence: str = "",
        from_occurrence_id: str = "",
        confirm: bool = False,
    ) -> dict:
        """Change an EXISTING calendar event, as the logged-in user.

        Same fields and limits as create_calendar_event. Only what you set is
        sent (plus the community's group_id). ⚠ Skool's own form always sends
        the whole event; whether a field you leave out is kept or reset is not
        verified, so pass every field the event should keep. from_occurrence_id
        empty = the whole event; set = that occurrence and all following ones
        of the series.

        Draft-first: with confirm=false NOTHING is changed and Skool is not
        contacted. The draft shows the ids and, as `event`, the request body
        (the new values; group_id is a placeholder, looked up only on confirm);
        the current event is not read back. Call again with confirm=true once
        approved.
        """
        event = _calendar_event_object(title, description, start_time, end_time, timezone,
                                       reminder_disabled, location_type, location_info,
                                       privacy, recurrence)
        if not event:
            raise ValueError("Nothing to update: pass at least one field.")
        from .client import SkoolClient
        SkoolClient.calendar_event_path(event_id, from_occurrence_id)  # refuse a bad id up front
        if not confirm:
            meta = event.get("metadata", {})
            return _draft("DRAFT — nothing was changed", "would_update",
                          {"community": community_slug, "event_id": event_id,
                           "from_occurrence_id": from_occurrence_id,
                           "applies_to": _calendar_scope(from_occurrence_id),
                           "event": SkoolClient.calendar_event_body(
                               _GROUP_ID_ON_CONFIRM, event)},
                          prose="title" in meta or "description" in meta,
                          not_verified=_NV_CALENDAR_UPDATE)
        client = _get_client()
        group_id = client.group_id_for(community_slug, for_write=True)  # archived guard
        updated = client.update_calendar_event(
            event_id, SkoolClient.calendar_event_body(group_id, event),
            from_occurrence_id=from_occurrence_id)
        return {"status": "updated", "event": _safe_raw(updated)}

    @mcp.tool()
    def delete_calendar_event(community_slug: str, event_id: str,
                              from_occurrence_id: str = "", confirm: bool = False) -> dict:
        """PERMANENTLY delete a calendar event. This cannot be undone.

        from_occurrence_id empty = the whole event, every occurrence; set = that
        occurrence and all following ones of the series.

        confirm=false (the default) deletes nothing and does not contact Skool. The
        draft shows the id and what it covers; the event's title is not read
        back, so make sure the user knows which event this id is.
        """
        from .client import SkoolClient
        SkoolClient.calendar_event_path(event_id, from_occurrence_id)  # refuse a bad id up front
        if not confirm:
            return _draft("DRAFT — nothing was deleted", "would_delete",
                          {"community": community_slug, "event_id": event_id,
                           "from_occurrence_id": from_occurrence_id,
                           "applies_to": _calendar_scope(from_occurrence_id)},
                          prose=False, not_verified=_NV_CALENDAR_DELETE)
        client = _get_client()
        client.group_id_for(community_slug, for_write=True)  # archived guard
        client.delete_calendar_event(event_id, from_occurrence_id=from_occurrence_id)
        return {"status": "deleted", "event_id": event_id,
                "from_occurrence_id": from_occurrence_id}

    # -- small post actions ------------------------------------------------------
    # Read from Skool's page code, not run live by catknows (docs/API.md §5.12).
    # Skool calls a category a "label". A tool takes the community slug for the
    # archived-community guard and the audit line; the id of the post, comment
    # or category is what names the target. Each draft sends nothing and does
    # not contact Skool; actions with an unchecked side effect carry a fixed
    # `not_verified` text in the draft.

    def _skool():
        """The SkoolClient class, imported on use (its HTTP stack is not needed to list tools)."""
        from .client import SkoolClient
        return SkoolClient

    _NV_VOTE = (
        "Not verified: what Skool does when `old` is not the vote it has stored for "
        "this account (the call sends exactly the old and new you passed). Skool "
        "answers a quick series of likes with 429 'Liking too fast'."
    )
    _NV_PIN_POST = (
        "Not verified: catknows sends an empty object {} here, as the autopilot does, "
        "while Skool's own client sends no body. The autopilot got HTTP 400 once in 8 "
        "days, cause unclear. The web client offers pinning to group admins only; the "
        "server rule is not verified."
    )
    _NV_CREATE_CATEGORY = (
        "Not verified: Skool's category form stops at 10 categories (the server's "
        "limit is not known) and catknows does not count them, so whether Skool refuses "
        "one more shows only in its answer. No description is sent. The draft shows "
        "the values flat; what is sent is {group_id, metadata}."
    )
    _NV_RENAME_CATEGORY = (
        "Not verified: only display_name is sent, and Skool's own form always sends "
        "name, sort and write role together, so a name-only update is not something "
        "the web client does. Whether Skool then keeps the category's sort and write "
        "role is not checked live. The current name is not read back."
    )
    _NV_DELETE_CATEGORY = (
        "Not verified: what Skool answers when the category still has posts and no "
        "migrate_to is given (its web client does not offer that), and what happens "
        "to the last category of a community. Skool's client reads the category "
        "first and then deletes; catknows sends the delete alone. The name is not "
        "read back. Deleting cannot be undone."
    )
    _NV_ABOUT = (
        "Not verified: which format Skool wants for the About text (plain text or "
        '"[v2]" TipTap): it goes out exactly as shown. This REPLACES the whole About '
        "description. Skool's editor allows 1000 plain characters; a longer text is "
        "probably refused."
    )
    _NV_COMMENTS = (
        "Not verified: who the server lets switch comments (the web client offers it to "
        "admins and moderators) and whether existing comments stay visible while "
        "comments are off. Skool's page code handles a 409 ('Comments have been "
        "disabled') for a new comment on such a post (read from the code, not run)."
    )
    _NV_PIN_COMMENT = (
        "Not verified: the web client allows one pinned comment per post; whether a "
        "second pin is refused or replaces the first is the server's rule. post_id is "
        "the post the comment sits under, comment_id the comment's own id."
    )

    @mcp.tool()
    def vote_post(community_slug: str, post_id: str, old: str = "", new: str = "up",
                  confirm: bool = False) -> dict:
        """Like a post, or take your like back, as the logged-in user.

        Like: old="" new="up" (the default). Take it back: old="up" new="". Those
        are the only two values Skool's client uses (no downvote), and old and new
        must differ. post_id comes from list_posts.

        Draft-first: with confirm=false NOTHING is sent and Skool is not
        contacted. Call again with confirm=true once the user approved.
        """
        _skool().check_post_vote(old, new)
        _skool().post_action_path(post_id, "vote")  # refuse a bad id up front
        if not confirm:
            liking = new == "up"
            return _draft(
                "DRAFT — nothing was voted", "would_vote" if liking else "would_unvote",
                {"community": community_slug, "post_id": post_id, "old": old, "new": new,
                 "does": "like the post" if liking else "take the like back"},
                prose=False, not_verified=_NV_VOTE)
        client = _get_client()
        client.group_id_for(community_slug, for_write=True)  # archived guard
        answer = client.vote_post(post_id, old, new)
        return {"status": "voted" if new == "up" else "vote_taken_back", "post_id": post_id,
                "answer": _safe_raw(answer)}

    @mcp.tool()
    def pin_post(community_slug: str, post_id: str, pinned: bool = True,
                 confirm: bool = False) -> dict:
        """Pin a post to the top of the community feed (pinned=true), or unpin it (pinned=false).

        Skool's web client offers this to group admins only. Pinning to a course
        page is a different call and not available here.

        Draft-first: with confirm=false NOTHING is sent and Skool is not
        contacted. Call again with confirm=true once the user approved. The draft
        shows the id, not the post's title: make sure the user knows which post it is.
        """
        _skool().check_flag(pinned, "pinned")
        _skool().post_action_path(post_id, "pin" if pinned else "unpin")  # bad id up front
        if not confirm:
            return _draft(
                "DRAFT — nothing was pinned" if pinned else "DRAFT — nothing was unpinned",
                "would_pin" if pinned else "would_unpin",
                {"community": community_slug, "post_id": post_id,
                 "does": "pin the post to the feed" if pinned else "unpin the post from the feed"},
                prose=False, not_verified=_NV_PIN_POST)
        client = _get_client()
        client.group_id_for(community_slug, for_write=True)  # archived guard
        answer = client.pin_post(post_id, pinned=pinned)
        return {"status": "pinned" if pinned else "unpinned", "post_id": post_id,
                "answer": _safe_raw(answer)}

    @mcp.tool()
    def create_category(community_slug: str, display_name: str, default_sort: str,
                        post_write_role: int, confirm: bool = False) -> dict:
        """Create a feed category (Skool calls it a label) in the community, as the logged-in user.

        display_name: at most 30 characters (the category form's limit, not
        measured on the server), public. default_sort: newest-cm
        (shown as "Default", latest activity), newest ("New"), best-1w ("Top
        (week)") or best-1m ("Top (month)"). post_write_role: 4 = members and
        admins may post in it, 3 = admins only. There are no defaults: choose the
        values with the user. The category form stops at 10 categories (the
        server's limit is not known). The answer carries the new category's id.

        Draft-first: with confirm=false NOTHING is created and Skool is not
        contacted. Call again with confirm=true once the user approved the name.
        """
        _skool().check_category_name(display_name)
        _skool().check_category_options(default_sort, post_write_role)
        if not confirm:
            return _draft(
                "DRAFT — nothing was created", "would_create",
                {"community": community_slug, "display_name": display_name,
                 "default_sort": default_sort, "post_write_role": post_write_role,
                 "who_may_post": "admins only" if post_write_role == 3 else "members and admins"},
                not_verified=_NV_CREATE_CATEGORY)
        created = _get_client().create_category(
            community_slug, display_name, default_sort=default_sort,
            post_write_role=post_write_role)
        return {"status": "created", "category": _safe_raw(created)}

    @mcp.tool()
    def rename_category(community_slug: str, category_id: str, display_name: str,
                        confirm: bool = False) -> dict:
        """Rename an EXISTING feed category (Skool calls it a label), as the logged-in user.

        category_id is the `category_id` of a post from list_posts. display_name:
        at most 30 characters (the category form's limit, not measured on the
        server), public. Only the name changes; the category's sort and write
        role are not sent.

        Draft-first: with confirm=false NOTHING is changed and Skool is not
        contacted. The draft shows the id and the new name; the current name is
        not read back. Call again with confirm=true once the user approved.
        """
        _skool().check_category_name(display_name)
        _skool().category_path(category_id)  # refuse a bad id up front
        if not confirm:
            return _draft("DRAFT — nothing was renamed", "would_rename",
                          {"community": community_slug, "category_id": category_id,
                           "display_name": display_name},
                          not_verified=_NV_RENAME_CATEGORY)
        client = _get_client()
        client.group_id_for(community_slug, for_write=True)  # archived guard
        updated = client.rename_category(category_id, display_name)
        return {"status": "renamed", "category": _safe_raw(updated)}

    @mcp.tool()
    def delete_category(community_slug: str, category_id: str, migrate_to: str = "",
                        confirm: bool = False) -> dict:
        """PERMANENTLY delete a feed category (Skool calls it a label). This cannot be undone.

        category_id is the `category_id` of a post from list_posts, or the id
        create_category answered. migrate_to is the id of ANOTHER category:
        every post of the deleted one moves there first. Leave it empty only
        for a category without posts; Skool's web client makes you move the
        posts before it deletes. The category form keeps at least 1 category.

        confirm=false (the default) deletes nothing and does not contact Skool.
        The draft shows the ids only; the category's name and its number of
        posts are not read back, so make sure the user knows which category
        this id is.
        """
        _skool().category_delete_path(category_id, migrate_to)  # refuse bad ids up front
        if not confirm:
            return _draft(
                "DRAFT — nothing was deleted", "would_delete",
                {"community": community_slug, "category_id": category_id,
                 "migrate_to": migrate_to or "(none: the category must have no posts)",
                 "does": ("move every post of this category into the other one, then "
                          "delete the category" if migrate_to else "delete the category")},
                prose=False, not_verified=_NV_DELETE_CATEGORY)
        client = _get_client()
        client.group_id_for(community_slug, for_write=True)  # archived guard
        answer = client.delete_category(category_id, migrate_to=migrate_to)
        return {"status": "deleted", "category_id": category_id, "migrate_to": migrate_to,
                "answer": _safe_raw(answer)}

    @mcp.tool()
    def update_about_description(community_slug: str, description: str,
                                 confirm: bool = False) -> dict:
        """REPLACE the description on the community's About page, as the logged-in user.

        The About page is public: everyone who opens the community link reads it.
        description is the whole new text (sent exactly as given; Skool's editor
        allows 1000 plain characters). Skool's web client offers the editor to
        admins only. A blank text is refused.

        This overwrites a public text, and the draft shows the NEW text only, so
        read the current About text first and show it to the user next to the
        new one. The compact get_community_about does not carry it; raw=true
        does (pageProps.currentGroup.metadata.lpDescription) but is large and
        may exceed the tool-result size limit.

        Draft-first: with confirm=false NOTHING is changed and Skool is not
        contacted. Call again with confirm=true once the user approved that
        exact text.
        """
        _skool().check_about_description(description)
        if not confirm:
            return _draft("DRAFT — nothing was changed", "would_update",
                          {"community": community_slug, "description": description},
                          not_verified=_NV_ABOUT)
        answer = _get_client().update_about_description(community_slug, description)
        return {"status": "updated", "answer": _safe_raw(answer)}

    @mcp.tool()
    def set_post_comments(community_slug: str, post_id: str, enabled: bool,
                          confirm: bool = False) -> dict:
        """Turn comments OFF (enabled=false) or back ON (enabled=true) for one post.

        Skool's web client offers this to at least admins and moderators (whether
        the post's author can switch it is not known). post_id comes from
        list_posts. There is no default: say which direction you mean.

        Draft-first: with confirm=false NOTHING is sent and Skool is not
        contacted. The draft shows the id, not the post's title. Call again with
        confirm=true once the user approved.
        """
        _skool().check_flag(enabled, "enabled")
        _skool().post_action_path(post_id, "enable-comments" if enabled else "disable-comments")
        if not confirm:
            return _draft(
                "DRAFT — nothing was changed",
                "would_enable_comments" if enabled else "would_disable_comments",
                {"community": community_slug, "post_id": post_id,
                 "does": "turn comments on" if enabled else "turn comments off"},
                prose=False, not_verified=_NV_COMMENTS)
        client = _get_client()
        client.group_id_for(community_slug, for_write=True)  # archived guard
        answer = client.set_post_comments(post_id, enabled=enabled)
        return {"status": "comments_enabled" if enabled else "comments_disabled",
                "post_id": post_id, "answer": _safe_raw(answer)}

    @mcp.tool()
    def pin_comment(community_slug: str, post_id: str, comment_id: str, pinned: bool = True,
                    confirm: bool = False) -> dict:
        """Pin one comment to the top of its post (pinned=true), or unpin it (pinned=false).

        post_id is the post the comment sits under, comment_id the comment's own
        id (both from get_post_comments / list_posts). Skool's web client offers
        this to at least admins and moderators and allows one pinned comment per
        post.

        Draft-first: with confirm=false NOTHING is sent and Skool is not
        contacted. The draft shows the ids, not the comment's text. Call again
        with confirm=true once the user approved.
        """
        _skool().check_flag(pinned, "pinned")
        _skool().comment_pin_path(post_id, comment_id, pinned=pinned)  # bad ids up front
        if not confirm:
            return _draft(
                "DRAFT — nothing was pinned" if pinned else "DRAFT — nothing was unpinned",
                "would_pin_comment" if pinned else "would_unpin_comment",
                {"community": community_slug, "post_id": post_id, "comment_id": comment_id,
                 "does": "pin the comment" if pinned else "unpin the comment"},
                prose=False, not_verified=_NV_PIN_COMMENT)
        client = _get_client()
        client.group_id_for(community_slug, for_write=True)  # archived guard
        answer = client.pin_comment(post_id, comment_id, pinned=pinned)
        return {"status": "comment_pinned" if pinned else "comment_unpinned",
                "post_id": post_id, "comment_id": comment_id, "answer": _safe_raw(answer)}

    @mcp.tool()
    def follow_post(community_slug: str, post_id: str, follow: bool = True,
                    confirm: bool = False) -> dict:
        """Follow a post (follow=true, the bell on the post) or stop following it (follow=false).

        post_id comes from list_posts.

        Draft-first: with confirm=false NOTHING is sent and Skool is not
        contacted. Call again with confirm=true once the user approved.
        """
        _skool().check_flag(follow, "follow")
        _skool().post_action_path(post_id, "follow" if follow else "unfollow")  # bad id up front
        if not confirm:
            return _draft(
                "DRAFT — nothing was changed", "would_follow" if follow else "would_unfollow",
                {"community": community_slug, "post_id": post_id,
                 "does": "follow the post" if follow else "stop following the post"},
                prose=False)
        client = _get_client()
        client.group_id_for(community_slug, for_write=True)  # archived guard
        answer = client.follow_post(post_id, follow=follow)
        return {"status": "followed" if follow else "unfollowed", "post_id": post_id,
                "answer": _safe_raw(answer)}

    # -- chat read marks (docs/API.md §5.13) ------------------------------------
    # Both change the unread state of the account, nothing else. No slug and no
    # archived guard: a chat belongs to no single community. The draft does not
    # contact Skool.

    _NV_MARK_READ = (
        "Not verified: what `last` does on the server. Skool's page sends the id of the "
        "chat message that just came into view, and no `last` when the newest message is "
        "in view or when a chat is marked read from the channel list; catknows sends "
        "exactly the text you passed. Also not verified: whether the other person can see "
        "that you read the chat. Anything that looks for unread chats "
        "(list_chat_channels with unread_only, a poller) stops seeing this one as unread."
    )
    _NV_MARK_ALL_READ = (
        "Not verified: what Skool answers, and whether the other people can see that you "
        "read their chats. This marks EVERY chat of the account as read at once, and "
        "there is no way back for all of them in one call: anything that looks for unread "
        "chats (list_chat_channels with unread_only, a poller) sees none afterwards."
    )

    @mcp.tool()
    def mark_chat_read(channel_id: str, last: str = "", confirm: bool = False) -> dict:
        """Mark ONE chat (DM channel) as read for the logged-in account, so its unread counter drops.

        channel_id comes from list_chat_channels. `last` is optional and sent as
        text exactly as given: Skool's own page sends the id of a chat message
        (the one that just came into view) and nothing for "the whole chat";
        leave it empty to mark the whole chat. What the server does with `last`
        was not run.

        This changes what looks unread: anything that polls unread chats stops
        seeing this one. Draft-first: with confirm=false NOTHING is sent and
        Skool is not contacted. Call again with confirm=true once the user
        approved. The draft shows the ids, not the chat's participants.
        """
        _skool().chat_read_path(channel_id, last or None)  # bad ids up front
        if not confirm:
            return _draft(
                "DRAFT — nothing was marked as read", "would_mark_read",
                {"channel_id": channel_id,
                 "last": last or "(none: the whole chat)",
                 "does": (f"mark this chat as read, sending last={last} (a chat message id; "
                          "what Skool does with it is not verified)" if last
                          else "mark the whole chat as read")},
                prose=False, not_verified=_NV_MARK_READ)
        answer = _get_client().mark_chat_read(channel_id, last or None)
        return {"status": "marked_read", "channel_id": channel_id,
                "answer": _safe_raw(answer)}

    @mcp.tool()
    def mark_all_chats_read(confirm: bool = False) -> dict:
        """Mark ALL chats of the logged-in account as read at once: every chat in every community, not one.

        There is no way to name a channel or a community here; for one chat use
        mark_chat_read. Afterwards nothing looks unread, so anything that polls
        unread chats (list_chat_channels with unread_only) finds none.

        Draft-first: with confirm=false NOTHING is sent and Skool is not
        contacted. Call again with confirm=true once the user approved, and say
        in plain words that it is every chat of the account.
        """
        if not confirm:
            return _draft(
                "DRAFT — nothing was marked as read", "would_mark_all_read",
                {"scope": "ALL chats of the logged-in Skool account",
                 "does": ("mark EVERY chat of this account as read: all channels in all "
                          "communities, not just one")},
                prose=False, not_verified=_NV_MARK_ALL_READ)
        answer = _get_client().mark_all_chats_read()
        return {"status": "all_chats_marked_read", "answer": _safe_raw(answer)}


# -- tool annotations ----------------------------------------------------------
# Clients (and the Anthropic connector directory) read these hints to decide
# what needs a confirmation prompt. Everything here talks to Skool, so all tools
# are open-world; the only question per tool is whether it changes anything.
#
# Set in one place rather than as a decorator argument on every tool: the safe
# default is "not read-only", so a tool added later is treated as a writer until
# someone lists it here on purpose. Failing closed beats a forgotten annotation.

_READ_ONLY = {
    "list_members", "list_posts", "get_post_comments", "get_post_likes",
    "get_member_profile", "get_follows", "get_community_about", "get_discovery",
    "get_discovery_rank", "get_admin_metrics", "get_calendar", "get_classroom",
    "get_course_tree", "list_chat_channels", "read_dms", "list_my_communities",
    "get_post", "get_video_transcript", "get_notifications", "search_community", "get_growth", "list_join_requests", "get_revenue_leaderboard", "get_leaderboard",
    "search_members", "get_file_download_url", "get_community_rules", "get_community_levels",
}
# Acts as the user, visible to real members, can't be taken back.
_DESTRUCTIVE = {
    "create_post", "create_comment", "send_dm",
    "edit_post", "edit_comment", "delete_post", "delete_comment",
    "create_course", "create_course_item", "update_course_item",
    "publish_course", "move_course_item", "delete_course_item", "review_join_request",
    "create_calendar_event", "update_calendar_event", "delete_calendar_event",
    "vote_post", "pin_post", "create_category", "rename_category", "delete_category",
    "update_about_description", "set_post_comments", "pin_comment", "follow_post",
    "mark_chat_read", "mark_all_chats_read",
    "set_course_drip", "move_course_to_folder", "create_course_share_key", "import_course",
}

# Titles shown in the client's own tool list (Claude's Actions panel, for one).
# The protocol has no "off by default" flag — whether an action starts enabled
# is the client's call, and destructive_hint is the only lever we have: it puts
# these two in the write/delete group the user has to approve on purpose. So the
# title has to carry the warning, because it is the one string a user reads
# while deciding. The server-side lockout below is the part that actually holds.
_TITLES = {
    "create_post": "Post to a community (paused on hosted, single use only)",
    "create_comment": "Comment on a post (paused on hosted, single use only)",
}


def _annotate_tools() -> None:
    # mcp._tool_manager is private SDK API — the decorator takes annotations
    # per tool, but that means repeating the same block on every tool. The
    # self-check asserts the annotations actually land, so an SDK rename
    # fails loudly instead of silently shipping unannotated tools.
    from mcp.types import ToolAnnotations

    for tool in mcp._tool_manager.list_tools():
        read_only = tool.name in _READ_ONLY
        tool.annotations = ToolAnnotations(
            title=_TITLES.get(tool.name),
            read_only_hint=read_only,
            # Only meaningful when read_only is false; writers that merely add
            # (a post, a DM) are not destructive in the "deletes data" sense,
            # but they are irreversible and public — flag them.
            destructive_hint=tool.name in _DESTRUCTIVE,
            # Reads hit a live community, so repeating one can return something
            # different; it just doesn't *change* anything.
            idempotent_hint=False,
            open_world_hint=True,
        )


_annotate_tools()


def main() -> None:
    if not _http_mode():
        mcp.run()  # stdio transport (default)
        return
    # Streamable HTTP for remote/hosted use. Binds loopback by default: this
    # transport carries no auth yet, so a reverse proxy (Caddy) terminates TLS
    # and fronts it. CATKNOWS_HOST=0.0.0.0 has to be a deliberate choice.
    kwargs: dict[str, Any] = {
        "transport": "streamable-http",
        "host": os.environ.get("CATKNOWS_HOST", "127.0.0.1"),
        "port": int(os.environ.get("CATKNOWS_PORT", "8000")),
    }
    # The SDK's DNS-rebinding guard only accepts Host headers it knows, so
    # behind a proxy every request arrives as the *public* name and is
    # rejected with "Invalid Host header". Name the domain to let it through
    # — keep the guard on, don't disable it.
    domain = os.environ.get("CATKNOWS_DOMAIN", "").strip()
    if domain:
        from mcp.server.transport_security import TransportSecuritySettings

        kwargs["transport_security"] = TransportSecuritySettings(
            allowed_hosts=[domain, f"{domain}:443", "127.0.0.1", "localhost"],
            allowed_origins=[f"https://{domain}"],
        )
    mcp.run(**kwargs)


def _self_check() -> None:
    """Transport switch: python -m catknows.mcp_server --self-check

    Records what main() would run instead of actually serving.
    """
    calls: list[tuple] = []
    real_run, mcp.run = mcp.run, lambda *a, **kw: calls.append((a, kw))
    real_env = dict(os.environ)
    try:
        for var in ("CATKNOWS_HTTP", "CATKNOWS_HOST", "CATKNOWS_PORT"):
            os.environ.pop(var, None)
        main()
        assert calls == [((), {})], f"default must stay stdio, got {calls}"

        calls.clear()
        os.environ["CATKNOWS_HTTP"] = "1"
        main()
        (_, kw), = calls
        assert kw["transport"] == "streamable-http", kw
        assert kw["host"] == "127.0.0.1", f"must bind loopback unless asked, got {kw['host']}"
        assert kw["port"] == 8000, kw

        calls.clear()
        os.environ.update(CATKNOWS_HOST="0.0.0.0", CATKNOWS_PORT="9000")
        main()
        (_, kw), = calls
        assert (kw["host"], kw["port"]) == ("0.0.0.0", 9000), kw

        calls.clear()
        os.environ["CATKNOWS_HTTP"] = "0"  # only "1" flips it
        main()
        assert calls == [((), {})], f"CATKNOWS_HTTP=0 must stay stdio, got {calls}"

        # Behind a proxy the Host header is the public name; without the
        # domain in allowed_hosts the SDK answers "Invalid Host header".
        calls.clear()
        os.environ.update(CATKNOWS_HTTP="1", CATKNOWS_DOMAIN="mcp.example.app")
        main()
        (_, kw), = calls
        sec = kw["transport_security"]
        assert sec.enable_dns_rebinding_protection, "guard must stay on"
        assert "mcp.example.app" in sec.allowed_hosts, sec.allowed_hosts
        assert "https://mcp.example.app" in sec.allowed_origins, sec.allowed_origins

        calls.clear()
        os.environ.pop("CATKNOWS_DOMAIN")
        main()
        (_, kw), = calls
        assert "transport_security" not in kw, "no domain -> SDK defaults, not ours"
    finally:
        mcp.run = real_run
        os.environ.clear()
        os.environ.update(real_env)

    # Annotations: every tool carries them (catches an SDK rename of the
    # private manager), and nothing that writes claims to be read-only.
    tools = {t.name: t.annotations for t in mcp._tool_manager.list_tools()}
    assert tools, "no tools registered — did the tool manager move?"
    unannotated = [n for n, a in tools.items() if a is None]
    assert not unannotated, f"tools without annotations: {unannotated}"

    writers = {"login_to_skool", "update_catknows", "pull_to_vault"} | _DESTRUCTIVE
    for name, ann in tools.items():
        expected = name in _READ_ONLY
        assert ann.read_only_hint is expected, f"{name}: read_only_hint={ann.read_only_hint}"
        assert ann.open_world_hint is True, f"{name}: talks to Skool, must be open-world"
        if name in writers:
            assert not ann.read_only_hint, f"{name} writes — must not be read-only"
        assert ann.destructive_hint is (name in _DESTRUCTIVE), name
    assert not (_READ_ONLY & writers), "a tool cannot be both read-only and a writer"
    # A new tool in NEITHER set is annotated a writer by default. That fails
    # closed, but silently: a read tool then asks for confirmation forever and
    # nobody notices. Name it here on purpose, either way.
    unclassified = set(tools) - _READ_ONLY - writers
    assert not unclassified, (
        f"tools in neither _READ_ONLY nor _DESTRUCTIVE: {sorted(unclassified)} — "
        "add each one (see AGENTS.md)"
    )
    # The write tools only exist with CATKNOWS_ALLOW_WRITE=1; when they do, they
    # must be the destructive ones — a silent rename would drop the flag.
    # Registering them here is what lets one --self-check cover both modes.
    if os.environ.get("CATKNOWS_ALLOW_WRITE", "") == "1":
        assert _DESTRUCTIVE <= set(tools), f"write tools missing from registry: {tools.keys()}"
        # The two paused tools carry their warning in the title, because that is
        # the string a user reads in the client's action list while deciding
        # whether to allow them. Losing it silently would undo the whole point.
        for name, title in _TITLES.items():
            assert tools[name].title == title, f"{name}: title is {tools[name].title!r}"
            assert "paused on hosted" in tools[name].title, name
        # And the docstring the model reads must keep telling it not to schedule.
        for name in ("create_post", "create_comment"):
            doc = mcp._tool_manager.get_tool(name).description or ""
            assert "NEVER run this on a schedule" in doc, f"{name} lost its schedule warning"
        # The draft must describe the attachments before anything is uploaded,
        # and a bad path must fail at preview time, not mid-post.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "report.pdf"
            pdf.write_bytes(b"%PDF-1.4 fake")
            (prev,) = _attachment_preview(str(pdf))
            assert prev["file"] == "report.pdf" and prev["type"] == "application/pdf", prev
            assert prev["size_bytes"] == 13, prev
            assert _attachment_preview("") == [], "no attachments -> empty preview"
            try:
                _attachment_preview(str(Path(tmp) / "nope.pdf"))
                raise AssertionError("missing attachment must raise at preview time")
            except ValueError:
                pass
    else:
        assert not (_DESTRUCTIVE & set(tools)), "write tools must stay gated"

    # The write-audit log (D8) hangs off the tool wrapper. If a future SDK
    # bypasses mcp.tool (or somebody registers a tool the raw way), writes still
    # get logged — but with no tool name and no community, which is most of the
    # line's value. Catch that here rather than in the incident review.
    # Wrap without registering: a probe tool in the real catalog would ship to
    # users and trip the "every tool is documented" check in test_mcp_tools_doc.
    probe: dict = {}

    def _audit_probe(community_slug: str = "probe-slug", post_id: str = "probe-post") -> dict:
        probe.update(tool=audit.current_tool(), ctx=dict(audit._CONTEXT.get()))
        return {}

    wrapped = _with_audit_context(_audit_probe)
    wrapped()
    assert probe.get("tool") == "_audit_probe", (
        f"tool calls no longer carry an audit context (got {probe.get('tool')!r}) — "
        "the write log would lose the tool name (see catknows/audit.py)"
    )
    assert probe["ctx"].get("community") == "probe-slug", probe["ctx"]
    assert probe["ctx"].get("post_id") == "probe-post", probe["ctx"]
    # The wrapper must not eat the name or signature the SDK registers a tool by.
    assert wrapped.__name__ == "_audit_probe", wrapped.__name__
    assert "community_slug" in inspect.signature(wrapped).parameters, "signature lost"
    # Every registered tool went through the wrapper, so a raw registration
    # slipping past it (an SDK that stops calling mcp.tool) shows up here.
    assert all(t.name != "wrapper" for t in mcp._tool_manager.list_tools()), \
        "a tool registered under the wrapper's own name — functools.wraps was lost"

    # Comma-separated arguments: plain splitting posted a poll option as two
    # options ("Yes, the cat has served me" -> 3 options, still inside the valid
    # 2-10 range, so nothing complained). Escaping keeps old calls identical.
    assert _csv_arg("a,b") == ["a", "b"], "plain split must not change"
    assert _csv_arg(" a , b ") == ["a", "b"], "values are trimmed"
    assert _csv_arg("") == [], "empty argument -> no values"
    assert _csv_arg("a,,b") == ["a", "b"], "empty slots are dropped"
    assert _csv_arg("Yes\\, the cat has served me,Not yet") == [
        "Yes, the cat has served me", "Not yet"
    ], "an escaped comma stays inside its option"
    # Windows paths must survive: only \, is an escape, \n stays two chars.
    assert _csv_arg("C:\\new\\file.pdf") == ["C:\\new\\file.pdf"], "backslash kept"
    assert _csv_arg("one\\") == ["one\\"], "trailing backslash kept"

    print(f"mcp_server self-check OK ({len(tools)} tools annotated)")


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        _self_check()
    else:
        main()
