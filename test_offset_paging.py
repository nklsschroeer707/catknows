"""Self-check: a capped list must be continuable, not the end of history.

Dan Schaad, catnose 2026-09-24 (comments 0daf4b97…, 02d4ebe6…): the 200-per-call
cap is fine as a batch size, but list_posts(author=...) and get_follows stopped
there for good — a member with 210 followers or 300 posts could never be read
in full. The same held for list_members since 17.08. All three take `offset`
now, and the limit marker says where the next batch starts (`next_offset`).

The marker only appears when rows were really cut: asking 650 of a 10-row list
must not claim a cut (the 17.08. "Gegenfehler").

Run: python test_offset_paging.py    (no network, no pytest)
"""

import catknows.mcp_server as m
from catknows.client import MemberList


def _tree(i):
    return {"post": {"id": f"p{i}", "name": f"p{i}", "userId": "u",
                     "metadata": {"title": f"t{i}"}},
            "user": {"id": "u", "name": "dan"}}


class FakeClient:
    def __init__(self, n_posts=450, n_members=450, n_followers=210):
        self.n_posts, self.n_members, self.n_followers = n_posts, n_members, n_followers
        self.limits = []

    def post_categories(self, slug):
        return []

    def posts(self, slug, *, limit=None, **kw):
        self.limits.append(limit)
        return [_tree(i) for i in range(min(limit, self.n_posts))]

    def posts_by(self, slug, handle, *, limit=None):
        self.limits.append(limit)
        return [_tree(i) for i in range(min(limit, self.n_posts))]

    def resolve_member(self, slug, author):
        return {"name": author}

    def members(self, slug, *, limit=None, **kw):
        self.limits.append(limit)
        return MemberList([{"id": f"u{i}"} for i in range(min(limit, self.n_members))])

    def follows(self, user, direction, *, limit=0):
        self.limits.append(limit)
        n = min(limit or self.n_followers, self.n_followers)
        return {"total": self.n_followers, "incomplete": False,
                "entries": [{"src": {"id": f"f{i}", "name": f"f{i}"}, "dst": {"id": "me"},
                             "following": False} for i in range(n)]}


def _run(fake, fn, *a, **kw):
    real = m._get_client
    m._get_client = lambda: fake
    try:
        return fn(*a, **kw)
    finally:
        m._get_client = real


def _rows(out):
    return [r for r in out if not (r.get("limit_capped") or r.get("incomplete")
                                   or r.get("capped_by_role"))]


def _marker(out):
    found = [r for r in out if r.get("limit_capped")]
    return found[0] if found else None


def test_posts_first_batch_points_at_the_next():
    out = _run(FakeClient(), m.list_posts, "x", limit=500)
    assert len(_rows(out)) == 200
    assert _marker(out)["next_offset"] == 200, _marker(out)


def test_posts_offset_returns_the_following_batch():
    fake = FakeClient()
    out = _run(fake, m.list_posts, "x", limit=500, offset=200)
    ids = [r["skool_id"] for r in _rows(out)]
    assert ids[0] == "p200" and ids[-1] == "p399", (ids[0], ids[-1])
    assert _marker(out)["next_offset"] == 400, _marker(out)


def test_posts_last_batch_has_no_marker():
    out = _run(FakeClient(), m.list_posts, "x", limit=500, offset=400)
    assert len(_rows(out)) == 50
    assert _marker(out) is None, "nothing was cut, so nothing may be claimed"


def test_posts_no_cut_no_claim():
    out = _run(FakeClient(n_posts=10), m.list_posts, "x", limit=650)
    assert len(_rows(out)) == 10 and _marker(out) is None, out[-1]


def test_author_filter_pages_too():
    fake = FakeClient(n_posts=300)
    out = _run(fake, m.list_posts, "x", limit=500, author="sam", offset=200)
    assert len(_rows(out)) == 100, len(_rows(out))
    assert _rows(out)[0]["skool_id"] == "p200"
    assert _marker(out) is None


def test_follows_offset_reaches_the_tail():
    first = _run(FakeClient(), m.get_follows, "dan", limit=500)
    assert first["returned"] == 200 and first["limit_capped"]["next_offset"] == 200
    rest = _run(FakeClient(), m.get_follows, "dan", limit=500, offset=200)
    assert rest["returned"] == 10, rest["returned"]
    assert rest["people"][0]["name"] == "f200", rest["people"][0]
    assert "limit_capped" not in rest, rest.get("limit_capped")


def test_follows_default_call_says_more_exist():
    """Dan's exact call: get_follows on 210 followers, no limit given."""
    out = _run(FakeClient(), m.get_follows, "dan")
    assert out["returned"] == 200, out["returned"]
    assert out["limit_capped"]["next_offset"] == 200, out.get("limit_capped")


def test_members_offset_reaches_the_tail():
    out = _run(FakeClient(), m.list_members, "x", limit=500, offset=400)
    assert len(_rows(out)) == 50, len(_rows(out))
    assert _marker(out) is None
    first = _run(FakeClient(), m.list_members, "x", limit=500)
    assert _marker(first)["next_offset"] == 200, _marker(first)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok — capped lists say where the next batch starts and can be continued")
