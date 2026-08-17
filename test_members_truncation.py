"""Self-check: a truncated members walk must SAY it is truncated.

Dan Schaad's 2026-08-14 report: on a degraded session Skool re-serves page 1
for every follow-up members page. The dedupe (14.08.) stopped the duplicate
rows, but the walk then ended silently at ~30 of 600 — a wrong result that
LOOKS complete, which is worse than the duplicates were. members() now
returns a MemberList that flags the truncation, and the MCP tool appends an
{"incomplete": true} trailer entry. This test fails if either honesty check
is removed again.

Run: python test_members_truncation.py    (no network, no pytest)
"""

import os

os.environ["CATKNOWS_PAGE_DELAY"] = "0"  # set before import — read at module load

from catknows.client import MemberList, SkoolClient  # noqa: E402


class _FakeHTTP:
    """Serves canned members.json pages; repeats the last one past the end."""

    def __init__(self, pages, total=None, total_pages=None, is_admin=True):
        self.pages, self.calls = pages, 0
        self.total, self.total_pages = total, total_pages
        self.is_admin = is_admin

    def get_next(self, q, slug):
        page = self.pages[min(self.calls, len(self.pages) - 1)]
        self.calls += 1
        pp = {"users": page,
              "totalPages": self.total_pages or len(self.pages),
              # Native bool, spelled as Skool spells it — copied from a live
              # members.json (hoomans True / skooligans False, 2026-08-17).
              "isAdmin": self.is_admin}
        if self.total is not None:
            pp["total"] = self.total
        return {"pageProps": pp}


def _client(pages, total=None, total_pages=None, is_admin=True):
    c = SkoolClient.__new__(SkoolClient)
    c.http = _FakeHTTP(pages, total, total_pages, is_admin)
    return c


P1 = [{"id": f"u{i}"} for i in range(30)]
P2 = [{"id": f"v{i}"} for i in range(30)]


def test_truncated_walk_is_flagged():
    """Skool repeats page 1 (20-page community) -> 30 unique, incomplete=True."""
    pages = [P1] * 20
    got = _client(pages).members("x", limit=65)
    assert [u["id"] for u in got] == [u["id"] for u in P1], "dedupe must hold"
    assert got.incomplete is True, "a repeated-page walk must flag incomplete"
    assert got.total_pages == 20 and got.pages_walked < 20, \
        (got.pages_walked, got.total_pages)


def test_full_walk_is_not_flagged():
    got = _client([P1, P2]).members("x", limit=60)
    assert len(got) == 60
    assert got.incomplete is False, "a complete walk must not cry wolf"


def test_limit_stops_the_walk_early():
    """limit=30 on a 3-page community: one request, and NOT incomplete."""
    c = _client([P1, P2, P2])
    got = c.members("x", limit=30)
    assert len(got) == 30
    assert c.http.calls == 1, f"limit satisfied on page 1, stop paging: {c.http.calls}"
    assert got.incomplete is False, "hitting the limit is success, not truncation"


def test_short_walk_is_flagged_even_when_all_pages_were_walked():
    """catnose, 2026-08-17, measured as OWNER: Skool reported totalPages=2 and
    served byte-identical rows on pages 1..4, so the walk ran both pages it was
    told about and stopped with 30 rows while the same response said total=35.
    Page count called it complete; the row count did not. This is the case the
    revoke pass must never mistake for a full list — a member missing from a
    short list would look like someone who cancelled."""
    got = _client([P1, P1], total=35, total_pages=2).members("x")
    assert len(got) == 30, len(got)
    assert got.pages_walked >= got.total_pages, "every announced page was walked"
    assert got.total_members == 35, got.total_members
    assert got.short_by == 5, got.short_by
    assert got.incomplete is True, "walked every page and still short = incomplete"


def test_complete_walk_with_total_stays_clean():
    """The guard must not cry wolf when Skool's count and the rows agree."""
    got = _client([P1, P2], total=60).members("x")
    assert len(got) == 60 and got.short_by == 0
    assert got.incomplete is False, "count matches rows — nothing is missing"


def test_limit_below_total_is_not_truncation():
    """limit=25 on a 600-member community is short ON PURPOSE. Flagging it
    would make the marker meaningless on the most common call there is."""
    got = _client([P1, P2], total=593).members("x", limit=25)
    assert len(got) == 25 and got.total_members == 593
    assert got.incomplete is False, "asking for fewer than all is not truncation"


def test_missing_total_keeps_old_behaviour():
    """Skool omitting `total` must not make every walk look suspicious."""
    got = _client([P1, P2]).members("x")
    assert got.total_members is None and got.short_by == 0
    assert got.incomplete is False


def test_vault_pull_states_the_role_cap():
    """Files outlive the call that wrote them: a partial roster on disk must
    carry the reason, or it gets trusted as the full community later."""
    import tempfile

    import catknows.mcp_server as m

    class FakeClient:
        def members(self, slug, **kwargs):
            ml = MemberList(P1)
            ml.capped_by_role, ml.total_members = True, 597
            return ml

        def posts(self, slug, **kwargs):
            return []

    real = m._get_client
    m._get_client = lambda: FakeClient()
    try:
        with tempfile.TemporaryDirectory() as d:
            res = m._pull_to_vault("x", d, include_comments=False)
    finally:
        m._get_client = real
    assert res["members"] == 30, res
    assert res["members_capped_by_role"] is True, res
    assert res["members_reported_by_skool"] == 597, res
    assert "partial roster" in res["note"], res


def test_vault_pull_stays_quiet_for_staff():
    """An owner's full pull must not carry a cap note."""
    import tempfile

    import catknows.mcp_server as m

    class FakeClient:
        def members(self, slug, **kwargs):
            return MemberList(P1)  # capped_by_role stays False

        def posts(self, slug, **kwargs):
            return []

    real = m._get_client
    m._get_client = lambda: FakeClient()
    try:
        with tempfile.TemporaryDirectory() as d:
            res = m._pull_to_vault("x", d, include_comments=False)
    finally:
        m._get_client = real
    assert "members_capped_by_role" not in res and "note" not in res, res


def test_member_gets_first_page_only():
    """A regular member stops after page 1: one request, no second page, and
    the cut is stated. Skool's UI shows a member no more than this."""
    c = _client([P1, P2], total=597, total_pages=20, is_admin=False)
    got = c.members("x")
    assert [u["id"] for u in got] == [u["id"] for u in P1], got
    assert c.http.calls == 1, f"member walk must not page: {c.http.calls} calls"
    assert got.capped_by_role is True, "a cut list must say it was cut"
    # The role cap owns this case; incomplete would send a caller retrying
    # against a wall that is there on purpose.
    assert got.incomplete is False, "role cap must not also cry truncation"


def test_staff_still_walks_every_page():
    """isAdmin=True is owner/admin/moderator — the walk is unchanged."""
    c = _client([P1, P2], total=60, total_pages=2, is_admin=True)
    got = c.members("x")
    assert len(got) == 60, len(got)
    assert c.http.calls == 2, f"staff must page: {c.http.calls}"
    assert got.capped_by_role is False and got.incomplete is False


def test_member_of_single_page_community_is_not_capped():
    """Page 1 IS the whole list here, so claiming a cap would be a lie."""
    got = _client([P1], total=30, total_pages=1, is_admin=False).members("x")
    assert len(got) == 30 and got.capped_by_role is False, got.capped_by_role


def test_mcp_trailer_states_the_role_cap():
    """The trailer must tell an agent this is NOT the full list, in the same
    shape as the existing limit_capped/incomplete markers."""
    import catknows.mcp_server as m

    class FakeClient:
        def members(self, slug, **kwargs):
            ml = MemberList(P1)
            ml.capped_by_role, ml.total_pages, ml.total_members = True, 20, 597
            return ml

    real = m._get_client
    m._get_client = lambda: FakeClient()
    try:
        out = m.list_members("x")
    finally:
        m._get_client = real
    trailer = out[-1]
    assert trailer["capped_by_role"] is True, trailer
    assert trailer["members_reported_by_skool"] == 597, trailer
    assert "NOT the full member list" in trailer["note"], trailer["note"]


def test_limit_marker_reports_real_rows_when_role_cap_hits_first():
    """Both markers can fire on one call (limit=650 as a plain member). The
    limit marker must then report the rows that actually came back, not the
    200 it would have allowed — measured live on skooligans: 30 rows."""
    import catknows.mcp_server as m

    class FakeClient:
        def members(self, slug, **kwargs):
            ml = MemberList(P1)
            ml.capped_by_role, ml.total_pages, ml.total_members = True, 20, 597
            return ml

    real = m._get_client
    m._get_client = lambda: FakeClient()
    try:
        out = m.list_members("x", limit=650)
    finally:
        m._get_client = real
    limit_marker = [r for r in out if r.get("limit_capped")][0]
    assert limit_marker["returned"] == 30, limit_marker
    assert limit_marker["requested"] == 650, limit_marker


def test_mcp_trailer_names_the_missing_members():
    """An agent reading the trailer must learn the list is a lower bound —
    'Skool repeated pages' would be the wrong explanation for this case."""
    import catknows.mcp_server as m

    class FakeClient:
        def members(self, slug, **kwargs):
            ml = MemberList(P1)
            ml.incomplete, ml.pages_walked, ml.total_pages = True, 2, 2
            ml.total_members = 35
            return ml

    real = m._get_client
    m._get_client = lambda: FakeClient()
    try:
        out = m.list_members("x")
    finally:
        m._get_client = real
    trailer = out[-1]
    assert trailer["incomplete"] is True and trailer["missing"] == 5, trailer
    assert trailer["members_reported_by_skool"] == 35, trailer
    assert "LOWER BOUND" in trailer["note"], trailer["note"]


def test_mcp_tool_filter_plumbing():
    """Flat string filters reach members() as kwargs; unknown flags fail fast."""
    import catknows.mcp_server as m

    seen = {}

    class FakeClient:
        def members(self, slug, **kwargs):
            seen.update(kwargs)
            return MemberList()

    real = m._get_client
    m._get_client = lambda: FakeClient()
    try:
        m.list_members("x", limit=5, lifecycle="churned", sort="most_points",
                       filters="admins, online", tiers="standard\\,x,vip",
                       course_ids="c1,c2")
        assert seen["lifecycle"] == "churned" and seen["sort"] == "most_points", seen
        assert seen["admins"] is True and seen["online"] is True, seen
        assert seen["tiers"] == ["standard,x", "vip"], "\\, must stay one tier"
        assert seen["course_ids"] == ["c1", "c2"], seen
        assert "trials" not in seen, "unset flags must stay unset"
        try:
            m.list_members("x", filters="admins,quatsch")
            raise AssertionError("unknown filter flag must be rejected")
        except ValueError as e:
            assert "quatsch" in str(e), e
    finally:
        m._get_client = real


def test_mcp_tool_appends_incomplete_trailer():
    """The AI-facing layer must surface the flag, not swallow it."""
    import catknows.mcp_server as m

    class FakeClient:
        def members(self, slug, **kwargs):
            ml = MemberList(P1)
            ml.incomplete, ml.pages_walked, ml.total_pages = True, 2, 20
            return ml

    real = m._get_client
    m._get_client = lambda: FakeClient()
    try:
        out = m.list_members("x", limit=65)
    finally:
        m._get_client = real
    assert len(out) == 31, "30 members + 1 trailer"
    trailer = out[-1]
    assert trailer.get("incomplete") is True, trailer
    assert trailer["unique_members_returned"] == 30, trailer
    assert trailer["total_pages"] == 20, trailer
    # And a complete result must stay exactly as before — no trailer.
    class FullClient:
        def members(self, slug, **kwargs):
            return MemberList(P1)

    m._get_client = lambda: FullClient()
    try:
        out = m.list_members("x", limit=30)
    finally:
        m._get_client = real
    assert len(out) == 30 and "incomplete" not in out[-1], out[-1]


def test_mcp_tool_flags_our_own_cap():
    """Dan Schaad, 17.08.: hoomans has 592 members, limit=650 returned exactly
    200 and said nothing. The cap is ours and deliberate; being silent about it
    was the bug, because 200 rows with no marker read as the whole community.
    The incomplete trailer never covered this — it only fires when SKOOL cuts
    the walk short, not when we lower the limit."""
    import catknows.mcp_server as m

    effective, capped = m._cap(650, raw=False)
    assert effective == 200, effective
    assert capped["limit_capped"] is True and capped["requested"] == 650, capped
    assert capped["returned"] == 200, capped
    assert m._cap(200, raw=False) == (200, None), "asking for exactly the cap is not capped"
    assert m._cap(25, raw=False) == (25, None), "the common case must stay clean"
    assert m._cap(100, raw=True)[0] == 30, "raw caps harder"
    assert m._cap(100, raw=True)[1]["returned"] == 30, "and says so"

    # Both list tools must append it — one shared mechanism, not two patches.
    class FakeClient:
        def members(self, slug, **kwargs):
            return MemberList([{"id": f"u{i}"} for i in range(kwargs["limit"])])

        def posts(self, slug, limit):
            return [{"post": {"id": f"p{i}", "metadata": {}}} for i in range(limit)]

    real = m._get_client
    m._get_client = lambda: FakeClient()
    try:
        members = m.list_members("x", limit=650)
        posts = m.list_posts("x", limit=650)
    finally:
        m._get_client = real
    assert len(members) == 201, "200 members + 1 trailer"
    assert members[-1]["limit_capped"] is True, members[-1]
    assert len(posts) == 201, "200 posts + 1 trailer"
    assert posts[-1]["requested"] == 650, posts[-1]


def test_no_cap_marker_when_nothing_was_cut():
    """The silent cap's mirror image: `limit=650` against a 10-member community
    returns all 10, so claiming a truncation invents one. Measured live on
    vrooms-3264 (10 of 10 rows, marker fired anyway)."""
    import catknows.mcp_server as m

    class FakeClient:
        def members(self, slug, **kwargs):
            return MemberList(P1[:10])

        def posts(self, slug, limit):
            return [{"post": {"id": f"p{i}", "metadata": {}}} for i in range(10)]

    real = m._get_client
    m._get_client = lambda: FakeClient()
    try:
        members = m.list_members("x", limit=650)
        posts = m.list_posts("x", limit=650)
    finally:
        m._get_client = real
    assert len(members) == 10, members
    assert not any(r.get("limit_capped") for r in members), members
    assert len(posts) == 10, posts
    assert not any(r.get("limit_capped") for r in posts), posts


def test_cap_marker_still_fires_on_a_real_cut():
    """The honesty check itself must survive the fix: a genuine cut still says so."""
    import catknows.mcp_server as m

    class FakeClient:
        def members(self, slug, **kwargs):
            return MemberList([{"id": f"u{i}"} for i in range(200)])

        def posts(self, slug, limit):
            return [{"post": {"id": f"p{i}", "metadata": {}}} for i in range(200)]

    real = m._get_client
    m._get_client = lambda: FakeClient()
    try:
        members = m.list_members("x", limit=650)
        posts = m.list_posts("x", limit=650)
    finally:
        m._get_client = real
    assert members[-1]["limit_capped"] is True, members[-1]
    assert members[-1]["returned"] == 200, members[-1]
    assert posts[-1]["limit_capped"] is True, posts[-1]


def test_vault_pull_paces_comment_fetches():
    """A 700-post pull fired ~4.5 comment requests/s and CloudFront blocked it
    after ~165 (comments_failed: 543 on hoomans, 2026-08-17). One sleep per
    post after the first, none when comments are off."""
    import tempfile

    import catknows.mcp_server as m

    class FakeClient:
        def members(self, slug, **kwargs):
            return MemberList([])

        def posts(self, slug, **kwargs):
            # The count lives in post.metadata.comments — verified against a
            # live hoomans post (594), where no top-level "comments" key exists.
            return [{"post": {"id": f"p{i}", "groupId": "g1",
                              "metadata": {"comments": 3}}} for i in range(5)]

        def comments(self, post_id, group_id):
            return {"post_tree": {"children": []}}

    slept: list[float] = []
    real, real_sleep = m._get_client, None
    m._get_client = lambda: FakeClient()
    import time as _t
    real_sleep = _t.sleep
    _t.sleep = slept.append
    try:
        with tempfile.TemporaryDirectory() as d:
            res = m._pull_to_vault("x", d, include_comments=True)
        paced = list(slept)
        slept.clear()
        with tempfile.TemporaryDirectory() as d:
            m._pull_to_vault("x", d, include_comments=False)
        unpaced = list(slept)
    finally:
        m._get_client, _t.sleep = real, real_sleep
    assert res["posts"] == 5 and res["comments_failed"] == 0, res
    # 5 posts with comments -> 4 pauses (none before the first fetch).
    # This module pins CATKNOWS_PAGE_DELAY=0, so assert the pull reuses that
    # knob rather than a second hardcoded one — the value itself is config.
    from catknows.client import _INTER_PAGE_DELAY_S
    assert paced == [_INTER_PAGE_DELAY_S] * 4, paced
    assert unpaced == [], "include_comments=False must not pace anything"


def test_waf_block_is_waited_out_but_dead_session_is_not():
    """CloudFront's burst 403 carries 'Request blocked' and heals after ~60s;
    a stale session's 403 does not and must fail fast. Bodies copied from a
    live block (2026-08-17) and from _auth_rejected's own path."""
    import catknows.http as H

    BLOCK = ('<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">\n'
             "<HTML><HEAD><TITLE>ERROR: The request could not be satisfied</TITLE>\n"
             "</HEAD><BODY><H1>403 ERROR</H1>\nRequest blocked.\n</BODY></HTML>")

    def _client(bodies):
        c = H.SkoolHTTP.__new__(H.SkoolHTTP)
        c._cache, c._profile = {}, {"ua": "t", "lang": "en", "sec_ch_ua": ""}
        c._cookie_header = lambda: "t"
        c.calls = []

        class Resp:
            def __init__(s, code, body):
                s.status_code, s.text = code, body

        def get(url, headers=None, timeout=None):
            code, body = bodies[min(len(c.calls), len(bodies) - 1)]
            c.calls.append(url)
            return Resp(code, body)

        c._http = type("T", (), {"get": staticmethod(get)})()
        return c

    slept: list[float] = []
    real_sleep = H.time.sleep
    H.time.sleep = slept.append
    try:
        # Blocked twice, then served: the pull survives instead of losing 543.
        c = _client([(403, BLOCK), (403, BLOCK), (200, '{"ok":1}')])
        assert c._get_with_retry("https://x/y", {}) == {"ok": 1}
        assert len(c.calls) == 3, c.calls
        assert slept == [H.RETRY_WAF_DELAY_S] * 2, slept

        # A stale session 403 has no marker: fail immediately, no waiting.
        slept.clear()
        c = _client([(403, '{"error":"forbidden"}')])
        try:
            c._get_with_retry("https://x/y", {})
        except H.SkoolHTTPError as e:
            assert e.status == 403 and "gone stale" in str(e), e
        else:
            raise AssertionError("a stale-session 403 must raise")
        assert slept == [], slept
        assert len(c.calls) == 1, c.calls

        # A block that never clears still raises rather than looping forever.
        slept.clear()
        c = _client([(403, BLOCK)])
        try:
            c._get_with_retry("https://x/y", {})
        except H.SkoolHTTPError as e:
            assert e.status == 403, e
        else:
            raise AssertionError("an unending block must raise")
        assert len(slept) == H.MAX_RETRIES_WAF, slept
    finally:
        H.time.sleep = real_sleep


if __name__ == "__main__":
    # Run every test_* in this module — an explicit list silently skipped five
    # of them for a while, which is the same failure mode these tests exist to
    # catch: looking complete while being short.
    _fns = [(n, f) for n, f in sorted(vars().items())
            if n.startswith("test_") and callable(f)]
    for _n, _f in _fns:
        _f()
    print(f"ok — {len(_fns)} checks passed: truncation, role cap, "
          "limit marker, comment pacing")
