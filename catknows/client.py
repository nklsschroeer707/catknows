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

import json
import mimetypes
import os
import pathlib
import re
import time
from typing import Iterator
from urllib.parse import quote

from .auth import Session
from .http import SkoolHTTP, SkoolHTTPError, SkoolSecurityChallenge

# Be polite to Skool between paginated requests (WAF-friendly, less robotic).
# CATKNOWS_PAGE_DELAY (seconds) overrides — lower is faster but more robotic;
# you carry the 403/ban risk yourself.
_INTER_PAGE_DELAY_S = float(os.environ.get("CATKNOWS_PAGE_DELAY", "0.8"))
_COMMENT_PAGE_LIMIT = 25
_WAIT_TRIES = 10     # dashboard analytics: ask /wait at most this often ...
_WAIT_POLL_S = 1.0   # ... this far apart (the website gives up after 10 s)
_MAX_COMMENT_PAGES = 400  # safety cap: 400 * 25 = 10k comments per post


class MemberList(list):
    """``members()`` result. ``incomplete=True`` means Skool stopped serving
    fresh pages before ``total_pages`` was reached — the list is a truncated
    view, NOT the whole community. Measured 2026-08-15: Skool re-serves page 1
    per community/role/session — a fresh session walked 592-member hoomans
    fully as a regular member, while 5.5k-member theskoolhub repeated page 1
    from page 2 on with the very same session. A stale WAF token additionally
    degrades members.json into 202-challenges/403s.

    ``total_members`` is Skool's own count for the same query
    (``pageProps.total``), carried so callers can check the walk against it
    without a second request. When it exceeds ``len(...)`` the walk is short —
    ``short_by`` says by how much.

    Measured 2026-08-17 on 36-member catnose, as OWNER: every page (1..4)
    returned byte-identical rows, so the walk collected 30 and reported
    ``incomplete=False`` because it had walked ``totalPages`` (2) of 2. Skool
    served three different numbers in one response: 30 rows, ``total`` 35, and
    ``currentGroup.metadata.totalMembers`` 36. No parameter got page 2 out
    (bare ``p=2``, the full filter tail, other sorts, itemsPerPage/limit/
    pageSize — all identical to page 1), while hoomans paginated correctly
    across 20 pages with the same code and session. Page count alone therefore
    cannot decide completeness, which is why the row count is compared against
    ``total`` as well: a revoke pass that trusts a short list cancels paying
    members.

    ``capped_by_role`` means we stopped after page 1 on purpose: Skool's own UI
    shows a regular member only the first page of a community's member list, and
    walking further is a workaround we choose not to take. Distinct from
    ``incomplete``, which reports Skool failing to serve pages it announced."""

    incomplete = False
    pages_walked = 1
    total_pages = 1
    total_members = None  # Skool's own count for this query, None if unreported
    capped_by_role = False  # we stopped at page 1 because the viewer is a member

    @property
    def short_by(self) -> int:
        """How many members Skool counts but did not hand over (0 if none)."""
        if self.total_members is None:
            return 0
        return max(0, self.total_members - len(self))


# The feed's sort menu -> Skool's ``s=`` value. "activity" is Skool's default
# (newest-cm, posts with new comments bubble up) and sends no parameter.
POST_SORTS = {
    "activity": None,
    "new": "newest",
    "top_day": "best-1d",
    "top_week": "best-1w",
    "top_month": "best-1m",
    "top_year": "best-1y",
    "top_all": "best",
}


def _plain(name: str) -> str:
    """A category name without emoji, spacing or case: "📮 Feedback" -> "feedback"."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _id_segment(value, name: str) -> str:
    """An id for a path piece or a query value: checked, then percent-encoded.

    Refuses (``ValueError`` naming the argument, never its value) anything that
    is not text, empty or blank text, and text made only of dots. ``.`` and ``..``
    survive ``quote(..., safe="")`` unchanged, and libcurl folds such a piece
    away (``/posts/../pin`` becomes ``/pin``), so a model-supplied id could
    otherwise steer the request to another endpoint. Everything else is encoded,
    so an id cannot leave its path segment or add a query.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required (non-empty text).")
    if set(value) <= {"."}:
        raise ValueError(f"{name} must not be made only of dots.")
    return quote(value, safe="")


_MEMBER_SORTS = {
    "newest": "-memberapprovedat",
    "last_active": "-memberlastoffline",
    "most_points": "-memberpoints",
}
_MEMBER_LIFECYCLES = ("active", "cancelling", "churned", "banned")
# Skool's boolean member filters, in the order the browser's page-2 tail sends
# them. Separate flags combine as AND server-side (annual=true&free=true = 0).
_MEMBER_FLAGS = ("admin", "online", "trials", "monthly", "annual", "oneTime", "free")


def _members_query(slug: str, page: int, *, sort_type: str, lifecycle: str = "",
                   flags: dict | None = None, tiers: list[str] = (),
                   course_ids: list[str] = ()) -> str:
    """The members.json query for one page, browser-faithful.

    Verified live 2026-08-15: page 1 takes only the active filters; follow-up
    pages carry the full filter tail (unset flags as ``false``/empty, exactly
    like Skool's own browser requests — including ``oneTime``/``free``, which
    the old hand-written tail omitted).
    """
    flags = flags or {}
    q = []
    if lifecycle:
        q.append(f"t={lifecycle}")
    q.append(f"sortType={sort_type}")
    if page > 1:
        q.append(f"p={page}")
        q.append("online=true" if flags.get("online") else "online=")
        q.append("levels=")
        q.append("price=")
        q.append(f"courseIds={','.join(course_ids)}")
        for name in ("monthly", "annual", "oneTime", "trials", "free"):
            q.append(f"{name}={'true' if flags.get(name) else 'false'}")
        if flags.get("admin"):
            q.append("admin=true")
        if tiers:
            q.append(f"tiers={','.join(tiers)}")
    else:
        for name in _MEMBER_FLAGS:
            if flags.get(name):
                q.append(f"{name}=true")
        if tiers:
            q.append(f"tiers={','.join(tiers)}")
        if course_ids:
            q.append(f"courseIds={','.join(course_ids)}")
    q.append(f"group={slug}")
    return f"/{slug}/-/members.json?" + "&".join(q)


class SkoolClient:
    def __init__(self, session: Session):
        self.http = SkoolHTTP(session)

    # -- members ---------------------------------------------------------------

    def members(self, community_slug: str, *, all_pages: bool = True,
                limit: int | None = None, lifecycle: str = "",
                sort: str = "last_active", admins: bool = False,
                online: bool = False, trials: bool = False,
                monthly: bool = False, annual: bool = False,
                one_time: bool = False, free: bool = False,
                tiers: list[str] = (), course_ids: list[str] = ()) -> MemberList:
        """All members of a community (raw ``pageProps.users[]`` objects).

        Sorted by Skool DESC on "last offline" so currently-active members come
        first (``sort``: newest | last_active | most_points). Walks every page
        by default. Each user object nests ``member`` (role, groupId, points)
        and ``metadata`` (online, lastOffline in NANOSECONDS, pictureProfile,
        bio).

        Filters (docs/API.md §1.1, verified live — they work for regular
        members too, not just admins): ``lifecycle`` is Skool's ``t=`` value
        (active/cancelling/churned/banned; empty = default view). The boolean
        flags and multiple ``course_ids`` combine as AND server-side; multiple
        ``tiers`` combine as OR. There is no native OR across flag families —
        "annual OR free" needs two calls, unioned by the caller.

        ``limit`` stops paginating once that many unique members are collected
        — callers that need the first N shouldn't walk a 160k community.

        Deduped by user id, like ``posts()``. The sort is live — members shift
        between pages mid-walk and reappear — and a degraded session can make
        Skool re-serve page 1 for every follow-up page. Both cases used to
        ship as duplicates: a walk of 65 returned 65 rows of which only ~30
        were distinct. The dedupe fixed the rows; the returned
        :class:`MemberList` now also SAYS when the walk ended early
        (``incomplete``) instead of letting a truncated list look complete.
        """
        # No default `t=active`: it's redundant, not forbidden — and Skool
        # FLIPPED it server-side. Measured 2026-08-12: 404 for everyone,
        # owners included (four communities). Measured 2026-08-15: 200 for
        # everyone, returning the exact unfiltered list (it's the default
        # view). Unknown t-values (t=quatsch) still 404 — which is why
        # `lifecycle` is validated here instead of letting Skool answer with
        # a misleading "no access" 404.
        if lifecycle and lifecycle not in _MEMBER_LIFECYCLES:
            raise ValueError(
                f"Unknown lifecycle {lifecycle!r} — Skool 404s on unknown values. "
                f"One of: {', '.join(_MEMBER_LIFECYCLES)} (or empty for the default view).")
        if sort not in _MEMBER_SORTS:
            raise ValueError(
                f"Unknown sort {sort!r}. One of: {', '.join(_MEMBER_SORTS)}.")
        flags = {"admin": admins, "online": online, "trials": trials,
                 "monthly": monthly, "annual": annual, "oneTime": one_time,
                 "free": free}
        tiers, course_ids = list(tiers), list(course_ids)

        out = MemberList()
        seen: set[str] = set()
        page = 1
        total_pages = 0
        while True:
            q = _members_query(community_slug, page,
                               sort_type=_MEMBER_SORTS[sort], lifecycle=lifecycle,
                               flags=flags, tiers=tiers, course_ids=course_ids)
            data = self.http.get_next(q, community_slug)
            users = _dig(data, "pageProps", "users") or []
            tp = _dig(data, "pageProps", "totalPages")
            if tp:
                total_pages = int(tp)
            # Skool's own verdict on who is looking, in the very response we are
            # already reading — no second request, and no reliance on
            # self/groups, which reports plain "member" for communities where
            # the same account is group-admin (measured 2026-08-17 across 53
            # communities: only owner and member ever came back).
            # True for owner/admin/moderator, since Skool gates the members
            # admin view on it; a regular member gets page 1 in the UI too.
            if page == 1:
                is_staff = bool(_dig(data, "pageProps", "isAdmin"))

            # Skool's own count for this exact query — the only cross-check we
            # get without a second request, and the one that catches a walk
            # that ran every page it was told about and still came up short.
            reported = _dig(data, "pageProps", "total")
            if isinstance(reported, int):
                out.total_members = reported
            fresh = []
            for user in users:
                uid = user.get("id")
                if uid in seen:
                    continue
                if uid:
                    seen.add(uid)
                fresh.append(user)
            out.extend(fresh)
            if limit is not None and len(out) >= limit:
                del out[limit:]  # trim in place — slicing would drop the flags
                break
            # A regular member sees the first page and no more. Skool's UI does
            # the same; paging past it is a workaround we deliberately drop.
            # Not a size cap (that one lives in mcp_server._cap and still
            # applies on top) — this one is about what a member may see at all.
            # Only a real cut counts: if page 1 was already the last page, the
            # member has the whole list and flagging a cap would be a lie.
            if not is_staff:
                out.capped_by_role = bool(total_pages and total_pages > 1)
                break
            # Empty page = past the end; all-duplicates = Skool is repeating a
            # page it already served (also an end signal, and the guard that
            # keeps a degraded walk from collecting page 1 over and over).
            if not all_pages or not users or not fresh:
                break
            if not total_pages or page >= total_pages:
                break  # reached the last page (or no pagination info at all)
            page += 1
            time.sleep(_INTER_PAGE_DELAY_S)
        out.pages_walked = page
        out.total_pages = total_pages or page
        # Honest truncation: we broke off before the last page without having
        # satisfied the caller's limit. A silently short list looks complete
        # and is worse than the duplicates ever were.
        satisfied = limit is not None and len(out) >= limit
        # A role cap is a deliberate stop, not a failed walk. Reporting both
        # would tell a caller to retry against a wall that is there by design,
        # so capped_by_role owns this case alone.
        out.incomplete = bool(all_pages and page < out.total_pages
                              and not satisfied and not out.capped_by_role)
        # Pages alone are not proof. catnose walked 2 of 2 pages and still
        # returned 30 of 35 because Skool re-served page 1 as page 2 — page
        # count said complete, the row count said otherwise. Only trust this
        # when the caller wanted everything; a limit=25 walk is short on
        # purpose.
        if all_pages and not satisfied and out.short_by and not out.capped_by_role:
            out.incomplete = True
        return out

    # -- posts -----------------------------------------------------------------

    def posts(self, community_slug: str, *, all_pages: bool = True,
              limit: int | None = None, sort: str = "activity",
              unread_only: bool = False, category_id: str = "") -> list[dict]:
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

        Skool sorts and filters server-side, the same switches as the feed's
        sort menu (docs/API.md §1.2): ``sort`` is one of ``POST_SORTS``,
        ``unread_only`` is the "Unread" tab (Skool pairs it with "newest",
        whatever else is asked), ``category_id`` a label id from
        :meth:`post_categories`. Every page carries the same filters.
        """
        if sort not in POST_SORTS:
            raise ValueError(f"sort must be one of {', '.join(POST_SORTS)}")
        filters = ""
        if unread_only:
            filters += "&s=newest&fl=unr"
        elif POST_SORTS[sort]:
            filters += f"&s={POST_SORTS[sort]}"
        if category_id:
            filters += f"&c={category_id}"
        out: list[dict] = []
        seen: set[str] = set()
        page = 1
        while True:
            q = (f"/{community_slug}.json?group={community_slug}{filters}"
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

    def resolve_member(self, community_slug: str, who: str) -> dict:
        """A handle ("niklas") or display name ("Niklas Schröer") -> the raw user.

        A handle is tried first on the profile route (exact, one request).
        Anything else goes through the member search; a single hit, or the
        one hit whose full name matches exactly, wins. Several candidates
        raise with their handles, so the caller can pick instead of guessing.
        """
        who = who.strip().lstrip("@")
        if not who:
            raise ValueError("author must not be empty")
        if " " not in who:
            user = self.profile(who, community_slug)
            if user:
                return user
        hits = [h.get("user") or {} for h in
                (self.search(community_slug, who, kind="members").get("members") or [])]
        hits = [u for u in hits if u.get("id")]
        want = _plain(who)
        exact = [u for u in hits if want in (
            _plain(f"{u.get('firstName', '')} {u.get('lastName', '')}"),
            _plain(u.get("name", "")))]
        if len(exact) == 1:
            return exact[0]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise ValueError(f"no member '{who}' in {community_slug}")
        names = ", ".join(f"{u.get('firstName', '')} {u.get('lastName', '')} (@{u.get('name')})"
                          for u in (exact or hits)[:10])
        raise ValueError(f"'{who}' matches several members of {community_slug}: "
                         f"{names} — pass the @handle")

    def posts_by(self, community_slug: str, user_name: str, *,
                 limit: int | None = None) -> list[dict]:
        """Top-level posts one member CREATED in one community (raw post trees).

        Reads the member's profile scoped to the community (docs/API.md §1.3b):
        ``@{handle}.json?g={slug}`` pages through every post the member takes
        part in there — their own, plus others' posts they commented on or are
        @mentioned in (measured 2026-09-22: 77 entries, 46 own = all 46 in the
        feed). Only the member's own posts are kept. Skool's ``hasMore`` is
        always false here, so the walk runs until an empty or repeated page.
        """
        user = self.profile(user_name, community_slug)
        if not user:
            raise ValueError(f"no member @{user_name} in {community_slug}")
        uid = user["id"]
        out: list[dict] = []
        seen: set[str] = set()
        page = 1
        while True:
            q = (f"/@{user_name}.json?g={community_slug}&group=@{user_name}"
                 + (f"&p={page}" if page > 1 else ""))
            trees = _dig(self.http.get_next(q, community_slug),
                         "pageProps", "postTrees") or []
            fresh = [t for t in trees if (t.get("post") or {}).get("id") not in seen]
            seen.update((t.get("post") or {}).get("id") for t in fresh)
            out.extend(t for t in fresh if (t.get("post") or {}).get("userId") == uid)
            if limit is not None and len(out) >= limit:
                return out[:limit]
            if not trees or not fresh:
                break
            page += 1
            time.sleep(_INTER_PAGE_DELAY_S)
        return out

    def post_categories(self, community_slug: str, *, via_api: bool = False) -> list[dict]:
        """The feed's categories (Skool calls them labels): id, name, post count.

        By default read from the feed's first page, the same URL an unfiltered
        :meth:`posts` call fetches first, so it is usually a cache hit and costs
        no extra request.

        ``via_api=True`` reads ``GET /groups/{gid}/labels``
        (``metadata.display_name``, ``metadata.posts``), Skool's own
        ``getGroupLabels``, at the price of two requests (group id, labels).
        Only if Skool answers that with 401, 403 or 404 does it fall back to the
        feed page; any other error is raised. Both ways return the same shape.
        """
        if not via_api:
            return self._post_categories_via_page(community_slug)
        gid = self.group_id_for(community_slug)
        try:
            data = self.http.get_api2(f"/groups/{gid}/labels")
        except SkoolHTTPError as e:
            if e.status not in (401, 403, 404):
                raise
            return self._post_categories_via_page(community_slug)
        return [{"id": lb.get("id", ""),
                 "name": (lb.get("metadata") or {}).get("display_name", ""),
                 "posts": (lb.get("metadata") or {}).get("posts")}
                for lb in data.get("labels") or []]

    def _post_categories_via_page(self, community_slug: str) -> list[dict]:
        """The categories as the feed page carries them (camelCase)."""
        data = self.http.get_next(f"/{community_slug}.json?group={community_slug}",
                                  community_slug)
        labels = _dig(data, "pageProps", "currentGroup", "labels") or []
        return [{"id": lb.get("id", ""),
                 "name": (lb.get("metadata") or {}).get("displayName", ""),
                 "posts": (lb.get("metadata") or {}).get("posts")} for lb in labels]

    def resolve_category(self, community_slug: str, category: str) -> str:
        """A category name ("feedback", "📮 Feedback") or label id -> the label id."""
        cats = self.post_categories(community_slug)
        want = _plain(category)
        for cat in cats:
            if category == cat["id"] or (want and want == _plain(cat["name"])):
                return cat["id"]
        names = ", ".join(c["name"] for c in cats) or "none"
        raise ValueError(f"no category '{category}' in {community_slug}; it has: {names}")

    def search(self, community_slug: str, query: str, *, kind: str = "posts",
               page: int = 1) -> dict:
        """Skool's own community search (docs/API.md §1.8), raw ``pageProps``.

        ``kind`` is ``posts`` (``postTrees``, 10 per page, ``totalPosts`` /
        ``totalPostPages``) or ``members`` (``members``, ``totalMembers``).
        Member hits are *membership* objects with the person under ``user`` —
        flatten them with ``normalize.search_member``. Note ``members.json``
        ignores a ``q``: searching members only works here.
        """
        if not query.strip():
            raise ValueError("query must not be empty")
        if kind not in ("posts", "members"):
            raise ValueError("kind must be 'posts' or 'members'")
        if page < 1:
            raise ValueError("page starts at 1")
        q = (f"/{community_slug}/-/search.json?q={quote(query, safe='')}"
             f"&t={kind}&group={community_slug}" + (f"&p={page}" if page > 1 else ""))
        return (self.http.get_next(q, community_slug) or {}).get("pageProps") or {}

    def post_detail(self, community_slug: str, post_name: str) -> dict:
        """One post's own page (raw ``pageProps``), by its URL slug.

        Worth a second request because the feed does NOT carry everything the
        post has: file attachments only reach ``metadata.attachmentsData`` here
        (the feed sends the bare id list), and Skool-hosted videos only get
        their playback handles here, in ``postTree.videos[]``.

        ``post_name`` is the post's ``name`` (URL slug), not its UUID — that is
        what the Next.js route keys on.
        """
        return self.http.get_next(
            f"/{community_slug}/{post_name}.json?group={community_slug}",
            community_slug,
        ).get("pageProps", {})

    def video_transcript(self, community_slug: str, post_name: str) -> list[dict]:
        """Transcripts of a post's Skool-hosted videos, via Skool's own player.

        Skool hosts video on Mux and, for anything with an audio track, ships
        an auto-generated "English CC" subtitle track. Nothing in the Skool
        payload says so: the route is postTree.videos[] → the signed Mux HLS
        master → its SUBTITLES track → WebVTT segments, which we stitch.

        Two hard requirements, both measured: the playback token is
        Referer-restricted (no Skool Referer → HTTP 403 E184-1), and it expires
        (``expire``), so the manifest must be fetched with a token freshly read
        from the page. Videos with no audio carry no subtitle track at all and
        come back ``has_transcript: False`` rather than raising — that is a
        real state, not an error.

        Externally hosted videos (``metadata.videoLinksData``: YouTube, Loom,
        …) are NOT covered — they never reach Mux and each hoster has its own
        transcript path.
        """
        out = []
        for v in (self.post_detail(community_slug, post_name)
                  .get("postTree") or {}).get("videos") or []:
            rec = {"video_id": v.get("id", ""),
                   "duration_s": round((v.get("duration") or 0) / 1000, 3),
                   "has_transcript": False, "transcript": "", "cues": []}
            master = self.http.get_mux(
                f"https://stream.mux.com/{v['playbackId']}.m3u8"
                f"?token={v['playbackToken']}")
            track = re.search(r'TYPE=SUBTITLES[^\n]*?URI="([^"]+)"', master)
            if track:
                playlist = self.http.get_mux(track.group(1))
                vtt = "".join(
                    self.http.get_mux(seg) for seg in playlist.splitlines()
                    if seg and not seg.startswith("#")
                )
                rec["cues"] = _vtt_cues(vtt)
                rec["transcript"] = " ".join(c["text"] for c in rec["cues"])
                rec["has_transcript"] = bool(rec["cues"])
            out.append(rec)
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

    def follows(self, user_name: str, direction: str = "followers", *,
                limit: int = 0) -> dict:
        """Who follows ``user_name`` / whom they follow (docs/API.md §1.3a).

        ``direction`` is ``followers`` or ``following`` — the profile page's
        ``?t=`` tab. Pages are 30 entries, walked with ``&p=N``; past the last
        page ``follows.list`` comes back ``null``. Returns
        ``{"total", "entries", "incomplete"}`` with the raw ``{src, dst,
        following}`` entries. Each entry carries the profile owner's full user
        object on one side — with their email and payout id when that's you —
        so callers must only surface the *other* side (`normalize.follow`).
        ``limit`` stops the walk once that many entries are in (0 = all).
        """
        if direction not in ("followers", "following"):
            raise ValueError("direction must be 'followers' or 'following'")
        self.http.build_id("skool")  # ensure a buildId is cached (any real slug)
        total_key = "totalFollowers" if direction == "followers" else "totalFollowing"
        entries: list[dict] = []
        seen: set = set()
        total = 0
        page = 1
        while True:
            if page > 1:
                time.sleep(_INTER_PAGE_DELAY_S)
            q = f"/@{user_name}.json?t={direction}&group=@{user_name}"
            if page > 1:
                q += f"&p={page}"
            data = self.http.get_next(q, "")
            pd = _dig(data, "pageProps", "renderData", "user", "profileData") or {}
            if page == 1:
                total = int(pd.get(total_key) or 0)
            batch = (pd.get("follows") or {}).get("list") or []
            # Deduped like members(): a re-served page must not pad the list.
            fresh = [e for e in batch if _follow_key(e) not in seen]
            seen.update(_follow_key(e) for e in fresh)
            entries.extend(fresh)
            if not fresh or len(entries) >= total or (limit and len(entries) >= limit):
                break
            page += 1
        if limit:
            entries = entries[:limit]
        # Same guard as members(): a walk that stops short must say so.
        return {"total": total, "entries": entries,
                "incomplete": len(entries) < min(total, limit or total)}

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
            if page_idx == 0:
                # Pinned comments (and their replies) are NOT in post_tree and
                # only come back when asked for (catnose 2026-11-01).
                q += "&pinned=true"
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

    def likes_page(self, post_skool_id: str, *, limit: int = 10, cursor: str = "",
                   tab: str = "upvotes") -> dict:
        """ONE page of a post's voters, raw (docs/API.md §1.5).

        ``GET /posts/{id}/vote-users?limit=&tab=&cursor=``, in that order, the
        way Skool's own client sends it: the first page with ``cursor=""``, the
        next with the ``cursor`` of the answer while ``has_more`` is true. No
        ``group-id``. Returns ``{tab, users[], has_more, cursor,
        total_upvote_users, total_downvote_users}``.
        """
        return self.http.get_api2(
            f"/posts/{post_skool_id}/vote-users"
            f"?limit={limit}&tab={tab}&cursor={quote(cursor, safe='')}"
        )

    def likes(self, post_skool_id: str, group_skool_id: str = "", *,
              tab: str = "upvotes", page_limit: int = 10,
              max_pages: int = 50) -> list[dict]:
        """Everyone who upvoted a post (raw ``users[]``), every page walked.

        api2.skool.com endpoint, mixed case: user objects have ``id``, ``name``,
        and ``first_name``/``firstName`` + ``last_name``/``lastName``.

        Pages with :meth:`likes_page` until ``has_more`` is false, a page is
        empty, the cursor is empty or did not move, or ``max_pages`` is reached.
        ``group_skool_id`` is still accepted so old callers keep working, but
        it is never sent: the vote-users call does not carry it.
        """
        out: list[dict] = []
        cursor = ""
        for page_idx in range(max_pages):
            if page_idx > 0:
                time.sleep(_INTER_PAGE_DELAY_S)
            data = self.likes_page(post_skool_id, limit=page_limit, cursor=cursor, tab=tab)
            users = data.get("users") or []
            out.extend(users)
            nxt = data.get("cursor") or ""
            if not data.get("has_more") or not users or not nxt or nxt == cursor:
                break
            cursor = nxt
        return out

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

        Uses the Next.js ``discovery.json`` route. Returns raw ``pageProps``
        with ``groups[]`` (each ``{group, rank, tags}``), ``numGroups`` (1000),
        and ``categories[]``. Query params other than ``p`` are ignored
        server-side; filter/sort locally. For your *own* community's true
        standing (beyond the top 1000) see :meth:`discovery_rank`.
        """
        self.http.build_id("skool")  # ensure a buildId is cached (any real slug)
        q = "/discovery.json" if page <= 1 else f"/discovery.json?p={page}"
        data = self.http.get_next(q, "")
        pp = data.get("pageProps", data)
        # Since ~Aug 2026 Skool ships rank: 0 on every row — the ordinal is
        # only implicit in the page order (30 per page). Compute it here so
        # every consumer (MCP tool, snapshot) gets real ranks; if Skool ever
        # revives the field, its non-zero value wins.
        for i, row in enumerate(pp.get("groups") or []):
            if isinstance(row, dict) and not row.get("rank"):
                row["rank"] = (page - 1) * 30 + i + 1
        return pp

    def revenue_leaderboard(self, *, category_id: str = "") -> dict:
        """Skool's top-earning communities, raw ``pageProps`` (docs/API.md §6.1).

        The skoolers "games" tab: ``rows[]`` (100 overall, 50 per category)
        and ``categories[]`` (``id``, ``name``). There is no second page. The
        viewer must be eligible for skoolers (own a community) or Skool
        redirects to the about page.
        """
        q = "/skoolers/-/games.json?group=skoolers"
        if category_id:
            q += f"&category={category_id}"
        return (self.http.get_next(q, "skoolers") or {}).get("pageProps") or {}

    def leaderboards(self, community_slug: str) -> dict:
        """A community's leaderboards page, raw ``pageProps`` (docs/API.md §1.10).

        One request holds all three boards (``allTime``, ``past30Days``,
        ``past7Days``, the top 10 each; the page loads no more), the viewer's
        own rank per board (``selfAllTime``/``self30Days``/``self7Days``, null
        when unranked) and ``groupLevels``. Members-only. Every row carries the
        full user object and the viewer's own carries credential-class fields:
        flatten with :func:`normalize.leaderboard`, never return it raw.
        """
        return (self.http.get_next(
            f"/{community_slug}/-/leaderboards.json?group={community_slug}", community_slug
        ) or {}).get("pageProps") or {}

    def discovery_rank(self, group_skool_id: str) -> dict:
        """Your own community's discovery standing (owner-only, docs/API.md §1.7).

        Returns ``{is_showing, rank, category:{id,name}, category_rank,
        language_code, boost_enabled, rank_updated_at}``. Works beyond the
        top-1000 board; foreign communities get a 401.
        """
        return self.http.get_api2(f"/groups/{group_skool_id}/discovery")

    def admin_metrics(self, group_skool_id: str, range_: str = "30d") -> dict:
        """Admin dashboard metrics (owner/admin only, raw api2 response).

        ``range_`` is effectively fixed: Skool 400s ("unsupported time range")
        on 7d, 60d, 90d, 1y, 12m and bare numbers — only ``30d`` is accepted
        (probed live 2026-08-14). The parameter stays because the endpoint
        takes one; don't advertise other values.
        """
        return self.http.get_api2(
            f"/groups/{group_skool_id}/admin-metrics?range={range_}&amt=monthly"
        )

    # -- join requests (admin/moderator) -------------------------------------

    # The pending page's two buttons -> the role Skool sets (docs/API.md §6.5).
    JOIN_DECISIONS = {"approve": "member", "decline": "declined"}

    def join_requests(self, community_slug: str, *, page: int = 1) -> dict:
        """One page (30) of the join queue, raw ``pageProps`` (docs/API.md §6.5).

        ``users[]`` are pending users; the application sits in ``member``
        (``member.id`` is what a decision needs). ``total``/``totalPages``
        count the whole queue. Owner/moderator only.
        """
        q = f"/{community_slug}/-/pending.json?group={community_slug}"
        if page > 1:
            q += f"&p={page}"
        return (self.http.get_next(q, community_slug) or {}).get("pageProps") or {}

    def decide_join_request(self, member_id: str, decision: str) -> dict:
        """Approve or decline one request, as the pending page's buttons do.

        ``POST /members/{member_id}/role`` with ``{"new": "member"}`` or
        ``{"new": "declined"}``. That endpoint sets ANY member's role, so
        callers must check the id is in the queue first (the MCP tool does).
        """
        if decision not in self.JOIN_DECISIONS:
            raise ValueError("decision must be 'approve' or 'decline'")
        return self.http.post_api2(f"/members/{member_id}/role",
                                   {"new": self.JOIN_DECISIONS[decision]})

    # The admin dashboard's charts (docs/API.md §1.9), owner/admin only.
    ANALYTICS_CHARTS = ("signups_by_source", "signups_by_day", "members",
                        "retention_members", "retention_cohorts",
                        "mrr", "mrr_cashflow", "mrr_unit")

    def _analytics(self, path: str) -> dict:
        """One dashboard number, through Skool's wait-token dance.

        First answer is ``{"token": t}``; ``/wait?token=t`` says
        ``in-progress`` or ``completed``; the same GET plus ``&token=t`` then
        carries ``{"data": ...}``. The website gives up after 10 s; so do we,
        asking once a second (a fixed pace, like everything else here).
        """
        first = self.http.get_api2(path) or {}
        token = first.get("token")
        if not token:
            return first.get("data") or {}
        for _ in range(_WAIT_TRIES):
            state = self.http.get_api2_text(f"/wait?token={token}")
            if state == "completed":
                break
            if state != "in-progress":
                raise RuntimeError(f"Skool's wait for {path} ended in {state!r}")
            time.sleep(_WAIT_POLL_S)
        else:
            raise RuntimeError(f"Skool's wait for {path} did not complete in time")
        sep = "&" if "?" in path else "?"
        return (self.http.get_api2(f"{path}{sep}token={token}") or {}).get("data") or {}

    def growth_overview(self, group_skool_id: str) -> dict:
        """The dashboard's last-30-days tiles: ``num_visitors``, ``num_signups``,
        ``new_mrr`` (growth overview) plus ``num_members`` (overview)."""
        base = f"/groups/{group_skool_id}"
        return {**self._analytics(f"{base}/analytics-growth-overview-v2"),
                **self._analytics(f"{base}/analytics-overview-v2")}

    def analytics_chart(self, group_skool_id: str, chart: str) -> list[dict]:
        """One dashboard chart's ``items`` (see ``ANALYTICS_CHARTS``)."""
        if chart not in self.ANALYTICS_CHARTS:
            raise ValueError(f"chart must be one of {', '.join(self.ANALYTICS_CHARTS)}")
        data = self._analytics(f"/groups/{group_skool_id}/analytics-v2?chart={chart}")
        return (data.get("chart_data") or {}).get("items") or []

    def calendar(self, community_slug: str, cal_date: int = 0) -> dict:
        """Calendar/events (raw ``pageProps``). ``cal_date`` = unix ts for a
        future month, 0 = current month."""
        q = f"/{community_slug}/calendar.json?group={community_slug}"
        if cal_date > 0:
            q = f"/{community_slug}/calendar.json?calDate={cal_date}&group={community_slug}"
        return self.http.get_next(q, community_slug)

    # -- calendar write path (docs/API.md §5.11) -------------------------------
    # Read from Skool's own page code and NOT yet run live: the limits and the
    # field set are the ones its event form enforces, nothing here is measured.
    # An event is ONE object for create and update (the whole form), so these
    # methods take that object and send it as it is. Which of its fields Skool
    # insists on, and whether an update with fewer fields keeps or resets the
    # rest, is not known (§5.11 lists it as open): nothing here guesses either.
    CALENDAR_TITLE_MAX = 30
    CALENDAR_DESCRIPTION_MAX = 300
    # Location types without an input field: Skool hands out the call code.
    CALENDAR_LOCATION_NO_INFO = (5, 6)  # 5 Skool call, 6 Skool webinar

    @staticmethod
    def check_calendar_event(event: dict) -> None:
        """Refuse an event object Skool's own form would not send (ValueError).

        Runs before the first request, so a refused event costs no call at all.
        It looks only at the fields that are present: no required set is
        invented. The refusal names the limit and never repeats the text.
        """
        if not isinstance(event, dict):
            raise ValueError("event must be a dict: Skool's event object.")
        meta = event.get("metadata", {})
        if not isinstance(meta, dict):
            raise ValueError("event['metadata'] must be a dict.")
        for key, limit in (("title", SkoolClient.CALENDAR_TITLE_MAX),
                           ("description", SkoolClient.CALENDAR_DESCRIPTION_MAX)):
            if key not in meta:
                continue
            if not isinstance(meta[key], str):
                raise ValueError(f"{key} must be text.")
            if len(meta[key]) > limit:
                raise ValueError(f"{key} has {len(meta[key])} characters; "
                                 f"Skool allows at most {limit}.")
        if "location" in meta:
            SkoolClient._check_calendar_location(meta["location"])

    @staticmethod
    def _check_calendar_location(location) -> None:
        if not isinstance(location, str):
            raise ValueError("location must be JSON text, not an object: "
                             '\'{"location_type": 4, "location_info": "…"}\'.')
        try:
            parsed = json.loads(location)
        except ValueError:
            raise ValueError("location is not valid JSON text.") from None
        if not isinstance(parsed, dict) or "location_type" not in parsed:
            raise ValueError("location must be JSON text of an object with a "
                             "location_type (and a location_info).")
        if (parsed["location_type"] in SkoolClient.CALENDAR_LOCATION_NO_INFO
                and "location_info" in parsed):
            raise ValueError("location_type 5 (Skool call) and 6 (Skool webinar) "
                             "take no location_info: Skool assigns the call code.")

    @staticmethod
    def calendar_event_path(event_id: str, from_occurrence_id: str = "") -> str:
        """``/calendar-events/{id}``, plus ``?from_occurrence_id=`` for a series.

        Without it a change or delete takes the whole event; with it, the
        occurrence and everything after it (docs/API.md §5.11). The event id goes
        through `_id_segment` (real text, not only dots: ``..`` would be folded
        away, ``/calendar-events/..`` becoming ``/``, and aim a PUT or DELETE at
        the API root). The occurrence id is a query value: empty means "not
        given", anything else must be non-blank text, and it is percent-encoded
        so it cannot add a parameter. Neither refusal repeats the value.
        """
        segment = _id_segment(event_id, "event_id")
        if not isinstance(from_occurrence_id, str) or (
                from_occurrence_id and not from_occurrence_id.strip()):
            raise ValueError("from_occurrence_id must be non-empty text when given.")
        path = f"/calendar-events/{segment}"
        if from_occurrence_id:
            path += f"?from_occurrence_id={quote(from_occurrence_id, safe='')}"
        return path

    def create_calendar_event(self, community_slug: str, event: dict) -> dict:
        """Create a calendar event: ``POST /calendar-events`` (§5.11). Raw answer.

        ``event`` is Skool's object without ``group_id`` (the slug decides it, and
        an archived community is refused up front): ``start_time``, ``end_time``,
        ``metadata`` {``title`` ≤ 30 characters, ``description`` ≤ 300,
        ``timezone``, ``reminder_disabled``, ``cover_image``, ``location``,
        ``privacy``} and ``recurrence``. ``location`` and ``privacy`` are JSON
        TEXT, not objects. Times and timezone go out exactly as passed.
        """
        self.check_calendar_event(event)
        if "group_id" in event:
            raise ValueError("event carries no group_id: the community slug decides it.")
        group_id = self.group_id_for(community_slug, for_write=True)
        return self.http.post_api2("/calendar-events", {"group_id": group_id, **event})

    def update_calendar_event(self, event_id: str, event: dict, *,
                              from_occurrence_id: str = "") -> dict:
        """Change an event: ``PUT /calendar-events/{id}`` (§5.11). Raw answer.

        ``event`` goes out unchanged, so it is the whole object Skool's form
        would send (``group_id`` included if wanted); the same limits as in
        `create_calendar_event` apply to the fields that are in it. Whether Skool
        keeps a field you leave out is not known: send the full set.
        ``from_occurrence_id`` changes that occurrence and all later ones of a
        series; empty changes the whole event. One occurrence alone
        (``updateCalendarEventOccurrenceV2``) is not built.
        """
        self.check_calendar_event(event)
        return self.http.put_api2(self.calendar_event_path(event_id, from_occurrence_id), event)

    def delete_calendar_event(self, event_id: str, *, from_occurrence_id: str = "") -> dict:
        """Delete an event: ``DELETE /calendar-events/{id}``, no body (§5.11).

        Irreversible. ``from_occurrence_id`` deletes that occurrence and all
        later ones of a series; empty deletes the whole event.
        """
        return self.http.delete_api2(self.calendar_event_path(event_id, from_occurrence_id))

    # -- small post actions (docs/API.md §5.12) --------------------------------
    # Read from Skool's own page code and NOT run live by catknows (the vote, pin,
    # category and About calls also run in the author's autopilot): paths, bodies
    # and limits are the ones its web client uses. Every method refuses a bad
    # argument BEFORE the first request and hands Skool's answer back as it is.
    # A SkoolSecurityChallenge is never caught here: the write counts as not sent.
    POST_VOTES = ("", "up")  # "" = no vote; Skool's client has no downvote
    CATEGORY_NAME_MAX = 30
    # The values the category form offers (best-1y is shown in the feed only).
    CATEGORY_SORTS = ("newest-cm", "newest", "best-1w", "best-1m")
    CATEGORY_WRITE_ROLES = (4, 3)  # 4 members and admins may post, 3 admins only

    @staticmethod
    def post_action_path(post_id: str, action: str) -> str:
        """``/posts/{id}/{action}``. The id goes through `_id_segment`: real text,
        not only dots, percent-encoded. ``action`` is one of our own constants."""
        return f"/posts/{_id_segment(post_id, 'post_id')}/{action}"

    @staticmethod
    def comment_pin_path(post_id: str, comment_id: str, *, pinned: bool = True) -> str:
        """``/posts/{id}/pin-comment?comment={commentId}`` (``unpin-comment`` if not pinned).

        A comment id is always required here: a missing one must not turn into a
        call without the ``comment`` query. Both ids go through `_id_segment`.
        """
        action = "pin-comment" if pinned else "unpin-comment"
        return (f"{SkoolClient.post_action_path(post_id, action)}"
                f"?comment={_id_segment(comment_id, 'comment_id')}")

    @staticmethod
    def category_path(category_id: str) -> str:
        """``/labels/{id}`` (Skool calls a category a label)."""
        return f"/labels/{_id_segment(category_id, 'category_id')}"

    @staticmethod
    def check_post_vote(old, new) -> None:
        """Only the two documented transitions: like (``"" -> "up"``) and take back."""
        for name, value in (("old", old), ("new", new)):
            if not isinstance(value, str) or value not in SkoolClient.POST_VOTES:
                raise ValueError(f'{name} must be "" (no vote) or "up": '
                                 "Skool's client has no downvote.")
        if old == new:
            raise ValueError("old and new are the same vote: that changes nothing, "
                             "so it is not sent.")

    @staticmethod
    def check_category_name(display_name) -> None:
        """A category name: non-blank text, at most 30 characters. Never echoes it.

        30 is the limit of Skool's category form, read from the page code; the
        server's own limit is not known, and neither is whether Skool counts
        characters the way ``len()`` does (docs/API.md §5.12).
        """
        if not isinstance(display_name, str) or not display_name.strip():
            raise ValueError("display_name must be non-blank text.")
        if len(display_name) > SkoolClient.CATEGORY_NAME_MAX:
            raise ValueError(f"display_name has {len(display_name)} characters; "
                             f"Skool allows at most {SkoolClient.CATEGORY_NAME_MAX}.")

    @staticmethod
    def check_category_options(default_sort, post_write_role) -> None:
        """The sort and the write role the category form offers, nothing else."""
        if not isinstance(default_sort, str) or default_sort not in SkoolClient.CATEGORY_SORTS:
            raise ValueError("default_sort must be one of "
                             + ", ".join(SkoolClient.CATEGORY_SORTS) + ".")
        if (isinstance(post_write_role, bool) or not isinstance(post_write_role, int)
                or post_write_role not in SkoolClient.CATEGORY_WRITE_ROLES):
            raise ValueError("post_write_role must be 4 (members and admins may post) "
                             "or 3 (admins only).")

    @staticmethod
    def check_about_description(description) -> None:
        """The About text: non-blank text. catknows' own rule: a blank text is refused.

        What Skool does with a blank About text is not verified against Skool.
        """
        if not isinstance(description, str) or not description.strip():
            raise ValueError("description must be non-blank text.")

    @staticmethod
    def check_flag(value, name: str) -> None:
        """A real boolean: a text "false" is truthy and must not flip the direction."""
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be true or false.")

    def vote_post(self, post_id: str, old: str, new: str) -> dict:
        """Like a post or take the like back: ``PUT /posts/{id}/vote`` (§5.12).

        Body ``{"old", "new"}``; only ``""`` and ``"up"`` are allowed, so a like
        is ``("", "up")`` and taking it back ``("up", "")``. No downvote. What
        Skool does with an ``old`` that is not the stored vote is not known.
        """
        self.check_post_vote(old, new)
        path = self.post_action_path(post_id, "vote")
        return self.http.put_api2(path, {"old": old, "new": new})

    def pin_post(self, post_id: str, *, pinned: bool = True) -> dict:
        """Pin a post to the feed, or unpin it: ``POST /posts/{id}/pin|unpin`` (§5.12).

        Skool's own client sends no body; this sends ``{}`` like the autopilot,
        which once got HTTP 400 for it (cause open). Group admins only in the web
        client.
        """
        self.check_flag(pinned, "pinned")
        path = self.post_action_path(post_id, "pin" if pinned else "unpin")
        return self.http.post_api2(path, {})

    def create_category(self, community_slug: str, display_name: str, *,
                        default_sort: str, post_write_role: int) -> dict:
        """Create a feed category: ``POST /labels`` (§5.12). Raw answer (the new label).

        Body ``{group_id, metadata: {display_name, default_sort, post_write_role}}``;
        the slug decides the group (an archived community is refused up front).
        No defaults of our own: the caller names the sort (``newest-cm``,
        ``newest``, ``best-1w``, ``best-1m``) and the role (4 members and admins,
        3 admins only). The category form stops at 10 categories (the server's
        limit is not known); catknows does not count them.
        """
        self.check_category_name(display_name)
        self.check_category_options(default_sort, post_write_role)
        group_id = self.group_id_for(community_slug, for_write=True)
        return self.http.post_api2("/labels", {
            "group_id": group_id,
            "metadata": {"display_name": display_name, "default_sort": default_sort,
                         "post_write_role": post_write_role},
        })

    def rename_category(self, category_id: str, display_name: str) -> dict:
        """Rename a feed category: ``PUT /labels/{id}`` with ``{display_name}`` (§5.12).

        Only the name is sent. Whether Skool then keeps the category's sort and
        write role is not verified. ``category_id`` is the ``category_id`` of a
        ``list_posts`` row. Deleting a category is not built.
        """
        self.check_category_name(display_name)
        return self.http.put_api2(self.category_path(category_id),
                                  {"display_name": display_name})

    def update_about_description(self, community_slug: str, description: str) -> dict:
        """Replace the About page's description (§5.12).

        ``POST /groups/{gid}/update-landing-page-description`` with
        ``{"description": text}``, sent unchanged: the format Skool wants (plain
        text or ``[v2]`` TipTap) is not documented. The slug decides the group
        (an archived community is refused). Public text, so the caller shows it
        to a human first; a blank text is refused.
        """
        self.check_about_description(description)
        group_id = self.group_id_for(community_slug, for_write=True)
        path = f"/groups/{_id_segment(group_id, 'group_id')}/update-landing-page-description"
        return self.http.post_api2(path, {"description": description})

    def set_post_comments(self, post_id: str, *, enabled: bool) -> dict:
        """Turn comments off or on for one post (§5.12).

        ``POST /posts/{id}/disable-comments`` or ``enable-comments``, no body.
        """
        self.check_flag(enabled, "enabled")
        path = self.post_action_path(post_id, "enable-comments" if enabled else "disable-comments")
        return self.http.post_api2(path, None)

    def pin_comment(self, post_id: str, comment_id: str, *, pinned: bool = True) -> dict:
        """Pin a comment under its post, or unpin it (§5.12).

        ``POST /posts/{post_id}/pin-comment?comment={comment_id}`` (or
        ``unpin-comment``), no body. ``comment_id`` is from ``get_post_comments``.
        The web client allows one pinned comment; what the server does with a
        second is not verified.
        """
        self.check_flag(pinned, "pinned")
        path = self.comment_pin_path(post_id, comment_id, pinned=pinned)
        return self.http.post_api2(path, None)

    def follow_post(self, post_id: str, *, follow: bool = True) -> dict:
        """Follow a post (the bell) or stop following it (§5.12).

        ``POST /posts/{id}/follow`` or ``unfollow``, no body.
        """
        self.check_flag(follow, "follow")
        path = self.post_action_path(post_id, "follow" if follow else "unfollow")
        return self.http.post_api2(path, None)

    # -- chat read marks, member search, file download link (docs/API.md §5.13) --
    # Read from Skool's own page code and NOT run live by catknows. The two marks
    # are writes (they change the unread state of the account): `post_api2`, so
    # they are spaced and logged. The search and the link request are POSTs that
    # only read: `post_api2_read`, which writes no audit line and does not wait.
    # Every method refuses a bad argument BEFORE the first request, and a
    # SkoolSecurityChallenge is never caught here.

    @staticmethod
    def chat_read_path(channel_id: str, last: str | None = None) -> str:
        """``/channels/{id}/read``, with ``?last={last}`` when ``last`` is given.

        Both go through `_id_segment`. ``last`` is sent as the text it is: the
        page code passes the id of the chat message that just came into view, and
        no ``last`` at all for "the whole chat". ``None`` means not given; an
        empty text is refused rather than turned into "not given".
        """
        path = f"/channels/{_id_segment(channel_id, 'channel_id')}/read"
        if last is None:
            return path
        return f"{path}?last={_id_segment(last, 'last')}"

    @staticmethod
    def file_download_path(file_id: str, expire: int | None = None) -> str:
        """``/files/{id}/download-url``, with ``?expire={n}`` when ``expire`` is given.

        Skool's own name for ``expire`` is ``expireSecs``, so seconds; the unit
        and the allowed range were not run. Only what cannot be a number is
        refused here (not a whole number, below 1, a boolean).
        """
        path = f"/files/{_id_segment(file_id, 'file_id')}/download-url"
        if expire is None:
            return path
        if isinstance(expire, bool) or not isinstance(expire, int) or expire < 1:
            raise ValueError("expire must be a whole number of 1 or more, or left out.")
        return f"{path}?expire={expire}"

    def mark_chat_read(self, channel_id: str, last: str | None = None) -> dict:
        """Mark one chat as read for this account: ``POST /channels/{id}/read`` (§5.13).

        No body. ``last`` (query) is the id of a chat message, as the page code
        passes it; without it the whole chat is meant. What the server does with
        ``last`` was not run, so it is sent exactly as given. Returns Skool's
        answer as it is.
        """
        return self.http.post_api2(self.chat_read_path(channel_id, last), None)

    def mark_all_chats_read(self) -> dict:
        """Mark EVERY chat of this account as read: ``POST /self/chat-channels/read`` (§5.13).

        No query, no body, no way to name a community or a channel. Returns
        Skool's answer as it is.
        """
        return self.http.post_api2("/self/chat-channels/read", None)

    # The one member-search request catknows sends. Skool's page code (build
    # 1794436674711, `static_chunks_8402`) asks for 7 hits on desktop (5 on a phone)
    # and never sends a cursor. Effect of a cursor or of another limit is not
    # documented ("contracts, not guesses"), and paging a search would be a detour
    # around the member-list limit (LEGAL.md boundary 4: a non-owner is served the
    # first page only). So the body is always exactly {query, group_id, limit: 7}.
    # That is sticking to the documented request, not a disguise of the client.
    SEARCH_USERS_LIMIT = 7

    @staticmethod
    def check_search_limit(limit) -> int:
        """A result limit for the tool: a whole number of 1 or more (not a boolean)."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a whole number of 1 or more.")
        return limit

    def search_members(self, community_slug: str, query: str) -> dict:
        """The @mention member search of one community: ``POST /search/users`` (§5.13).

        Reads only, although it is a POST (`post_api2_read`). The body is always
        exactly ``{query, group_id, limit: 7}``: Skool's own client sends no
        cursor and no other limit, and catknows sends only that documented form
        (`SEARCH_USERS_LIMIT`), so there is no ``limit`` or ``cursor`` argument.
        A blank query is refused: this is a name search, not a way to list a
        community. Returns Skool's answer (an object, ``users[]`` in the page
        code) as it is; flatten it with ``normalize.search_user``. Archived
        communities stay searchable.
        """
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be non-blank text.")
        body = {"query": query, "group_id": self.group_id_for(community_slug),
                "limit": self.SEARCH_USERS_LIMIT}
        answer = self.http.post_api2_read("/search/users", body)
        if not isinstance(answer, dict):
            raise SkoolHTTPError(
                "Skool answered the member search with something that is not an object "
                "(the answer is not shown).")
        return answer

    def file_download_url(self, file_id: str, expire: int | None = None) -> str:
        """A download link for one uploaded file: ``POST /files/{id}/download-url`` (§5.13).

        Reads only (`post_api2_read`), no body. Returns the link as text. The page
        code uses the answer as the link itself, so ONE text starting with
        ``https://`` and holding no white space is the only thing accepted (the
        "https only" part is catknows' own safeguard, not checked against Skool).
        Anything else (an object, a list, empty, ``http://``, a line break, an HTML page such
        as a WAF challenge or login page, a progress word like ``in-progress``) is an
        error that does NOT show the answer, because it may hold the access link.
        The link must not be logged and is not: nothing here writes it anywhere.
        This is for files (documents, images). Video download is an admin right in
        Skool and is not offered.
        """
        answer = self.http.post_api2_read(self.file_download_path(file_id, expire), None)
        link = answer.strip() if isinstance(answer, str) else ""
        if (not link.startswith("https://") or len(link) == len("https://")
                or any(ch.isspace() for ch in link)):
            raise SkoolHTTPError(
                "Skool's answer to the download-link request is not a link (one https:// text "
                "without white space was expected; the answer is not shown: it may hold an "
                "access link).")
        return link

    def classroom(self, community_slug: str) -> dict:
        """Classroom / courses (raw ``pageProps``)."""
        return self.http.get_next(
            f"/{community_slug}/classroom.json?group={community_slug}", community_slug
        )

    # -- classroom write path (docs/API.md §7) ---------------------------------
    # Course, folder and page are ONE object type ("unit") in a tree:
    # unit_type "course" (root), "set" (folder), "module" (page). All verbs go
    # through api2 /courses. Verified live 2026-08-15 on a private community.

    def course_tree(self, course_id: str, community_slug: str = "") -> dict:
        """One course's full tree: ``{course, children:[{course, children}]}``.

        Two doors lead here and they do NOT grant the same thing (measured
        2026-08-19). ``api2 /courses/{id}`` needs membership: an account
        outside the community gets 401 ``action not permitted`` on EVERY
        course, open ones included. The classroom page reads it the way the
        website does and shows whatever the community publishes — which is how
        a non-member can open a course in the browser at all.

        So: api2 first (it is the only door for our own drafts and the write
        path), the page as the fallback. ``community_slug`` enables that
        fallback; without it a non-member just gets the 401.
        """
        try:
            return self.http.get_api2(f"/courses/{course_id}")
        except SkoolHTTPError as e:
            if not community_slug or e.status not in (401, 403):
                raise
            tree = self._course_tree_via_page(community_slug, course_id)
            if tree is None:
                raise
            return tree

    def _course_tree_via_page(self, community_slug: str, course_id: str) -> dict | None:
        """The course as the website reads it, or None if it isn't visible there.

        The page wants the course's short slug (``name``), not its id, so this
        resolves the id through the classroom list first. Opening a course
        without a module answers a redirect stub carrying the first lesson's
        ``md=`` — the browser follows it, and so do we.
        """
        name = ""
        for c in ((self.classroom(community_slug).get("pageProps") or {})
                  .get("allCourses") or []):
            if c.get("id") == course_id:
                name = c.get("name") or ""
                break
        if not name:
            return None

        base = f"/{community_slug}/classroom/{name}.json?group={community_slug}"
        data = self.http.get_next(base, community_slug)
        pp = data.get("pageProps") or {}
        if not pp.get("course"):
            target = str(pp.get("__N_REDIRECT") or "")
            md = target.partition("md=")[2].split("&", 1)[0]
            if not md:
                return None
            data = self.http.get_next(f"{base}&md={md}", community_slug)
            pp = data.get("pageProps") or {}
        return pp.get("course") or None

    def classroom_courses(self, community_slug: str) -> dict:
        """The api2 course list: ``{courses: [unit], num_all_courses}``.

        Same courses as `classroom()` but snake_case and with the unit fields
        (``id``, ``unit_type``, ``state``) the write path needs.
        """
        gid = self.group_id_for(community_slug)
        return self.http.get_api2(f"/groups/{gid}/courses")

    def create_course(
        self,
        community_slug: str,
        title: str,
        *,
        desc: str = "",
        privacy: int = 0,
        min_access_level: int = 0,
        min_tier: int = 0,
        draft: bool = True,
    ) -> dict:
        """Create a classroom course (a top-level tile). Returns the created unit.

        ``desc`` is PLAIN TEXT here (unlike page bodies). ``privacy``: 0 open,
        1 level-locked (with ``min_access_level``), 2 private/tier
        (``min_tier``). ``draft=True`` creates it invisible to non-admins
        (state 1) — publish with `set_course_state`.
        """
        metadata: dict = {"title": title, "desc": desc, "privacy": privacy,
                          "lock_free_trial": 0}
        # Skool validates these when present: min_access_level must be >= 1
        # ("invalid course min access level value: 0") — omit the unset ones.
        if min_access_level > 0:
            metadata["min_access_level"] = min_access_level
        if min_tier > 0:
            metadata["min_tier"] = min_tier
        return self.http.post_api2("/courses", {
            "group_id": self.group_id_for(community_slug, for_write=True),
            "unit_type": "course",
            "state": 1 if draft else 2,
            "metadata": metadata,
        })

    def create_course_item(
        self,
        course_id: str,
        title: str,
        *,
        parent_id: str = "",
        folder: bool = False,
        content: str = "",
        video_link: str = "",
        draft: bool = False,
    ) -> dict:
        """Create a folder (``folder=True``) or page inside a course.

        ``parent_id`` defaults to the course itself; pass a folder's id to nest
        the page there (pages inside pages render as invisible — don't).
        ``content`` is the page body: plain text is wrapped into Skool's TipTap
        format automatically, a string starting with ``[v2]`` is passed as-is.
        ``video_link`` embeds an external video (YouTube/Loom/Vimeo URL).
        """
        tree = self.course_tree(course_id)  # validates the id, gives group/user
        course = tree.get("course") or {}
        metadata: dict = {"title": title}
        if not folder:
            metadata["resources"] = "[]"
            metadata["desc"] = content if content.startswith("[v2]") else tiptap(content)
            if video_link:
                metadata["video_link"] = video_link
        return self.http.post_api2("/courses", {
            "group_id": course.get("groupId") or course.get("group_id"),
            "unit_type": "set" if folder else "module",
            "parent_id": parent_id or course_id,
            "root_id": course_id,
            "state": 1 if draft else 2,
            "metadata": metadata,
        })

    def update_course_item(self, unit_id: str, fields: dict) -> dict:
        """Update a unit via the flat PUT body the Skool UI sends.

        ⚠ Skool merges SOME omitted fields and resets OTHERS (verified live:
        a PUT with only ``title`` silently reset a course's ``privacy`` to 0).
        For courses this method therefore reads the current metadata first and
        sends the full settings set with your ``fields`` merged in. For
        folders/pages (whose metadata can't be read standalone) the fields go
        out verbatim — send the full set: pages want ``{title, desc,
        transcript, video_id}``, what you omit may be dropped.

        Page ``desc`` is TipTap ``[v2]`` JSON — plain text is wrapped for you.
        A COURSE ``desc`` is plain text (§7.3) and goes out unchanged: wrapping
        it shows the raw JSON under the classroom tile.
        """
        fields = dict(fields)
        try:
            cur = (self.course_tree(unit_id).get("course") or {}).get("metadata") or {}
        except SkoolSecurityChallenge:
            raise  # Skool's security question is no "not a root unit": no blind PUT
        except SkoolHTTPError:
            cur = None  # not a root unit -> flat update, caller sends full set
        if cur is None and "desc" in fields and not str(fields["desc"]).startswith("[v2]"):
            fields["desc"] = tiptap(str(fields["desc"]))
        if cur is not None:
            base = {k: cur[k] for k in ("title", "desc", "privacy", "min_access_level",
                                        "min_tier", "lock_free_trial", "cover_image",
                                        "cover_image_file", "is_afl_comp_eligible")
                    if k in cur}
            base.update(fields)
            # Type asymmetry, verified live: the POST metadata takes these as
            # ints, the PUT body rejects ints ("invalid request body") — bool them.
            for flag in ("lock_free_trial", "is_afl_comp_eligible"):
                if flag in base:
                    base[flag] = bool(base[flag])
            fields = base
        return self.http.put_api2(f"/courses/{unit_id}", fields)

    def set_course_state(self, course_id: str, published: bool) -> dict:
        """Publish (visible to members) or unpublish (draft) a course."""
        state = "published" if published else "draft"
        return self.http.put_api2(f"/courses/{course_id}/state?state={state}", {})

    def move_course_item(self, unit_id: str, position: int) -> dict:
        """Reorder a unit among its siblings; ``position`` is the 0-based target.

        Course tiles reorder within the classroom, pages within their parent.
        This does NOT re-parent (Skool ignores a PUT ``parent_id`` too). To move
        a page into a folder or out of one, use `move_course_to_folder` (read
        from Skool's page code, not run live yet); until that has been tried,
        recreating the page in the target folder and deleting the old one is
        the proven way.
        """
        return self.http.post_api2(f"/courses/{unit_id}/move2?dst={position}", {})

    def delete_course_item(self, unit_id: str, *, client_id: str = "") -> None:
        """Delete a unit. ⚠ Deleting a FOLDER does not delete its pages — they
        are lifted to the course root (verified live). Deleting a COURSE takes
        everything inside it with it.

        Folders and pages delete with no extra ceremony. Deleting a whole
        COURSE requires ``client_id``: a 32-hex id that has passed Skool's
        email-code verification (``/auth/email-verify-init`` → code →
        ``/auth/email-verify``). The browser's own id stays verified across
        sessions — capture it once from the web app and reuse it (§7.6).
        """
        q = f"?client_id={client_id}" if client_id else ""
        self.http.delete_api2(f"/courses/{unit_id}{q}")

    # -- course settings: drip, folder, share key, import (docs/API.md §7.8) ----
    # Read from Skool's own page code and NOT run live by catknows: paths, query
    # and body are the ones its web client sends. Every method refuses a bad
    # argument BEFORE the first request and hands Skool's answer back as it is.
    # A SkoolSecurityChallenge is never caught here: the write counts as not sent.
    # The share key is a credential (a query value on the import, the answer of
    # the key request), and so is the client id of a course delete. Nothing in this
    # class puts one in a message of its own; the HTTP layer keeps them out of the
    # audit line, the error texts and the exception chain (docs/API.md §7.8:
    # `audit.redact`, `audit.secret_answer`, `audit.carries_secret`). What it does
    # not cover: the key request's answer, which is returned to the caller on purpose.
    COURSE_DRIP_DAYS_MAX = 999   # the drip form clamps the field to 1..999
    CLONE_CODE_LENGTH = 64       # the import form refuses any other length
    COURSE_ID_LENGTH = 32        # ... and takes the course id from the first 32

    @staticmethod
    def course_action_path(course_id: str, action: str) -> str:
        """``/courses/{id}/{action}``. The id goes through `_id_segment`: real text,
        not only dots, percent-encoded. ``action`` is one of our own constants."""
        return f"/courses/{_id_segment(course_id, 'course_id')}/{action}"

    @staticmethod
    def course_parent_path(course_id: str, parent_id: str) -> str:
        """``/courses/{id}/update-parent?parent={parentId}``.

        Both ids go through `_id_segment` (the parent is a query value, so it is
        percent-encoded and cannot add a parameter). A unit cannot be its own parent.
        """
        path = SkoolClient.course_action_path(course_id, "update-parent")
        parent = _id_segment(parent_id, "parent_id")
        if course_id == parent_id:
            raise ValueError("course_id and parent_id are the same id: a unit cannot sit "
                             "inside itself.")
        return f"{path}?parent={parent}"

    @staticmethod
    def check_course_drip(enabled, days) -> int:
        """The days to send with a drip switch, after the checks of Skool's own form.

        ``enabled`` must be a real boolean. ``days`` a whole number (not a boolean):
        1 to 999 when drip is on (the form clamps to that, and its Save button stays
        off without days), 0 to 999 when it is off (the form sends the days left in
        its field, or 0). Whether the server enforces the same limits is not known.
        """
        SkoolClient.check_flag(enabled, "enabled")
        low, high = (1 if enabled else 0), SkoolClient.COURSE_DRIP_DAYS_MAX
        if isinstance(days, bool) or not isinstance(days, int) or not low <= days <= high:
            raise ValueError(f"days must be a whole number from {low} to {high}"
                             + (" (drip is on)." if enabled else " (0 when drip is off)."))
        return days

    @staticmethod
    def check_import_course(course_id, clone_code) -> str:
        """The share key for an import, after the checks of Skool's own form; returns it lowercased.

        The form refuses a key that is not exactly 64 characters, lowercases it,
        and takes the course id from its first 32 characters, so that is the only
        pair it ever sends. The refusals say how long, never what was passed.
        """
        if (not isinstance(clone_code, str)
                or len(clone_code) != SkoolClient.CLONE_CODE_LENGTH):
            raise ValueError(f"clone_code must be exactly {SkoolClient.CLONE_CODE_LENGTH} "
                             "characters, the share key as Skool shows it.")
        code = clone_code.lower()
        _id_segment(course_id, "course_id")
        if course_id != code[:SkoolClient.COURSE_ID_LENGTH]:
            raise ValueError(f"course_id must be the first {SkoolClient.COURSE_ID_LENGTH} "
                             "characters of clone_code: Skool's own form takes the course id "
                             "from the key.")
        return code

    def set_course_drip(self, course_id: str, enabled: bool, days: int = 0) -> dict:
        """Switch drip (timed release) on or off: ``POST /courses/{id}/update-drip`` (§7.8).

        Body ``{"enabled", "days"}``. Skool's web client offers it in a page's
        menu, so ``course_id`` is a page id; whether a course or folder id works
        is not known. ``days``: see `check_course_drip` (1 to 999 when ``enabled``,
        0 by default when not). Returns Skool's answer as it is.
        """
        days = self.check_course_drip(enabled, days)
        path = self.course_action_path(course_id, "update-drip")
        return self.http.post_api2(path, {"enabled": enabled, "days": days})

    def move_course_to_folder(self, course_id: str, parent_id: str) -> dict:
        """Put a page into a folder, or out of it: ``POST /courses/{id}/update-parent?parent=`` (§7.8).

        No body. ``parent_id`` is a folder's id, or the id of the course itself
        (what the page's "None" sends: the page's ``root_id``). It sets the parent
        and nothing else: the place among the new siblings is not set (reorder
        with `move_course_item`). Skool's web client moves pages only. Returns
        Skool's answer as it is.
        """
        return self.http.post_api2(self.course_parent_path(course_id, parent_id), None)

    def create_course_share_key(self, course_id: str) -> dict:
        """Ask for a course's share key: ``POST /courses/{id}/clone-code``, no body (§7.8).

        The answer is an object with ``code`` (what the page reads), a 64-character
        key. **It is a credential**: whoever holds it can duplicate the whole
        course into any community. Returned to the caller as it is, which is the
        point; the audit line keeps only a hash of it, and an error about this
        request shows no piece of the answer and has no chain (docs/API.md §7.8).
        """
        return self.http.post_api2(self.course_action_path(course_id, "clone-code"), None)

    def import_course(self, course_id: str, community_slug: str, clone_code: str) -> dict:
        """Import a course into a community with its share key: ``POST /courses/{id}/import`` (§7.8).

        Query ``group`` (the community's group ID, from the slug: an archived
        community is refused up front) and ``clone_code`` (the key, lowercased as
        Skool's form does); no body. ``course_id`` is the SOURCE course, the first 32
        characters of the key; the copy goes into the community. Skool makes the copy
        on its side, so the answer says nothing about the finished course. The key
        is a credential: it is sent on the wire, and hidden by its name
        (``clone_code=``) in the audit line and in every error text, and an error
        has no exception chain (docs/API.md §7.8). Returns Skool's answer as it is.
        """
        code = self.check_import_course(course_id, clone_code)
        group_id = self.group_id_for(community_slug, for_write=True)
        path = (f"{self.course_action_path(course_id, 'import')}"
                f"?group={_id_segment(group_id, 'group_id')}&clone_code={quote(code, safe='')}")
        return self.http.post_api2(path, None)

    # -- community rules and level names (docs/API.md §1.11) -------------------
    # Plain GETs, no query. Read from the page code, answer shape not run live, so
    # both return the answer as it is. Archived communities stay readable.

    def group_rules(self, community_slug: str) -> dict:
        """The community's rules: ``GET /groups/{gid}/rules`` (§1.11). Raw answer."""
        gid = _id_segment(self.group_id_for(community_slug), "group_id")
        return self.http.get_api2(f"/groups/{gid}/rules")

    def group_levels(self, community_slug: str) -> dict:
        """The community's level names: ``GET /groups/{gid}/levels`` (§1.11). Raw answer."""
        gid = _id_segment(self.group_id_for(community_slug), "group_id")
        return self.http.get_api2(f"/groups/{gid}/levels")

    # -- notifications ---------------------------------------------------------

    # The website's filter tabs; anything else Skool answers 400 "invalid category".
    NOTIFICATION_CATEGORIES = ("mentions", "comments", "following", "requests",
                               "new_posts", "recordings")

    def notifications(self, *, limit: int = 30, category: str = "",
                      community_slug: str = "", cursor: str = "") -> dict:
        """Your notifications, newest first (docs/API.md §1.7) — the bell's list.

        The same GET the website sends when the bell opens. It does not mark
        anything read: Skool does that with separate POSTs (``/messages``,
        ``/messages/{id}/read``) that this never sends. ``limit`` above 30 is
        refused by Skool ("invalid limit"); page on with the returned
        ``cursor``. ``community_slug`` narrows to one community.
        Returns ``{messages, has_more, cursor, type}`` raw.
        """
        if not 1 <= limit <= 30:
            raise ValueError("limit must be 1..30 (Skool refuses more); page with cursor")
        if category and category not in self.NOTIFICATION_CATEGORIES:
            raise ValueError(f"category must be one of {', '.join(self.NOTIFICATION_CATEGORIES)}")
        q = f"/self/notifications?limit={limit}"
        if community_slug:
            q += f"&type=group&group={self.group_id_for(community_slug)}"
        else:
            q += "&type=all"
        if category:
            q += f"&category={category}"
        if cursor:
            q += f"&cursor={quote(cursor, safe='')}"
        return self.http.get_api2(q)

    # -- chat ------------------------------------------------------------------

    def chat_channels(self, *, offset: int = 0, limit: int = 30,
                      unread_only: bool = False) -> dict:
        """Your DM channels (docs/API.md §1.6) — the channel ids `send_dm` needs.

        ``limit`` above 30 is refused by Skool ("invalid limit"). With
        ``unread_only`` Skool lists only channels with unread messages; when
        there are none it answers ``{"channels": null}``, which comes back here
        as ``"channels": []`` so callers never see ``None``. The raw object is
        returned otherwise.
        """
        flag = "true" if unread_only else "false"
        data = self.http.get_api2(
            f"/self/chat-channels?offset={offset}&limit={limit}&last=true&unread-only={flag}"
        )
        if data.get("channels") is None:
            data = {**data, "channels": []}
        return data

    def chat_messages(self, channel_id: str, *, count: int = 30) -> dict:
        """Messages of one DM channel, newest last (docs/API.md §1.6).

        Two different shapes hide behind one endpoint, and only using both
        gets you a full history:

        * The opening read takes ``before=N`` — NOT a timestamp and NOT a
          message id, but "how many messages back from newest". It is capped
          at 50; anything larger is refused with ``invalid before``. No
          ``limit`` goes with it: Skool's own client sends none, and ``before``
          alone gives the same answer.
        * Going further back needs the ``msg={id}`` cursor: it returns a
          window *around* that message (``before``/``after`` then count
          outwards from it). Walking it from the oldest message of the
          previous window is the only way past those first 50.

        Skool includes the boundary message in each window, so pages overlap;
        results are deduped by id. Returns the raw response shape with every
        page merged into ``messages`` (oldest first).
        """
        n = min(50, max(1, count))
        data = self.http.get_api2(
            f"/channels/{channel_id}/messages?before={n}"
        )
        msgs = list(data.get("messages") or [])
        seen = {m.get("id") for m in msgs}

        # With unread messages the opening window ends at *your* last read
        # position, not at the newest message (``has_more_after`` is true).
        # Walk forward first, or exactly the unread messages go missing.
        fwd = data
        while fwd.get("has_more_after") and msgs:
            newest = max(msgs, key=lambda m: m.get("created_at") or "").get("id")
            time.sleep(_INTER_PAGE_DELAY_S)
            fwd = self.http.get_api2(
                f"/channels/{channel_id}/messages?before=0&after=50&msg={newest}"
            )
            fresh = [m for m in fwd.get("messages") or [] if m.get("id") not in seen]
            if not fresh:  # cursor stopped moving
                break
            seen.update(m.get("id") for m in fresh)
            msgs += fresh
        msgs.sort(key=lambda m: m.get("created_at") or "")

        # Walk the cursor backwards while more history exists and the caller
        # still wants more than we hold.
        while len(msgs) < count and data.get("has_more_before") and msgs:
            oldest = msgs[0].get("id")
            if not oldest:
                break
            data = self.http.get_api2(
                f"/channels/{channel_id}/messages?before=50&after=0&msg={oldest}"
            )
            fresh = [m for m in data.get("messages") or [] if m.get("id") not in seen]
            if not fresh:  # cursor stopped moving — end of the history
                break
            seen.update(m.get("id") for m in fresh)
            msgs = fresh + msgs
            time.sleep(_INTER_PAGE_DELAY_S)

        msgs.sort(key=lambda m: m.get("created_at") or "")
        return {**data, "messages": msgs[-count:] if count < len(msgs) else msgs}

    # -- your own account ------------------------------------------------------

    def self_user(self) -> dict:
        """Your own user object (docs/API.md §4). Only bare ``/self`` works."""
        return self.http.get_api2("/self")

    def self_groups(self, *, all_pages: bool = True) -> list[dict]:
        """Every community you're in, with your role (docs/API.md §4).

        Pages 30 at a time; ``has_more`` says when to stop. Roles are absent
        for communities you own — `normalize.my_community` resolves that
        against your own user id.
        """
        out: list[dict] = []
        offset = 0
        while True:
            if offset:
                time.sleep(_INTER_PAGE_DELAY_S)
            data = self.http.get_api2(
                f"/self/groups?offset={offset}&limit=30&prefs=false&members=true"
            )
            page = data.get("groups") or []
            out.extend(page)
            # Trust has_more, but stop on a short/empty page too: a missing
            # flag must not turn into an endless loop (the 32-post bug).
            if not data.get("has_more") or not page:
                return out
            offset += len(page)

    # -- writing (docs/API.md §5) ----------------------------------------------
    # These act as YOU, visible to real members. Test in a private community
    # first; notify_members emails everyone in the group.

    def upload_file(self, community: str, path: str) -> dict:
        """Upload a local file and return its ``{"id", "file_name", ...}`` (§5.3).

        Two steps: register the file to get a presigned S3 URL, then PUT the
        bytes there. The returned id goes into ``attachments`` on `create_post`,
        `create_comment` or `send_dm` — the file must exist before the message
        referencing it.

        ``community`` is a slug, or the group UUID directly: a DM has no slug,
        but its channel carries a ``group_id``, and `owner_id` wants the UUID
        either way. Passing the UUID also skips the posts fetch a slug needs.

        Content type is guessed from the extension; unknown types fall back to
        ``application/octet-stream``, which Skool accepts.
        """
        data = pathlib.Path(path).read_bytes()
        name = pathlib.Path(path).name
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        # A 32-char hex string is already a group UUID; anything else is a slug.
        is_uuid = len(community) == 32 and all(c in "0123456789abcdef" for c in community)
        reg = self.http.post_api2(
            "/files",
            {
                "file_name": name,
                "content_type": ctype,
                "content_length": len(data),
                "content_disposition": "",
                "ref": "",
                "owner_id": community if is_uuid else self.group_id_for(community, for_write=True),
                "large_thumbnail": False,
            },
        )
        file_obj = reg.get("file") or {}
        write_url = reg.get("write_url") or ""
        if not file_obj.get("id"):
            raise SkoolHTTPError(f"Skool returned no file id for {name}: {str(reg)[:200]}")
        # Empty write_url = external file (a GIF registered by URL) — nothing to
        # upload. A local file always gets one; no URL here means the register
        # call didn't take, and posting the id would attach an empty file.
        if not write_url:
            raise SkoolHTTPError(f"No upload URL returned for {name} — file not stored.")
        self.http.put_bytes(
            write_url, data, {"Content-Type": ctype, "x-amz-acl": "public-read"}
        )
        return file_obj

    def create_poll(self, community: str, options: list[str]) -> dict:
        """Create a poll object (§5.5). Returns ``{"poll_id": "..."}``.

        A poll exists separately from the post; put the returned ``poll_id``
        into ``poll`` on `create_post`. Skool requires 2–10 non-empty options.
        ``community`` is a slug or the group UUID (same as `upload_file`).
        """
        opts = [o.strip() for o in options if o.strip()]
        if not 2 <= len(opts) <= 10:
            raise ValueError(f"A poll needs 2–10 options, got {len(opts)}.")
        is_uuid = len(community) == 32 and all(c in "0123456789abcdef" for c in community)
        return self.http.post_api2(
            "/polls",
            {
                "group_id": community if is_uuid else self.group_id_for(community, for_write=True),
                "options": opts,
            },
        )

    def create_post(
        self,
        community_slug: str,
        title: str,
        content: str,
        *,
        labels: str = "",
        video_links: str = "",
        attachments: str = "",
        poll: str = "",
        notify_members: bool = False,
    ) -> dict:
        """Create a normal feed post (§5.1). Returns the created post object.

        `labels` is a category id, `video_links` a YouTube/Loom/Vimeo URL —
        both optional. `attachments` is a comma-joined list of file ids from
        `upload_file`, `poll` a poll id from `create_poll`. `notify_members=True`
        is Skool's email broadcast: it emails every member and is subject to
        the group's cooldown.
        """
        metadata: dict = {"title": title, "content": content, "action": 0}
        if labels:
            metadata["labels"] = labels
        if video_links:
            metadata["video_links"] = video_links
        if attachments:
            metadata["attachments"] = attachments
        if poll:
            metadata["poll"] = poll
        query = "?notify=members&follow=true" if notify_members else "?follow=true"
        return self.http.post_api2(
            f"/posts{query}",
            {
                "post_type": "generic",
                "group_id": self.group_id_for(community_slug, for_write=True),
                "metadata": metadata,
            },
        )

    def create_comment(
        self,
        community_slug: str,
        post_id: str,
        content: str,
        *,
        parent_comment_id: str = "",
        parent_is_top_level: bool = False,
        attachments: str = "",
    ) -> dict:
        """Comment on a post, or reply to a comment (§5.8).

        A comment IS a post with ``post_type: "comment"`` — same endpoint as
        `create_post`. ``root_id`` is always the post; ``parent_id`` is the
        top-level comment being replied to, or the post itself for a top-level
        comment. A reply to a *reply* is re-hung under its top-level comment
        with an @-mention first, the way Skool's own UI does it — see
        :meth:`reply_target`. `attachments` is a comma-joined list of file ids
        from `upload_file`. Returns the created comment object.

        Pass ``parent_is_top_level=True`` only when you know for sure that
        ``parent_comment_id`` is a top-level comment: the lookup in
        :meth:`reply_target` is skipped and the reply hangs under that comment
        as given. The hint is believed, not checked (a wrong one gets the reply
        shown as "Comment was deleted"); without it, or with an empty
        ``parent_comment_id`` or the post's own id, everything works as before.
        """
        if parent_is_top_level is True and parent_comment_id and parent_comment_id != post_id:
            target = {"parent_id": parent_comment_id, "flattened": False, "mention": ""}
        else:
            target = self.reply_target(community_slug, post_id, parent_comment_id)
        metadata: dict = {"title": "", "content": with_mention(target["mention"], content)}
        if attachments:
            metadata["attachments"] = attachments
        return self.http.post_api2(
            "/posts?follow=false",
            {
                "post_type": "comment",
                "group_id": self.group_id_for(community_slug, for_write=True),
                "root_id": post_id,
                "parent_id": target["parent_id"],
                "metadata": metadata,
            },
        )

    def reply_target(self, community_slug: str, post_id: str,
                     parent_comment_id: str) -> dict:
        """Where a reply may hang: ``{parent_id, flattened, mention}``.

        Skool threads are two levels deep — comments, and one flat column of
        replies under each. A comment whose ``parent_id`` is itself a reply
        gets created, counted and indexed, then shows as "Comment was deleted"
        (Dan's write test 2026-09-15: all 5 replies under top-level comments
        survived, both replies under replies died; the same texts posted by
        hand under the top-level comment stayed). Skool's own UI never sends
        that: replying to a reply posts under the top-level comment and opens
        with an @-mention of the person. We do the same. ``mention`` is the
        prefix to put before the text ("" when nothing was re-hung).

        The lookup behind this (``GET /posts/{id}``) is catknows' own rule, not
        a call of Skool's web client. This method does it for every reply; a
        caller that knows the target is a top-level comment skips it through
        ``create_comment(..., parent_is_top_level=True)``.
        """
        if not parent_comment_id or parent_comment_id == post_id:
            return {"parent_id": post_id, "flattened": False, "mention": ""}
        target = self.post_by_id(parent_comment_id) or {}
        top = target.get("parent_id") or post_id
        if top == post_id:  # already a top-level comment
            return {"parent_id": parent_comment_id, "flattened": False, "mention": ""}
        tree = self.comments(post_id, self.group_id_for(community_slug))
        user = _find_comment_user(tree, parent_comment_id)
        name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x) \
            or user.get("name", "")
        uid = user.get("id") or target.get("user_id", "")
        mention = f"[@{name}](obj://user/{uid}) " if (name and uid) else ""
        return {"parent_id": top, "flattened": True, "mention": mention}

    def post_by_id(self, post_id: str) -> dict:
        """One post or comment by its id: ``GET api2 /posts/{id}``.

        Verified 2026-08-17. Returns the flat object (``metadata.title`` /
        ``metadata.content``, ``label_id``, ``root_id``), which is what an edit
        needs to keep the fields it isn't changing.
        """
        return self.http.get_api2(f"/posts/{post_id}")

    def update_post(self, post_id: str, *, title: str | None = None,
                    content: str | None = None, labels: str | None = None,
                    video_links: str | None = None,
                    attachments: str | None = None) -> dict:
        """Edit an existing post or comment (§5.9). Returns Skool's response.

        Captured from the browser 2026-08-17: the body is **flat**, not wrapped
        in ``metadata`` like `create_post`, and carries no ``group_id`` — the
        post id in the path is the whole address.

        Skool's own editor sends every field on every save, so a field left out
        of the body is *cleared*, not kept. That makes "just change the text"
        a way to silently drop a post's category and attachments. Every
        argument here therefore defaults to None meaning "leave as is": the
        current values are read back from `post_by_id` and only what the caller
        actually passed is replaced.

        A comment is a post, so this edits comments too (measured: same
        endpoint, ``title`` empty, no ``labels``).
        """
        current = self.post_by_id(post_id) or {}
        meta = current.get("metadata") or {}

        def keep(passed, *names, default=""):
            if passed is not None:
                return passed
            for n in names:
                v = meta.get(n)
                if v:
                    return v
            return default

        body: dict = {
            "title": keep(title, "title"),
            "content": keep(content, "content"),
            "attachments": keep(attachments, "attachments"),
            "video_links": keep(video_links, "video_links", "videoLinks"),
            "video_ids": [],
        }
        label = keep(labels, "labels", "label_id") or current.get("label_id") or ""
        if label:
            body["labels"] = label
        return self.http.post_api2(f"/posts/{post_id}/update", body)

    def delete_post(self, post_id: str) -> dict:
        """Delete a post or a comment (§5.9). Empty 200 on success.

        Same endpoint for both, because a comment IS a post. Verified live
        2026-08-16 (post) and 2026-08-17 (comment) on throwaway objects in
        `hoomans-9944`. Deleting somebody *else's* post needs moderator rights
        and is untested. Sends an empty JSON body ``{}``, as Skool's own client
        does for ``deletePost``; it is the only DELETE catknows sends with a body.
        """
        return self.http.delete_api2(f"/posts/{post_id}", {})

    def send_dm(self, channel_id: str, content: str, *, attachments: list[str] = ()) -> dict:
        """Send a direct message into an existing chat channel (§5.7).

        `channel_id` comes from `chat_channels()`. `attachments` is a list of
        file ids from `upload_file` — note the shape differs from posts: a DM
        takes a real JSON **array**, a post a comma-joined string.
        Returns the created message.
        """
        return self.http.post_api2(
            f"/channels/{channel_id}/messages?ct=wdm",
            {"content": content, "attachments": list(attachments)},
        )

    # -- convenience -----------------------------------------------------------

    def group_id_for(self, community_slug: str, *, for_write: bool = False) -> str:
        """Discover a community's group UUID without the user supplying it.

        Fast path: ``GET api2 /groups/{slug}`` resolves a slug directly
        (verified 2026-08-15). Fallback: it also "falls out" of the first posts
        response as ``post.groupId`` — so one posts fetch bootstraps it.

        ``for_write=True`` additionally refuses archived communities. That group
        payload already states it (``archived: true`` plus ``metadata.archived:
        1``, measured on `vrooms-3264` 17.08.), so the check costs no extra
        request. Archiving leaves a community readable and turns every write
        off, so only the write paths pass the flag; reads must keep working.
        """
        group = {}
        try:
            group = self.http.get_api2(f"/groups/{community_slug}") or {}
        except SkoolSecurityChallenge:
            raise  # Skool's security question is a stop, not a hiccup to route around
        except SkoolHTTPError:
            pass  # e.g. WAF hiccup — the posts bootstrap still works
        # Outside the try: an archived community is a definitive answer, not a
        # transient failure, so it must not fall through to the posts bootstrap.
        if for_write and (group.get("archived")
                          or (group.get("metadata") or {}).get("archived")):
            raise ValueError(
                f"'{community_slug}' is archived on Skool. Archived communities "
                "stay readable, but posting, commenting and liking are turned "
                "off, so this write would fail. Un-archive it in Skool's group "
                "settings first."
            )
        if group.get("id"):
            return group["id"]
        trees = self.posts(community_slug, all_pages=False)
        for tree in trees:
            gid = _dig(tree, "post", "groupId")
            if gid:
                return gid
        raise SkoolHTTPError(
            f"Could not determine group UUID for '{community_slug}' "
            "(no posts with a groupId found)."
        )


def tiptap(text: str) -> str:
    """Plain text -> Skool's ``[v2]`` TipTap page body (one paragraph per line).

    The minimum that round-trips: headings, marks, embeds etc. stay the
    caller's job — pass a ready ``[v2]…`` string to skip this entirely.
    """
    nodes = []
    for line in text.split("\n"):
        node: dict = {"type": "paragraph"}
        if line.strip():
            node["content"] = [{"type": "text", "text": line}]
        nodes.append(node)
    return "[v2]" + json.dumps(nodes, separators=(",", ":"), ensure_ascii=False)


_VTT_TIME = re.compile(
    r"(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})")


def _vtt_cues(vtt: str) -> list[dict]:
    """WebVTT text → ``[{start, end, text}]``, in order.

    Mux serves long videos as several VTT *segments*, so this parses a
    concatenation of them: every segment restarts at "WEBVTT" and renumbers
    its cues from 1, which is why cue numbers are ignored and timestamps are
    kept as the segments state them (already absolute). A cue's text can span
    several lines and is joined with spaces.
    """
    cues: list[dict] = []
    cur: dict | None = None
    for line in vtt.splitlines():
        stamp = _VTT_TIME.search(line)
        if stamp:
            cur = {"start": stamp.group(1), "end": stamp.group(2), "text": ""}
            cues.append(cur)
        elif cur is not None:
            line = line.strip()
            if not line:
                cur = None  # blank line ends the cue
            elif line != "WEBVTT":
                cur["text"] = f"{cur['text']} {line}".strip()
    return [c for c in cues if c["text"]]


def with_mention(mention: str, content: str) -> str:
    """Prefix ``mention`` unless the text already mentions that user."""
    ref = mention[mention.find("(") + 1:mention.find(")")] if mention else ""
    return content if (not mention or ref in content) else mention + content


def _find_comment_user(tree: dict, comment_id: str) -> dict:
    """The author object of one comment anywhere in a `comments()` tree."""
    stack = list((tree.get("post_tree") or {}).get("children") or [])
    while stack:
        node = stack.pop()
        post = node.get("post") or {}
        if post.get("id") == comment_id:
            return post.get("user") or {}
        stack.extend(node.get("children") or [])
    return {}


def _follow_key(entry: dict) -> tuple:
    return ((entry.get("src") or {}).get("id"), (entry.get("dst") or {}).get("id"))


def _dig(obj, *keys):
    """Safe nested lookup: _dig(data, 'pageProps', 'users') -> value or None."""
    for k in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
    return obj


if __name__ == "__main__":  # python -m catknows.client
    # The pagination walks are the only real logic in here, and the members
    # walk shipped duplicates for months. Fake the transport, assert the walk.
    class _FakeHTTP:
        def __init__(self, pages, is_admin=True):
            self.pages = pages
            self.calls = 0
            self.is_admin = is_admin

        def get_next(self, q, slug):
            page = self.pages[min(self.calls, len(self.pages) - 1)]
            self.calls += 1
            # isAdmin as Skool sends it (native bool, live members.json):
            # these cases test the page walk, so they walk as staff.
            return {"pageProps": {"users": page, "totalPages": len(self.pages),
                                  "isAdmin": self.is_admin}}

    def _client(pages, is_admin=True):
        c = SkoolClient.__new__(SkoolClient)
        c.http = _FakeHTTP(pages, is_admin)
        return c

    _INTER_PAGE_DELAY_S = 0  # noqa: F811 — don't sleep through the self-check
    p1 = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    # Skool serving page 1 again (the degraded-session case): stop, don't
    # collect it twice — and SAY the list is truncated. A silently short list
    # that looks complete is the bug Dan reported on 2026-08-14.
    repeat = _client([p1, p1, p1]).members("x")
    assert [u["id"] for u in repeat] == ["a", "b", "c"], repeat
    assert repeat.incomplete is True, "a repeated-page walk must flag incomplete"
    assert (repeat.pages_walked, repeat.total_pages) == (2, 3), \
        (repeat.pages_walked, repeat.total_pages)
    # As a regular member the walk stops after page 1 on purpose (Skool's UI
    # shows no more), and says so instead of looking complete.
    as_member = _client([p1, [{"id": "d"}]], is_admin=False)
    capped = as_member.members("x")
    assert [u["id"] for u in capped] == ["a", "b", "c"], capped
    assert as_member.http.calls == 1, "a member walk must not page"
    assert capped.capped_by_role is True and capped.incomplete is False, capped
    # Real page 2 that overlaps page 1 (live sort shifts a member across pages).
    overlap = _client([p1, [{"id": "c"}, {"id": "d"}]]).members("x")
    assert [u["id"] for u in overlap] == ["a", "b", "c", "d"], overlap
    assert overlap.incomplete is False, "a fully-walked list must not cry wolf"
    ids = [u["id"] for u in overlap]
    assert len(ids) == len(set(ids)), f"members() must not return duplicates: {ids}"
    # `limit` stops the walk early (a 160k community must not be fully paged
    # for limit=25) and satisfying the limit is NOT incomplete.
    limited = _client([p1, [{"id": "d"}, {"id": "e"}], [{"id": "f"}]])
    got = limited.members("x", limit=2)
    assert [u["id"] for u in got] == ["a", "b"], got
    assert limited.http.calls == 1, "limit satisfied on page 1 — stop paging"
    assert got.incomplete is False, "hitting the limit is success, not truncation"

    # The query builder: unfiltered page 1 must stay byte-identical to the
    # shape that's been verified live for weeks; the page-2 tail is the full
    # browser tail (incl. oneTime/free, verified live 2026-08-15).
    assert _members_query("x", 1, sort_type="-memberlastoffline") == \
        "/x/-/members.json?sortType=-memberlastoffline&group=x"
    assert _members_query("x", 2, sort_type="-memberlastoffline") == (
        "/x/-/members.json?sortType=-memberlastoffline&p=2"
        "&online=&levels=&price=&courseIds=&monthly=false&annual=false"
        "&oneTime=false&trials=false&free=false&group=x")
    filtered = _members_query(
        "x", 1, sort_type="-memberapprovedat", lifecycle="churned",
        flags={"admin": True, "annual": True}, tiers=["vip"], course_ids=["c1", "c2"])
    assert filtered == ("/x/-/members.json?t=churned&sortType=-memberapprovedat"
                        "&admin=true&annual=true&tiers=vip&courseIds=c1,c2&group=x"), filtered
    f2 = _members_query("x", 2, sort_type="-memberlastoffline",
                        flags={"admin": True, "online": True}, tiers=["vip"])
    assert "online=true" in f2 and "admin=true" in f2 and "tiers=vip" in f2, f2
    # Filter args reach the query, and bad values fail HERE, not as Skool 404s.
    fc = _client([p1])
    fc.members("x", lifecycle="churned", admins=True, sort="most_points")
    sent = fc.http.calls  # _FakeHTTP counts; the query itself:
    for bad_kwargs in ({"lifecycle": "quatsch"}, {"sort": "quatsch"}):
        try:
            _client([p1]).members("x", **bad_kwargs)
            raise AssertionError(f"{bad_kwargs} must be rejected")
        except ValueError:
            pass

    # chat_messages: the first read is capped at 50, so anything longer has to
    # walk the msg= cursor. Windows overlap on the boundary message.
    class _ChatHTTP:
        def __init__(self, pages):
            self.pages, self.queries = pages, []

        def get_api2(self, q):
            self.queries.append(q)
            page = self.pages[min(len(self.queries) - 1, len(self.pages) - 1)]
            return {"messages": page, "has_more_before": len(self.queries) < len(self.pages)}

    def _msg(i):
        return {"id": f"m{i}", "created_at": f"2026-01-{i:02d}T00:00:00Z"}

    chat = SkoolClient.__new__(SkoolClient)
    # newest page first in wall-clock, then two older windows that each repeat
    # their boundary message — exactly what Skool sends back.
    chat.http = _ChatHTTP([[_msg(20), _msg(21)], [_msg(10), _msg(20)], [_msg(1), _msg(10)]])
    got = chat.chat_messages("c1", count=99)["messages"]
    got_ids = [m["id"] for m in got]
    assert got_ids == ["m1", "m10", "m20", "m21"], got_ids
    assert len(got_ids) == len(set(got_ids)), f"overlapping windows must dedupe: {got_ids}"
    assert "before=50" in chat.http.queries[0], chat.http.queries[0]
    assert "msg=m20" in chat.http.queries[1], chat.http.queries[1]
    # A caller asking for less than one window must not trigger the walk.
    short = SkoolClient.__new__(SkoolClient)
    short.http = _ChatHTTP([[_msg(20), _msg(21)]])
    assert len(short.chat_messages("c1", count=2)["messages"]) == 2
    assert len(short.http.queries) == 1, "one window was enough, don't page"
    # tiptap: plain text must become valid [v2] JSON, blank lines empty paragraphs.
    body = tiptap("Zeile 1\n\nZeile 2 mit ümlaut")
    assert body.startswith("[v2]")
    nodes = json.loads(body[4:])
    assert [n.get("content", [{}])[0].get("text") for n in nodes] == \
        ["Zeile 1", None, "Zeile 2 mit ümlaut"], nodes

    # update_course_item: a COURSE update must read-merge the full settings set
    # (Skool resets `privacy` to 0 on a partial PUT — verified live 2026-08-15);
    # a non-root unit (tree read 400s) must send the fields verbatim.
    class _ClassroomHTTP:
        def __init__(self, tree_ok):
            self.tree_ok, self.put_bodies = tree_ok, []

        def get_api2(self, q):
            if not self.tree_ok:
                raise SkoolHTTPError("HTTP 400", 400)
            return {"course": {"metadata": {"title": "alt", "desc": "d",
                                            "privacy": 2, "min_tier": 1}}}

        def put_api2(self, q, body):
            self.put_bodies.append(body)
            return body

    cu = SkoolClient.__new__(SkoolClient)
    cu.http = _ClassroomHTTP(tree_ok=True)
    merged = cu.update_course_item("c1", {"title": "neu"})
    assert merged == {"title": "neu", "desc": "d", "privacy": 2, "min_tier": 1}, merged
    mod = SkoolClient.__new__(SkoolClient)
    mod.http = _ClassroomHTTP(tree_ok=False)
    flat = mod.update_course_item("m1", {"title": "t", "desc": "plain"})
    assert flat["title"] == "t" and flat["desc"].startswith("[v2]"), flat

    # WebVTT: two Mux segments concatenated, exactly as stitched live (Mux
    # restarts "WEBVTT" and renumbers cues from 1 per segment, so cue numbers
    # are meaningless and timestamps are already absolute). Text below is from
    # the real goosify capture (17.08.), plus a two-line cue.
    stitched = (
        "WEBVTT\n\n1\n00:00:00.000 --> 00:00:05.280\nBaby up, the vaults are open\n\n"
        "2\n00:00:05.280 --> 00:00:07.000\nThe scores are loaded\nMonday is coming\n\n"
        "WEBVTT\n\n1\n00:02:50.480 --> 00:02:51.480\n49, let's go\n"
    )
    cues = _vtt_cues(stitched)
    assert len(cues) == 3, cues
    assert cues[0] == {"start": "00:00:00.000", "end": "00:00:05.280",
                       "text": "Baby up, the vaults are open"}, cues[0]
    # A multi-line cue is ONE cue, joined — not two.
    assert cues[1]["text"] == "The scores are loaded Monday is coming", cues[1]
    # The second segment's cue keeps its own (absolute) time, and the repeated
    # "WEBVTT" header never leaks into the text.
    assert cues[2]["start"] == "00:02:50.480", cues[2]
    assert "WEBVTT" not in " ".join(c["text"] for c in cues)
    # A video with no audio has no subtitle track at all → no cues, not a crash.
    assert _vtt_cues("") == []

    print("client self-check OK")
