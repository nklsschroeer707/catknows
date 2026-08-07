"""MCP server: plug your Skool data into any AI (Claude, Codex, Cursor, ...).

    catknows-mcp        # or: python -m catknows.mcp_server

Exposes the SkoolClient endpoints as MCP tools over stdio. Register once, e.g.:

    claude mcp add catknows -- python -m catknows.mcp_server

The first tool call triggers the browser login (a window opens once); the
session persists in ~/.catknows/skool-profile so every later call runs
silently. Set CATKNOWS_COOKIE to a raw Cookie header to skip the browser
entirely (headless machines), or CATKNOWS_PROFILE_DIR to move the profile.

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


def _jsonable(obj: Any) -> Any:
    """normalize.* records carry datetimes — make them JSON-safe."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonable(v) for v in obj]
    return obj


@mcp.tool()
def login_to_skool() -> str:
    """Log in to Skool (opens a browser window on first use). Call this if other tools report auth errors; afterwards the session is persisted and reused silently."""
    _get_client()
    return "Logged in. Session persisted — future calls run without the browser."


@mcp.tool()
def list_members(community_slug: str, limit: int = 100, raw: bool = False) -> list[dict]:
    """List members of a Skool community: name, role, points, last-active, and more.

    community_slug is the part after skool.com/ in the community URL.
    raw=True returns Skool's unmodified JSON instead of flat records.
    """
    # ponytail: fetches all pages then slices; per-page limits if huge communities hurt.
    users = _get_client().members(community_slug)[:limit]
    return users if raw else [_jsonable(normalize.member(u)) for u in users]


@mcp.tool()
def list_posts(community_slug: str, limit: int = 50, raw: bool = False) -> list[dict]:
    """List posts of a Skool community (title, author, likes, comment count, content).

    raw=True returns Skool's unmodified post trees.
    """
    trees = _get_client().posts(community_slug)[:limit]
    return trees if raw else [_jsonable(normalize.post(t)) for t in trees]


@mcp.tool()
def get_post_comments(community_slug: str, post_id: str, raw: bool = False) -> Any:
    """Get the full nested comment thread of a post. post_id is the post's Skool id (from list_posts)."""
    client = _get_client()
    merged = client.comments(post_id, client.group_id_for(community_slug))
    return merged if raw else _jsonable(normalize.comments(merged))


@mcp.tool()
def get_post_likes(community_slug: str, post_id: str) -> list[dict]:
    """List the users who liked/upvoted a post."""
    client = _get_client()
    return client.likes(post_id, client.group_id_for(community_slug))


@mcp.tool()
def get_member_profile(user_name: str, community_slug: str, raw: bool = False) -> dict | None:
    """Get one member's profile (bio, socials, stats). user_name is their Skool handle."""
    user = _get_client().profile(user_name, community_slug)
    if user is None or raw:
        return user
    return _jsonable(normalize.profile(user))


@mcp.tool()
def get_community_about(community_slug: str) -> dict:
    """Get a community's public About info (description, pricing, size, owner) — works without joining it."""
    return _get_client().community_about(community_slug)


@mcp.tool()
def get_discovery(community_slug: str) -> dict:
    """Get Skool discovery/leaderboard data for a community (rankings, categories)."""
    client = _get_client()
    return client.discovery(client.group_id_for(community_slug))


@mcp.tool()
def get_admin_metrics(community_slug: str, range: str = "30d") -> dict:
    """Get admin metrics for a community you own (growth, engagement). range e.g. 7d, 30d, 90d."""
    client = _get_client()
    return client.admin_metrics(client.group_id_for(community_slug), range_=range)


@mcp.tool()
def get_calendar(community_slug: str, cal_date: int = 0) -> dict:
    """Get the community calendar/events."""
    return _get_client().calendar(community_slug, cal_date=cal_date)


@mcp.tool()
def get_classroom(community_slug: str) -> dict:
    """Get the community classroom structure (courses, modules)."""
    return _get_client().classroom(community_slug)


@mcp.tool()
def list_chat_channels(offset: int = 0, limit: int = 30) -> dict:
    """List your Skool DM/chat channels (ids, participants, last message). Channel ids are what send_dm needs."""
    return _get_client().chat_channels(offset=offset, limit=limit)


@mcp.tool()
def pull_to_vault(
    community_slug: str, vault_dir: str = "./vault", include_comments: bool = True
) -> dict:
    """Pull the whole community (members, posts, comments) into an Obsidian vault of Markdown notes with YAML frontmatter. Returns counts and the vault path."""
    client = _get_client()
    out = Path(vault_dir).expanduser().resolve()

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
