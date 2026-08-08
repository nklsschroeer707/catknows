"""The public Skool API — one method per thing you can pull from Skool.

This is the surface you (or an AI reading the docs) actually call. Each method
maps to one documented endpoint in docs/API.md and returns the raw Skool JSON,
so nothing is hidden or lossy. Pagination walks (members, posts, comments) are
handled for you and yield every page merged.

    from catknows import SkoolClient, login

    client = SkoolClient(login())
    for member in client.members("my-community"):
        print(member["name"])
"""

from __future__ import annotations

import time
from typing import Iterator

from .auth import Session
from .http import SkoolHTTP, SkoolHTTPError

# Be polite to Skool between paginated requests (WAF-friendly, less robotic).
_INTER_PAGE_DELAY_S = 0.8
_COMMENT_PAGE_LIMIT = 25
_MAX_COMMENT_PAGES = 400  # safety cap: 400 * 25 = 10k comments per post


class SkoolClient:
    def __init__(self, session: Session):
        self.http = SkoolHTTP(session)

    # -- members ---------------------------------------------------------------

    def members(self, community_slug: str, *, all_pages: bool = True) -> list[dict]:
        """All members of a community (raw ``pageProps.users[]`` objects).

        Sorted by Skool DESC on "last offline" so currently-active members come
        first. Walks every page by default. Each user object nests ``member``
        (role, groupId, points) and ``metadata`` (online, lastOffline in
        NANOSECONDS, pictureProfile, bio).
        """
        out: list[dict] = []
        page = 1
        while True:
            if page == 1:
                q = (f"/{community_slug}/-/members.json"
                     f"?t=active&sortType=-memberlastoffline&group={community_slug}")
            else:
                q = (f"/{community_slug}/-/members.json"
                     f"?t=active&sortType=-memberlastoffline&p={page}"
                     f"&online=&levels=&price=&courseIds=&monthly=false"
                     f"&annual=false&trials=false&group={community_slug}")
            data = self.http.get_next(q, community_slug)
            users = _dig(data, "pageProps", "users") or []
            out.extend(users)
            if not all_pages or not users:
                break
            total_pages = _dig(data, "pageProps", "totalPages")
            if total_pages and page >= int(total_pages):
                break
            if not total_pages:  # no pagination info -> single page
                break
            page += 1
            time.sleep(_INTER_PAGE_DELAY_S)
        return out

    # -- posts -----------------------------------------------------------------

    def posts(self, community_slug: str, *, all_pages: bool = True,
              limit: int | None = None) -> list[dict]:
        """All top-level posts of a community (raw ``pageProps.postTrees[]``).

        Each tree has a ``post`` object (id, name, postType, groupId, userId,
        rootId, metadata.comments, metadata.upvotes) plus the author ``user``.
        Default sort = last activity (posts with new comments bubble up).

        Unlike the members endpoint, the feed sends NO ``totalPages`` — we walk
        ``p`` until a page comes back empty. Results are deduped by post id:
        pinned posts appear twice on page 1 (once up top, once in the feed),
        and the activity sort can shift a post between pages mid-walk.

        ``limit`` stops paginating once that many unique posts are collected —
        callers that only need the first N shouldn't pay for the full walk.
        """
        out: list[dict] = []
        seen: set[str] = set()
        page = 1
        while True:
            q = (f"/{community_slug}.json?group={community_slug}"
                 + (f"&p={page}" if page > 1 else ""))
            data = self.http.get_next(q, community_slug)
            trees = _dig(data, "pageProps", "postTrees") or []
            fresh = []
            for tree in trees:
                post_id = (tree.get("post") or {}).get("id")
                if post_id in seen:
                    continue
                if post_id:
                    seen.add(post_id)
                fresh.append(tree)
            out.extend(fresh)
            if limit is not None and len(out) >= limit:
                return out[:limit]
            # Empty page = past the end; all-duplicates = feed is repeating
            # itself (also an end signal, and guards against looping forever).
            if not all_pages or not trees or not fresh:
                break
            page += 1
            time.sleep(_INTER_PAGE_DELAY_S)
        return out

    # -- profile ---------------------------------------------------------------

    def profile(self, user_name: str, community_slug: str) -> dict | None:
        """A single member's full profile (raw ``pageProps.currentUser``).

        Includes ``profileData`` (totalPosts, totalFollowers, totalGroups,
        dailyActivities, groupsMemberOf[], groupsCreatedByUser[]). ``user_name``
        is the Skool handle (the ``@handle`` slug), not the display name.
        """
        q = f"/@{user_name}.json?g={community_slug}&group=@{user_name}"
        data = self.http.get_next(q, community_slug)
        user = _dig(data, "pageProps", "currentUser")
        if not user or not user.get("id"):
            user = _dig(data, "pageProps", "renderData", "user")
        return user if (user and user.get("id")) else None

    # -- comments --------------------------------------------------------------

    def comments(self, post_skool_id: str, group_skool_id: str) -> dict:
        """Every comment on a post, all pages merged into one tree.

        Skool returns ~30 top-level comments per call; the response's ``last``
        field (created_at in microseconds) is the forward cursor. We walk
        ``created-gt`` from 0 until a page is empty or the cursor stops
        advancing, then merge every page's ``post_tree.children[]``. Nested
        replies arrive inline. NOTE: api2.skool.com uses **snake_case**
        (post.user_id, post.created_at) unlike the Next.js endpoints.

        Returns ``{"post_tree": {"children": [...]}, "pinned_post_tree": ...}``.
        """
        all_children: list[dict] = []
        pinned_tree = None
        cursor = 0
        last_cursor = 0

        for page_idx in range(_MAX_COMMENT_PAGES):
            if page_idx > 0:
                time.sleep(_INTER_PAGE_DELAY_S)
            q = (f"/posts/{post_skool_id}/comments"
                 f"?group-id={group_skool_id}&limit={_COMMENT_PAGE_LIMIT}"
                 f"&created-gt={cursor}")
            data = self.http.get_api2(q)

            if page_idx == 0:
                pinned_tree = data.get("pinned_post_tree")

            children = _dig(data, "post_tree", "children")
            if not children:
                break
            all_children.extend(children)

            last = data.get("last")
            if not isinstance(last, int):
                break
            last_cursor = last
            if last_cursor <= cursor:
                break  # cursor did not advance -> fully walked
            cursor = last_cursor

        merged = {"post_tree": {"children": all_children}, "last": last_cursor}
        if pinned_tree is not None:
            merged["pinned_post_tree"] = pinned_tree
        return merged

    # -- likes -----------------------------------------------------------------

    def likes(self, post_skool_id: str, group_skool_id: str) -> list[dict]:
        """Everyone who upvoted a post (raw ``users[]``).

        api2.skool.com endpoint, mixed case: user objects have ``id``, ``name``,
        and ``first_name``/``firstName`` + ``last_name``/``lastName``.
        """
        q = f"/posts/{post_skool_id}/vote-users?group-id={group_skool_id}"
        data = self.http.get_api2(q)
        return data.get("users") or []

    # -- community metadata ----------------------------------------------------

    def community_about(self, community_slug: str) -> dict:
        """The community's About page data (raw ``pageProps``)."""
        return self.http.get_next(
            f"/{community_slug}/about.json?group={community_slug}", community_slug
        )

    def group_info(self, group_skool_id: str) -> dict:
        """Group metadata by UUID (raw api2 response)."""
        return self.http.get_api2(f"/groups/{group_skool_id}")

    def discovery(self, page: int = 1) -> dict:
        """Skool's global discovery board (docs/API.md §6.2), one page of ~30.

        Uses the Next.js ``discovery.json`` route — the api2
        ``/groups/{gid}/discovery`` endpoint is WAF-blocked (403). Returns raw
        ``pageProps`` with ``groups[]`` (each ``{group, rank, tags}``),
        ``numGroups`` (1000), and ``categories[]``. Query params other than
        ``p`` are ignored server-side; filter/sort locally.
        """
        self.http.build_id("skool")  # ensure a buildId is cached (any real slug)
        q = "/discovery.json" if page <= 1 else f"/discovery.json?p={page}"
        data = self.http.get_next(q, "")
        return data.get("pageProps", data)

    def admin_metrics(self, group_skool_id: str, range_: str = "30d") -> dict:
        """Admin dashboard metrics (owner/admin only, raw api2 response)."""
        return self.http.get_api2(
            f"/groups/{group_skool_id}/admin-metrics?range={range_}&amt=monthly"
        )

    def calendar(self, community_slug: str, cal_date: int = 0) -> dict:
        """Calendar/events (raw ``pageProps``). ``cal_date`` = unix ts for a
        future month, 0 = current month."""
        q = f"/{community_slug}/calendar.json?group={community_slug}"
        if cal_date > 0:
            q = f"/{community_slug}/calendar.json?calDate={cal_date}&group={community_slug}"
        return self.http.get_next(q, community_slug)

    def classroom(self, community_slug: str) -> dict:
        """Classroom / courses (raw ``pageProps``)."""
        return self.http.get_next(
            f"/{community_slug}/classroom.json?group={community_slug}", community_slug
        )

    # -- chat ------------------------------------------------------------------

    def chat_channels(self, *, offset: int = 0, limit: int = 30) -> dict:
        """Your DM channels (docs/API.md §1.6) — the channel ids `send_dm` needs."""
        return self.http.get_api2(
            f"/self/chat-channels?offset={offset}&limit={limit}&last=true&unread-only=false"
        )

    # -- writing (docs/API.md §5) ----------------------------------------------
    # These act as YOU, visible to real members. Test in a private community
    # first; notify_members emails everyone in the group.

    def create_post(
        self,
        community_slug: str,
        title: str,
        content: str,
        *,
        labels: str = "",
        video_links: str = "",
        notify_members: bool = False,
    ) -> dict:
        """Create a normal feed post (§5.1). Returns the created post object.

        `labels` is a category id, `video_links` a YouTube/Loom/Vimeo URL —
        both optional. `notify_members=True` is Skool's email broadcast: it
        emails every member and is subject to the group's cooldown.
        """
        metadata: dict = {"title": title, "content": content, "action": 0}
        if labels:
            metadata["labels"] = labels
        if video_links:
            metadata["video_links"] = video_links
        query = "?notify=members&follow=true" if notify_members else "?follow=true"
        return self.http.post_api2(
            f"/posts{query}",
            {
                "post_type": "generic",
                "group_id": self.group_id_for(community_slug),
                "metadata": metadata,
            },
        )

    def send_dm(self, channel_id: str, content: str) -> dict:
        """Send a direct message into an existing chat channel (§5.7).

        `channel_id` comes from `chat_channels()`. Returns the created message.
        """
        return self.http.post_api2(
            f"/channels/{channel_id}/messages?ct=wdm",
            {"content": content, "attachments": []},
        )

    # -- convenience -----------------------------------------------------------

    def group_id_for(self, community_slug: str) -> str:
        """Discover a community's group UUID without the user supplying it.

        The api2 endpoints (comments, likes, discovery) need the group UUID,
        but the user only knows the slug. It "falls out" of the first posts
        response as ``post.groupId`` — so one posts fetch bootstraps it.
        """
        trees = self.posts(community_slug, all_pages=False)
        for tree in trees:
            gid = _dig(tree, "post", "groupId")
            if gid:
                return gid
        raise SkoolHTTPError(
            f"Could not determine group UUID for '{community_slug}' "
            "(no posts with a groupId found)."
        )


def _dig(obj, *keys):
    """Safe nested lookup: _dig(data, 'pageProps', 'users') -> value or None."""
    for k in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
    return obj
