"""The public Skool API — one method per thing you can pull from Skool.

This is the surface you (or an AI reading the docs) actually call. Each method
maps to one documented endpoint in docs/API.md and returns the raw Skool JSON,
so nothing is hidden or lossy. Pagination walks (members, posts, comments) are
handled for you and yield every page merged.

    from skoolapi import SkoolClient, login

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

    def posts(self, community_slug: str, *, all_pages: bool = True) -> list[dict]:
        """All top-level posts of a community (raw ``pageProps.postTrees[]``).

        Each tree has a ``post`` object (id, name, postType, groupId, userId,
        rootId, metadata.comments, metadata.upvotes) plus the author ``user``.
        Default sort = last activity (posts with new comments bubble up).
        """
        out: list[dict] = []
        page = 1
        while True:
            q = (f"/{community_slug}.json?group={community_slug}"
                 + (f"&p={page}" if page > 1 else ""))
            data = self.http.get_next(q, community_slug)
            trees = _dig(data, "pageProps", "postTrees") or []
            out.extend(trees)
            if not all_pages or not trees:
                break
            total_pages = _dig(data, "pageProps", "totalPages")
            if not total_pages or page >= int(total_pages):
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

    def discovery(self, group_skool_id: str) -> dict:
        """Related/discovered communities (raw api2 response)."""
        return self.http.get_api2(f"/groups/{group_skool_id}/discovery")

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
