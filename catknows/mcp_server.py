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

from . import normalize, vault

mcp = MCPServer("catknows")

_client = None  # lazy: log in only when the first tool actually needs Skool


def _profile_dir() -> Path:
    return Path(
        os.environ.get("CATKNOWS_PROFILE_DIR", Path.home() / ".catknows" / "skool-profile")
    )


def _get_client():
    """Return a logged-in SkoolClient, creating the session on first use.

    stdout is the MCP protocol channel — login() prints instructions, so
    everything here runs with stdout redirected to stderr.
    """
    global _client
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


def _maybe_json(val: Any) -> Any:
    """Skool nests JSON as *strings* inside metadata (displayPrice, owner,
    event location, ...). Parse when it is one, pass through otherwise."""
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, ValueError):
            return val
    return val


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
    price = _maybe_json(md.get("displayPrice"))
    # owner arrives as a JSON *string* since ~Aug 2026 — .get() on it crashed.
    owner = _maybe_json(md.get("owner"))
    return {
        "slug": g.get("name", community_slug),
        "display_name": md.get("displayName", ""),
        "description": md.get("description", ""),
        "membership_model": md.get("membershipModel"),  # 1=free, 2=paid
        "plan": md.get("plan"),
        "price": price,
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
    locally. (The old per-community api2 discovery endpoint is WAF-blocked.)
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
def get_admin_metrics(community_slug: str, range: str = "30d") -> dict:
    """Get admin metrics for a community you own (growth, engagement). range e.g. 7d, 30d, 90d."""
    client = _get_client()
    return client.admin_metrics(client.group_id_for(community_slug), range_=range)


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


@mcp.tool()
def pull_to_vault(
    community_slug: str, vault_dir: str = "./vault", include_comments: bool = True
) -> dict:
    """Pull the whole community (members, posts, comments) into an Obsidian vault of Markdown notes with YAML frontmatter. Returns counts and the vault path."""
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
        return {"status": "posted", "post": created}

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
        return {"status": "sent", "message": sent}


def main() -> None:
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
