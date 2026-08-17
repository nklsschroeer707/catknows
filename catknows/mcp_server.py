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

ᓚᘏᗢ
"""

from __future__ import annotations

import mimetypes
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


def _cap(limit: int, raw: bool) -> tuple[int, dict | None]:
    """Bound a list limit so results don't blow past the tool-result size cap,
    and hand back the marker to append when the caller asked for more.

    Raw records are far bigger than normalized ones, so cap them harder.

    The cap itself is deliberate, but it used to be silent: asking a 592-member
    community for 650 returned exactly 200 rows and nothing said so, which reads
    as "that is the whole community". The incomplete marker never fired here — it
    only reports Skool cutting the page walk short, not us lowering the limit.
    Both list tools append this instead, so the two stay in step.

    ``returned`` is the ceiling, i.e. the most the walk may collect. A caller
    that can end the walk earlier (the role cap in `list_members`) overwrites it
    with the real row count before appending.
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
        "note": f"catknows caps this tool at {ceiling} records per call"
                f"{' (raw records are much bigger)' if raw else ''} so the "
                "response stays inside the tool-result size limit. This list is "
                "the first "
                f"{effective}, not everything that matched. Narrow the query "
                "instead of raising limit: filters, lifecycle, tiers or "
                "course_ids on list_members, so each call returns fewer rows.",
    }


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
                 course_ids: str = "") -> list[dict]:
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
    so the response fits the tool-result size limit. Ask for more and the last
    list entry is {"limit_capped": true, "requested": ..., "returned": ...} —
    narrow the query with the filters below rather than raising limit.

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
    users = _get_client().members(
        community_slug, limit=effective, lifecycle=lifecycle, sort=sort,
        tiers=_csv_arg(tiers), course_ids=_csv_arg(course_ids), **kwargs)
    out = _safe_raw(list(users)) if raw else [_jsonable(normalize.member(u)) for u in users]
    if capped:
        # `returned` is computed from the limit, before the walk runs. The role
        # cap can end the walk sooner, and a marker claiming 200 next to 30 rows
        # is exactly the dishonesty this marker exists to prevent.
        capped["returned"] = len(users)
        out.append(capped)
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
def list_posts(community_slug: str, limit: int = 25, raw: bool = False) -> list[dict]:
    """List posts of a Skool community (title, author, likes, comment count, content).

    raw=True returns Skool's unmodified post trees. Keep limit small — raw trees
    are large and can exceed the tool-result size cap.

    One call returns at most 200 posts (30 with raw=True), whatever limit says.
    Ask for more and the last list entry is {"limit_capped": true, ...} — the
    list is the newest 200, not the whole community.
    """
    effective, capped = _cap(limit, raw)
    trees = _get_client().posts(community_slug, limit=effective)
    out = _safe_raw(trees) if raw else [_jsonable(normalize.post(t)) for t in trees]
    if capped:
        out = [*out, capped]
    return out


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
def get_course_tree(course_id: str, raw: bool = False) -> dict:
    """Read one course's full structure: folders and pages with ids, titles, order and draft state.

    course_id comes from get_classroom. This is the only way to read page
    bodies — the classroom list carries just the course tiles. Child order in
    the tree is the display order.
    """
    data = _get_client().course_tree(course_id)
    if raw:
        return _safe_raw(data)

    def _compact(node):
        unit = node.get("course") or {}
        md = unit.get("metadata") or {}
        out = {
            "id": unit.get("id"),
            "type": unit.get("unit_type"),  # course | set (folder) | module (page)
            "title": md.get("title", ""),
            "is_draft": unit.get("state") == 1,
        }
        if unit.get("unit_type") == "module":
            out["content"] = md.get("desc", "")
            if md.get("video_link"):
                out["video_link"] = md.get("video_link")
        children = [_compact(ch) for ch in node.get("children") or []]
        if children:
            out["children"] = children
        return out

    return _compact(data)


@mcp.tool()
def list_chat_channels(offset: int = 0, limit: int = 30) -> dict:
    """List your Skool DM/chat channels (ids, participants, last message). Channel ids are what send_dm needs."""
    # Channel objects embed the other participant's full metadata (and, on your
    # own entries, afl/payout secrets) — scrub before returning.
    return _safe_raw(_get_client().chat_channels(offset=offset, limit=limit))


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

    result = {
        "vault": str(out),
        "members": len(members),
        "posts": posts_written,
        "comments_failed": comments_failed,
    }
    if getattr(members, "incomplete", False):
        result["members_incomplete"] = True  # truncated walk — not the whole community
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

    def _attachment_preview(paths: str) -> list[dict]:
        """Describe local files for the draft, without uploading anything yet.

        The draft has to show what will be attached BEFORE it is sent, so this
        only stats the files. A missing file must fail here, at preview time —
        not halfway through a confirmed post.
        """
        out = []
        for p in _csv_arg(paths):
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
            for p in _csv_arg(paths)
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
        ...). They are uploaded to Skool only when confirm=true; the draft just
        lists their name, type and size.
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
        if not confirm:
            return {
                "status": "DRAFT — nothing was posted",
                "would_post": draft,
                "next_step": "Show this draft to the user; call again with confirm=true once they approve.",
            }
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
        attachments is a comma-separated list of LOCAL FILE PATHS, uploaded only
        when confirm=true.

        Draft-first: with confirm=false (the default) NOTHING is written — you
        get the draft back to show the user. Only call again with confirm=true
        after the user explicitly approved it.
        """
        draft = {
            "community": community_slug,
            "post_id": post_id,
            "content": content,
            "replying_to_comment": parent_comment_id or "(none — top-level comment on the post)",
            "attachments": _attachment_preview(attachments),
        }
        if not confirm:
            return {
                "status": "DRAFT — nothing was written",
                "would_comment": draft,
                "next_step": "Show this draft to the user; call again with confirm=true once they approve.",
            }
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
            return {
                "status": "DRAFT — nothing was changed",
                "would_edit": _edit_draft(post_id, new_title, new_content),
                "next_step": "Show this to the user; call again with confirm=true once they approve.",
            }
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
            return {
                "status": "DRAFT — nothing was changed",
                "would_edit": _edit_draft(comment_id, None, new_content),
                "next_step": "Show this to the user; call again with confirm=true once they approve.",
            }
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
            return {
                "status": "DRAFT — nothing was sent",
                "would_send": {
                    "channel_id": channel_id,
                    "content": content,
                    "attachments": _attachment_preview(attachments),
                },
                "next_step": "Show this to the user; call again with confirm=true once they approve.",
            }
        client = _get_client()
        ids: list[str] = []
        if attachments:
            # A DM has no community slug — the file is owned by the group the
            # channel belongs to, so read that off the channel itself. The
            # messages endpoint returns the channel object in one call; listing
            # /self/chat-channels instead cannot work: Skool refuses limit > 30
            # there, and accounts have hundreds of channels.
            from .http import SkoolHTTPError

            try:
                gid = (client.chat_messages(channel_id, count=1)
                       .get("channel") or {}).get("group_id") or ""
            except SkoolHTTPError:
                gid = ""
            if not gid:
                raise ValueError(f"Unknown channel {channel_id} — cannot attach files.")
            ids = [
                client.upload_file(gid, p)["id"]
                for p in _csv_arg(attachments)
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
            return {"status": "DRAFT — nothing was created", "would_create": would,
                    "next_step": "Show this to the user; call again with confirm=true once they approve."}
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
            return {"status": "DRAFT — nothing was created", "would_create": would,
                    "next_step": "Show this to the user; call again with confirm=true once they approve."}
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
            return {"status": "DRAFT — nothing was changed",
                    "would_update": {"item_id": item_id, **fields},
                    "next_step": "Show this to the user; call again with confirm=true once they approve."}
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

        Moving between folders is NOT possible via the API — recreate the page
        in the target folder instead. Draft-first: confirm=true executes.
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
    "get_course_tree", "list_chat_channels", "read_dms", "list_my_communities",
    "get_post", "get_video_transcript",
}
# Acts as the user, visible to real members, can't be taken back.
_DESTRUCTIVE = {
    "create_post", "create_comment", "send_dm",
    "edit_post", "edit_comment", "delete_post", "delete_comment",
    "create_course", "create_course_item", "update_course_item",
    "publish_course", "move_course_item", "delete_course_item",
}


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
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
