"""MCP server: plug your Skool data into any AI (Claude, Codex, Cursor, ...).

    catknows-mcp        # or: python -m catknows.mcp_server

Exposes the SkoolClient endpoints as MCP tools over stdio. Register once, e.g.:

    claude mcp add catknows -- python -m catknows.mcp_server

The first tool call triggers the browser login (a window opens once); the
session persists in ~/.catknows/skool-profile so every later call runs
silently. Set CATKNOWS_COOKIE to a raw Cookie header to skip the browser
entirely (headless machines), or CATKNOWS_PROFILE_DIR to move the profile.

Speed knobs: reads are cached in-process for CATKNOWS_CACHE_TTL seconds
(default 600, 0 disables; chat channels never cached, writes clear it), and
CATKNOWS_PAGE_DELAY tunes the politeness pause between paginated requests
(default 0.8 s — lowering it is your 403 risk).

You have been served by catknows. — you are welcome.
"""

from __future__ import annotations

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

from . import normalize, sessions, vault


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

_client = None  # lazy: log in only when the first tool actually needs Skool
_clients: dict[str, Any] = {}  # per-user clients, keyed by OAuth subject


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
        if subject not in _clients:
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
            _clients[subject] = _client_from_cookie(cookie)
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


def _cap(limit: int, raw: bool) -> int:
    """Bound a list limit so results don't blow past the tool-result size cap.

    Raw records are far bigger than normalized ones, so cap them harder.
    """
    ceiling = 30 if raw else 200
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = ceiling
    return max(1, min(limit, ceiling))


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
    client-side platform keys — docs/API.md §6.6) never reach an AI context or
    a log. The scrub list lives in one place: normalize.SECRET_KEYS.
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
        existed = sessions.delete(subject)
        return {"status": "deleted" if existed else "nothing stored"}


@mcp.tool()
def list_members(community_slug: str, limit: int = 25, raw: bool = False) -> list[dict]:
    """List members of a Skool community: name, role, points, level, last-active, and more.

    Sorted by Skool DESC on last-active, so the default (limit=25) is the most
    recently active members. community_slug is the part after skool.com/ in the
    URL. raw=True returns Skool's unmodified JSON (large — keep limit small, it's
    hard-capped to avoid exceeding the tool-result size limit).
    """
    # ponytail: fetches all pages then slices; per-page limits if huge communities hurt.
    users = _get_client().members(community_slug)[: _cap(limit, raw)]
    return _safe_raw(users) if raw else [_jsonable(normalize.member(u)) for u in users]


@mcp.tool()
def list_posts(community_slug: str, limit: int = 25, raw: bool = False) -> list[dict]:
    """List posts of a Skool community (title, author, likes, comment count, content).

    raw=True returns Skool's unmodified post trees. Keep limit small — raw trees
    are large and can exceed the tool-result size cap.
    """
    trees = _get_client().posts(community_slug, limit=_cap(limit, raw))
    return _safe_raw(trees) if raw else [_jsonable(normalize.post(t)) for t in trees]


@mcp.tool()
def get_post_comments(community_slug: str, post_id: str, raw: bool = False) -> Any:
    """Get the full nested comment thread of a post. post_id is the post's Skool id (from list_posts)."""
    client = _get_client()
    merged = client.comments(post_id, client.group_id_for(community_slug))
    return _safe_raw(merged) if raw else _jsonable(normalize.comments(merged))


@mcp.tool()
def get_post_likes(community_slug: str, post_id: str) -> list[dict]:
    """List the users who liked/upvoted a post (id, handle, first/last name)."""
    client = _get_client()
    users = client.likes(post_id, client.group_id_for(community_slug))
    # Raw liker objects carry full user metadata — including the caller's own
    # payout/affiliate ids on their own like. Only names leave this tool.
    return [normalize.like(u, post_id) for u in users]


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
        "privacy": md.get("privacy"),  # 1=private, 2=public
        "owner": owner.get("name") if isinstance(owner, dict) else owner,
        "created_by": md.get("createdBy"),
    }


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
                "title": md.get("title", ""),
                "description": desc[:300] + ("…" if len(desc) > 300 else ""),
                "num_modules": md.get("numModules"),
                "has_access": bool(md.get("hasAccess")),
                "privacy": md.get("privacy"),  # 0=open, 1=locked/paid, 2=level-locked
            }
        )
    return {"num_courses": len(courses), "courses": courses}


@mcp.tool()
def list_chat_channels(offset: int = 0, limit: int = 30) -> dict:
    """List your Skool DM/chat channels (ids, participants, last message). Channel ids are what send_dm needs."""
    # Channel objects embed the other participant's full metadata (and, on your
    # own entries, afl/payout secrets) — scrub before returning.
    return _safe_raw(_get_client().chat_channels(offset=offset, limit=limit))


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
        """Pull the whole community (members, posts, comments) into an Obsidian vault of Markdown notes with YAML frontmatter. Returns counts and the vault path."""
        return _pull_to_vault(community_slug, vault_dir, include_comments)


def _pull_to_vault(community_slug: str, vault_dir: str, include_comments: bool) -> dict:
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
    for tree in trees:
        prec = normalize.post(tree)
        comment_recs = None
        if include_comments and group_id and prec["comments"] > 0:
            try:
                comment_recs = normalize.comments(client.comments(prec["skool_id"], group_id))
            except Exception:  # one bad post shouldn't kill the pull
                comments_failed += 1
        vault.write_post(out, community_slug, prec, comment_recs)
        posts_written += 1

    return {
        "vault": str(out),
        "members": len(members),
        "posts": posts_written,
        "comments_failed": comments_failed,
    }


# -- write tools (opt-in) ------------------------------------------------------
# Only registered when CATKNOWS_ALLOW_WRITE=1 is set in the server's env.
# Without the flag these tools don't exist at all from the AI's point of view.

if os.environ.get("CATKNOWS_ALLOW_WRITE", "") == "1":

    @mcp.tool()
    def create_post(
        community_slug: str,
        title: str,
        content: str,
        labels: str = "",
        video_links: str = "",
        notify_members: bool = False,
        confirm: bool = False,
    ) -> dict:
        """Create a REAL post in the community, as the logged-in user, visible to all members.

        Draft-first: with confirm=false (the default) NOTHING is posted — you get
        the exact payload back to show the user for approval. Only call again with
        confirm=true after the user explicitly approved that draft.
        notify_members=true EMAILS EVERY MEMBER (Skool broadcast) — set it only if
        the user explicitly asked to email everyone.
        """
        draft = {
            "community": community_slug,
            "title": title,
            "content": content,
            "labels": labels,
            "video_links": video_links,
            "notify_members (emails everyone!)": notify_members,
        }
        if not confirm:
            return {
                "status": "DRAFT — nothing was posted",
                "would_post": draft,
                "next_step": "Show this draft to the user; call again with confirm=true once they approve.",
            }
        created = _get_client().create_post(
            community_slug,
            title,
            content,
            labels=labels,
            video_links=video_links,
            notify_members=notify_members,
        )
        return {"status": "posted", "post": _safe_raw(created)}

    @mcp.tool()
    def send_dm(channel_id: str, content: str, confirm: bool = False) -> dict:
        """Send a REAL direct message into an existing chat channel (ids via list_chat_channels).

        Draft-first: with confirm=false (the default) NOTHING is sent — you get a
        preview back. Only call again with confirm=true after the user explicitly
        approved the message text.
        """
        if not confirm:
            return {
                "status": "DRAFT — nothing was sent",
                "would_send": {"channel_id": channel_id, "content": content},
                "next_step": "Show this to the user; call again with confirm=true once they approve.",
            }
        sent = _get_client().send_dm(channel_id, content)
        return {"status": "sent", "message": _safe_raw(sent)}


# -- tool annotations ----------------------------------------------------------
# Clients (and the Anthropic connector directory) read these hints to decide
# what needs a confirmation prompt. Everything here talks to Skool, so all tools
# are open-world; the only question per tool is whether it changes anything.
#
# Set in one place rather than as 17 decorator arguments: the safe default is
# "not read-only", so a tool added later is treated as a writer until someone
# lists it here on purpose. Failing closed beats a forgotten annotation.

_READ_ONLY = {
    "list_members", "list_posts", "get_post_comments", "get_post_likes",
    "get_member_profile", "get_community_about", "get_discovery",
    "get_discovery_rank", "get_admin_metrics", "get_calendar", "get_classroom",
    "list_chat_channels",
}
# Acts as the user, visible to real members, can't be taken back.
_DESTRUCTIVE = {"create_post", "send_dm"}


def _annotate_tools() -> None:
    # mcp._tool_manager is private SDK API — the decorator takes annotations
    # per tool, but that means repeating the same block 17 times. The
    # self-check asserts the annotations actually land, so an SDK rename
    # fails loudly instead of silently shipping unannotated tools.
    from mcp.types import ToolAnnotations

    for tool in mcp._tool_manager.list_tools():
        read_only = tool.name in _READ_ONLY
        tool.annotations = ToolAnnotations(
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
    # The write tools only exist with CATKNOWS_ALLOW_WRITE=1; when they do, they
    # must be the destructive ones — a silent rename would drop the flag.
    if os.environ.get("CATKNOWS_ALLOW_WRITE", "") == "1":
        assert _DESTRUCTIVE <= set(tools), f"write tools missing from registry: {tools.keys()}"
    print(f"mcp_server self-check OK ({len(tools)} tools annotated)")


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        _self_check()
    else:
        main()
