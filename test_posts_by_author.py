"""Self-check: list_posts(author=...) returns the posts a member CREATED.

Skool has no author filter on the feed, and its search matches a name
anywhere — @mentions in other people's comments included. The member's
profile scoped to a community (docs/API.md §1.3b) lists every post they take
part in; keeping post.userId == their id gives exactly their own posts.
Measured on cat-knows-1423, 2026-09-22: 77 entries, 46 own = all 46 in the feed.

Run: python test_posts_by_author.py    (no network, no pytest)
"""

import catknows.client as client_mod
from catknows.client import SkoolClient

SLUG = "some-group"
ME = {"id": "u-me", "name": "niklas", "firstName": "Niklas", "lastName": "Schröer"}
TWIN = {"id": "u-twin", "name": "niklas-2", "firstName": "Niklas", "lastName": "Other"}


def _tree(pid, uid):
    return {"post": {"id": pid, "name": pid, "userId": uid, "rootId": pid,
                     "metadata": {"title": pid}}}


# page 1: own + someone else's post I commented on; page 2: own; page 3: empty
PAGES = {1: [_tree("a", "u-me"), _tree("b", "u-other")],
         2: [_tree("c", "u-me")], 3: []}


class _FakeHTTP:
    def __init__(self, members=(ME,)):
        self.gets = []
        self.members = members

    def get_next(self, q, slug):
        self.gets.append(q)
        if "/-/search.json" in q:
            return {"pageProps": {"members": [{"id": "m", "user": u} for u in self.members]}}
        if q.startswith("/@niklas.json"):
            page = int(q.split("&p=")[1]) if "&p=" in q else 1
            return {"pageProps": {"currentUser": ME, "postTrees": PAGES[page]}}
        return {"pageProps": {"currentGroup": {"labels": []}}}

    def _no_write(self, *a, **k):
        raise AssertionError("a read sent a write")

    post_api2 = put_api2 = delete_api2 = _no_write


def _client(**kw):
    c = SkoolClient.__new__(SkoolClient)
    c.http = _FakeHTTP(**kw)
    return c


def test_only_own_posts_across_pages():
    client_mod._INTER_PAGE_DELAY_S = 0
    c = _client()
    got = [t["post"]["id"] for t in c.posts_by(SLUG, "niklas")]
    assert got == ["a", "c"], got
    assert all(f"g={SLUG}" in q for q in c.http.gets if q.startswith("/@niklas")), c.http.gets


def test_limit_stops_the_walk():
    client_mod._INTER_PAGE_DELAY_S = 0
    c = _client()
    assert [t["post"]["id"] for t in c.posts_by(SLUG, "niklas", limit=1)] == ["a"]
    assert not any("&p=2" in q for q in c.http.gets), c.http.gets


def test_a_full_name_resolves_through_member_search():
    c = _client(members=(ME, TWIN))
    assert c.resolve_member(SLUG, "Niklas Schröer")["name"] == "niklas"
    assert c.resolve_member(SLUG, "@niklas")["name"] == "niklas"


def test_an_ambiguous_name_names_the_candidates():
    c = _client(members=(ME, TWIN))
    try:
        c.resolve_member(SLUG, "Niklas S")
    except ValueError as e:
        assert "@niklas" in str(e) and "@niklas-2" in str(e), e
    else:
        raise AssertionError("ambiguous name must not pick one")


def test_the_tool_filters_by_author_and_refuses_mixed_filters():
    import catknows.mcp_server as m
    fn = lambda t: t.fn if hasattr(t, "fn") else t
    client_mod._INTER_PAGE_DELAY_S = 0
    c = _client()
    old = m._get_client
    m._get_client = lambda: c
    try:
        posts = fn(m.list_posts)(SLUG, author="Niklas Schröer")
        assert [p["skool_id"] for p in posts] == ["a", "c"], posts
        try:
            fn(m.list_posts)(SLUG, author="niklas", sort="new")
        except ValueError:
            pass
        else:
            raise AssertionError("author + sort must refuse")
    finally:
        m._get_client = old


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all green")
