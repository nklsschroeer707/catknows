"""Turn raw Skool JSON into flat, tidy records.

The raw API responses are nested and inconsistent (nanosecond timestamps here,
ISO strings there; camelCase on Next.js, snake_case on api2). This module is
the single place that knows those quirks, so the rest of your pipeline — and
whatever you do in Obsidian — sees clean, flat dicts with real datetimes.

Field mappings and the quirks below are ported 1:1 from the production
extractors, so a normalized record here matches what the graph app stored.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone


def _ns_or_iso_to_dt(raw) -> datetime | None:
    """Skool timestamps come as nanoseconds (int) OR ISO strings. Handle both.

    Values above 1e15 are treated as nanoseconds (Skool uses ns for member
    last-offline and some created-at fields); ISO strings are parsed as-is.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)) and raw > 1e15:
        return datetime.fromtimestamp(raw / 1_000_000_000, tz=timezone.utc)
    if isinstance(raw, (int, float)) and raw > 1e9:  # plausible unix seconds
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _micros_to_dt(raw) -> datetime | None:
    """api2 comment timestamps: microseconds as a number, or an ISO string.

    The cursor field (``last``) is microseconds, but ``post.created_at`` comes
    back as ISO ("2026-08-08T16:59:44.650886Z") — falling through to None on
    strings is what made every normalized comment show ``created_at: null``.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)) and raw > 0:
        return datetime.fromtimestamp(raw / 1_000_000, tz=timezone.utc)
    return _ns_or_iso_to_dt(raw)


def _sp_data(meta: dict) -> dict:
    """Parse the ``spData`` JSON-string blob (points/level/role live here)."""
    raw = meta.get("spData")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


def member(user: dict) -> dict:
    """Flatten one raw ``pageProps.users[]`` object into a member record."""
    m = user.get("member") or {}
    meta = user.get("metadata") or {}
    sp = _sp_data(meta)

    last_offline = meta.get("lastOffline")  # nanoseconds
    return {
        "skool_id": user.get("id", ""),
        "name": user.get("name", ""),
        "email": user.get("email", ""),
        "first_name": user.get("firstName", ""),
        "last_name": user.get("lastName", ""),
        "role": m.get("role", ""),
        "group_id": m.get("groupId", ""),
        # Points live in metadata.spData.pts (a JSON string), NOT in
        # member.metadata.points (which is absent → always read as 0).
        "points": int(sp.get("pts", 0) or 0),
        "level": int(sp.get("lv", 0) or 0),
        "is_online": bool(meta.get("online")),
        "last_active": _ns_or_iso_to_dt(last_offline),
        "picture_url": meta.get("pictureProfile") or meta.get("picture", ""),
        "bio": meta.get("bio", ""),
    }


def post(tree: dict) -> dict:
    """Flatten one raw ``pageProps.postTrees[]`` tree into a post record."""
    p = tree.get("post") or {}
    u = p.get("user") or {}
    meta = p.get("metadata") or {}
    skool_id = p.get("id", "")
    root_id = p.get("rootId", "")
    return {
        "skool_id": skool_id,
        "name": p.get("name", ""),  # URL slug, NOT the display title
        # The human-readable title and body live in metadata; without them a
        # post record is just a slug and an AI can't summarize anything.
        "title": meta.get("title", ""),
        "content": meta.get("content", ""),
        "post_type": p.get("postType", ""),
        "group_id": p.get("groupId", ""),
        "user_id": p.get("userId", ""),
        "user_name": u.get("name", ""),
        "root_id": root_id,
        "is_toplevel": root_id == "" or root_id == skool_id,
        "comments": int(meta.get("comments", 0) or 0),
        "upvotes": int(meta.get("upvotes", 0) or 0),
        "created_at": _ns_or_iso_to_dt(p.get("createdAt")),
    }


def comments(merged: dict) -> list[dict]:
    """Flatten a merged comment tree into a list of comment records.

    Walks recursively, setting ``parent_id`` so replies point at their parent
    comment (or the post itself for first-layer comments). api2 uses
    snake_case, so we read snake_case first, camelCase as fallback.
    """
    out: list[dict] = []
    children = ((merged.get("post_tree") or {}).get("children")) or []
    _walk_comments(children, "", out)
    return out


def _walk_comments(nodes: list, explicit_parent: str, out: list) -> None:
    for node in nodes:
        p = node.get("post") or {}
        skool_id = p.get("id")
        if not skool_id:
            continue
        u = p.get("user") or {}
        meta = p.get("metadata") or {}
        root_id = p.get("root_id") or p.get("rootId") or ""
        parent_id = explicit_parent or root_id
        out.append({
            "skool_id": skool_id,
            # Body is in metadata.content; `name` is a slug-ish title that is
            # None on real comments (only the self-check's fixtures set it).
            "text": meta.get("content") or p.get("name") or "",
            "user_id": p.get("user_id") or p.get("userId") or "",
            "user_name": u.get("name", ""),
            "root_id": root_id,
            "parent_id": parent_id,
            "upvotes": int(meta.get("upvotes", 0) or 0),
            "created_at": _micros_to_dt(p.get("created_at") or p.get("createdAt")),
        })
        sub = node.get("children") or []
        if sub:
            _walk_comments(sub, skool_id, out)


def like(user: dict, post_skool_id: str) -> dict:
    """Flatten one raw ``users[]`` liker into a like record (mixed case)."""
    return {
        "post_skool_id": post_skool_id,
        "user_skool_id": str(user.get("id", "")),
        "user_name": user.get("name", ""),
        "user_first_name": user.get("first_name") or user.get("firstName", ""),
        "user_last_name": user.get("last_name") or user.get("lastName", ""),
    }


def profile(user: dict) -> dict:
    """Flatten a raw ``pageProps.currentUser`` into a profile record."""
    pd = user.get("profileData") or {}
    m = pd.get("member") or {}
    return {
        "skool_id": user.get("id", ""),
        "name": user.get("name", ""),
        "email": user.get("email", ""),
        "first_name": user.get("firstName", ""),
        "last_name": user.get("lastName", ""),
        "role": m.get("role", ""),
        "total_posts": int(pd.get("totalPosts", 0) or 0),
        "total_followers": int(pd.get("totalFollowers", 0) or 0),
        "total_following": int(pd.get("totalFollowing", 0) or 0),
        "total_contributions": int(pd.get("totalContributions", 0) or 0),
        "total_groups": int(pd.get("totalGroups", 0) or 0),
        "groups_member_of": _slugs(pd.get("groupsMemberOf")),
        "groups_created_by_user": _slugs(pd.get("groupsCreatedByUser")),
    }


def _slugs(groups) -> list[str]:
    """Normalize a groups array to a plain list of slugs.

    Skool sends either ["slug1", ...] or [{"name": "slug1", ...}, ...].
    """
    if not isinstance(groups, list):
        return []
    out = []
    for g in groups:
        if isinstance(g, str) and g:
            out.append(g)
        elif isinstance(g, dict) and g.get("name"):
            out.append(g["name"])
    return out


if __name__ == "__main__":
    # ponytail: self-check for the quirky bits — ns timestamps, mixed-case
    # likes, recursive comment parenting. Runs with `python -m catknows.normalize`.
    ns = 1_700_000_000_000_000_000
    assert _ns_or_iso_to_dt(ns).year == 2023, "nanosecond parse failed"
    assert _ns_or_iso_to_dt("2024-01-15T10:00:00Z").year == 2024, "ISO parse failed"

    m = member({"id": "u1", "name": "Ann", "member": {"role": "admin"},
                "metadata": {"online": True, "lastOffline": ns,
                             "spData": '{"pts":42,"lv":3}'}})
    assert m["points"] == 42 and m["level"] == 3, "spData points/level parse failed"
    assert m["is_online"] and m["role"] == "admin"

    lk = like({"id": 7, "name": "Bo", "firstName": "Bo"}, "p1")  # camelCase liker
    assert lk["user_first_name"] == "Bo" and lk["user_skool_id"] == "7"

    # Shaped like real api2 comments: body in metadata.content (`name` is
    # None there), created_at an ISO string — both were silently dropped once.
    tree = {"post_tree": {"children": [
        {"post": {"id": "c1", "name": None, "user_id": "u1", "root_id": "p1",
                  "created_at": "2026-08-08T16:59:44.650886Z",
                  "metadata": {"content": "hi"}},
         "children": [{"post": {"id": "c2", "name": None, "user_id": "u2",
                                "created_at": 1_754_000_000_000_000,
                                "metadata": {"content": "re"}}}]}]}}
    cs = comments(tree)
    assert len(cs) == 2, "recursive walk missed nested comment"
    assert cs[0]["parent_id"] == "p1", "first-layer parent should be the post"
    assert cs[1]["parent_id"] == "c1", "nested reply should point at its parent comment"
    assert cs[0]["text"] == "hi" and cs[1]["text"] == "re", "comment body must come from metadata.content"
    assert cs[0]["created_at"].year == 2026, "ISO comment timestamp must parse"
    assert cs[1]["created_at"].year == 2025, "microsecond comment timestamp must parse"
    print("normalize self-check OK")
