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

    def __init__(self, pages):
        self.pages, self.calls = pages, 0

    def get_next(self, q, slug):
        page = self.pages[min(self.calls, len(self.pages) - 1)]
        self.calls += 1
        return {"pageProps": {"users": page, "totalPages": len(self.pages)}}


def _client(pages):
    c = SkoolClient.__new__(SkoolClient)
    c.http = _FakeHTTP(pages)
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


if __name__ == "__main__":
    test_truncated_walk_is_flagged()
    test_full_walk_is_not_flagged()
    test_limit_stops_the_walk_early()
    test_mcp_tool_filter_plumbing()
    test_mcp_tool_appends_incomplete_trailer()
    test_mcp_tool_flags_our_own_cap()
    print("ok — truncated member walks are flagged, complete ones stay clean")
