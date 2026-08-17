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

from .auth import Session
from .http import SkoolHTTP, SkoolHTTPError

# Be polite to Skool between paginated requests (WAF-friendly, less robotic).
# CATKNOWS_PAGE_DELAY (seconds) overrides — lower is faster but more robotic;
# you carry the 403/ban risk yourself.
_INTER_PAGE_DELAY_S = float(os.environ.get("CATKNOWS_PAGE_DELAY", "0.8"))
_COMMENT_PAGE_LIMIT = 25
_MAX_COMMENT_PAGES = 400  # safety cap: 400 * 25 = 10k comments per post


class MemberList(list):
    """``members()`` result. ``incomplete=True`` means Skool stopped serving
    fresh pages before ``total_pages`` was reached — the list is a truncated
    view, NOT the whole community. Measured 2026-08-15: Skool re-serves page 1
    per community/role/session — a fresh session walked 592-member hoomans
    fully as a regular member, while 5.5k-member theskoolhub repeated page 1
    from page 2 on with the very same session. A stale WAF token additionally
    degrades members.json into 202-challenges/403s."""

    incomplete = False
    pages_walked = 1
    total_pages = 1


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
        out.incomplete = bool(all_pages and page < out.total_pages
                              and (limit is None or len(out) < limit))
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

    # -- classroom write path (docs/API.md §7) ---------------------------------
    # Course, folder and page are ONE object type ("unit") in a tree:
    # unit_type "course" (root), "set" (folder), "module" (page). All verbs go
    # through api2 /courses. Verified live 2026-08-15 on a private community.

    def course_tree(self, course_id: str) -> dict:
        """One course's full tree: ``{course, children:[{course, children}]}``.

        This is the ONLY way to read modules/pages — the classroom page payload
        carries just the course list, and ``GET /courses/{moduleId}`` answers
        400 for non-root units. Child order in ``children[]`` is display order.
        """
        return self.http.get_api2(f"/courses/{course_id}?withChildren=true")

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
        """
        fields = dict(fields)
        if "desc" in fields and not str(fields["desc"]).startswith("[v2]"):
            fields["desc"] = tiptap(str(fields["desc"]))
        try:
            cur = (self.course_tree(unit_id).get("course") or {}).get("metadata") or {}
        except SkoolHTTPError:
            cur = None  # not a root unit -> flat update, caller sends full set
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
        This does NOT re-parent (Skool ignores a PUT ``parent_id`` too) — to
        move a page into another folder, recreate it there and delete the old.
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

    # -- chat ------------------------------------------------------------------

    def chat_channels(self, *, offset: int = 0, limit: int = 30) -> dict:
        """Your DM channels (docs/API.md §1.6) — the channel ids `send_dm` needs.

        ``limit`` above 30 is refused by Skool ("invalid limit").
        """
        return self.http.get_api2(
            f"/self/chat-channels?offset={offset}&limit={limit}&last=true&unread-only=false"
        )

    def chat_messages(self, channel_id: str, *, count: int = 30) -> dict:
        """Messages of one DM channel, newest last (docs/API.md §1.6).

        Two different shapes hide behind one endpoint, and only using both
        gets you a full history:

        * The opening read takes ``before=N`` — NOT a timestamp and NOT a
          message id, but "how many messages back from newest". It is capped
          at 50; anything larger is refused with ``invalid before``.
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
            f"/channels/{channel_id}/messages?limit={n}&before={n}"
        )
        msgs = list(data.get("messages") or [])
        seen = {m.get("id") for m in msgs}

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
        attachments: str = "",
    ) -> dict:
        """Comment on a post, or reply to a comment (§5.8).

        A comment IS a post with ``post_type: "comment"`` — same endpoint as
        `create_post`. ``root_id`` is always the post; ``parent_id`` is the
        comment being replied to, or the post itself for a top-level comment.
        `attachments` is a comma-joined list of file ids from `upload_file`.
        Returns the created comment object.
        """
        metadata: dict = {"title": "", "content": content}
        if attachments:
            metadata["attachments"] = attachments
        return self.http.post_api2(
            "/posts?follow=false",
            {
                "post_type": "comment",
                "group_id": self.group_id_for(community_slug, for_write=True),
                "root_id": post_id,
                "parent_id": parent_comment_id or post_id,
                "metadata": metadata,
            },
        )

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
        and is untested.
        """
        return self.http.delete_api2(f"/posts/{post_id}")

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
        def __init__(self, pages):
            self.pages = pages
            self.calls = 0

        def get_next(self, q, slug):
            page = self.pages[min(self.calls, len(self.pages) - 1)]
            self.calls += 1
            return {"pageProps": {"users": page, "totalPages": len(self.pages)}}

    def _client(pages):
        c = SkoolClient.__new__(SkoolClient)
        c.http = _FakeHTTP(pages)
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
