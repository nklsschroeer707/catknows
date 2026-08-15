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


def test_mcp_tool_appends_incomplete_trailer():
    """The AI-facing layer must surface the flag, not swallow it."""
    import catknows.mcp_server as m

    class FakeClient:
        def members(self, slug, limit=None):
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
        def members(self, slug, limit=None):
            return MemberList(P1)

    m._get_client = lambda: FullClient()
    try:
        out = m.list_members("x", limit=30)
    finally:
        m._get_client = real
    assert len(out) == 30 and "incomplete" not in out[-1], out[-1]


if __name__ == "__main__":
    test_truncated_walk_is_flagged()
    test_full_walk_is_not_flagged()
    test_limit_stops_the_walk_early()
    test_mcp_tool_appends_incomplete_trailer()
    print("ok — truncated member walks are flagged, complete ones stay clean")
