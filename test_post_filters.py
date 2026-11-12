"""Self-check: Skool sorts, filters and searches, so the AI doesn't have to.

Reading 200 posts to find the three unread ones burns tokens a filter on
Skool's side saves. Parameters measured live on cat-knows-1423, 2026-09-22
(docs/API.md §1.2 and §1.8): s=newest|best-1d..best-1y|best, fl=unr with
s=newest, c={labelId}, and /-/search.json?q=&t=posts|members&p=N.

Run: python test_post_filters.py    (no network, no pytest)
"""

from catknows import normalize
from catknows.client import SkoolClient

SLUG = "some-group"
LABELS = [
    {"id": "a" * 32, "metadata": {"displayName": "🐽 General", "posts": 60}},
    {"id": "b" * 32, "metadata": {"displayName": "📮 Feedback", "posts": 17}},
]
TREE = {"post": {"id": "p1", "name": "hello", "labelId": "b" * 32, "rootId": "p1",
                 "metadata": {"title": "Hello", "labels": "b" * 32,
                              "hasNewComments": 1, "comments": 3}}}


class _FakeHTTP:
    def __init__(self, pages=1):
        self.gets = []
        self.pages = pages

    def get_next(self, q, slug):
        self.gets.append(q)
        if "/-/search.json" in q:
            if "t=members" in q:
                return {"pageProps": {"totalMembers": 1, "page": 1, "members": [{
                    "id": "m1", "role": "group-admin", "groupId": "g1",
                    "user": {"id": "u1", "name": "mindy", "firstName": "Mindy",
                             "metadata": {"bio": "hi", "spData": '{"pts":7,"lv":2}'}}}]}}
            return {"pageProps": {"totalPosts": 24, "totalPostPages": 3, "page": 2,
                                  "postTrees": [TREE]}}
        page = int(q.split("&p=")[1].split("&")[0]) if "&p=" in q else 1
        trees = [{"post": {"id": f"p{page}"}}] if page <= self.pages else []
        return {"pageProps": {"postTrees": trees,
                              "currentGroup": {"labels": LABELS}}}

    def _no_write(self, *a, **k):
        raise AssertionError("a read sent a write")

    post_api2 = put_api2 = delete_api2 = _no_write


def _client(pages=1):
    c = SkoolClient.__new__(SkoolClient)
    c.http = _FakeHTTP(pages)
    return c


def test_default_feed_query_is_unchanged():
    """Same URL as before, so the cache and old callers see no difference."""
    c = _client()
    c.posts(SLUG)
    assert c.http.gets[0] == f"/{SLUG}.json?group={SLUG}", c.http.gets


def test_sort_names_map_to_skool_values():
    for sort, s in (("new", "newest"), ("top_day", "best-1d"), ("top_week", "best-1w"),
                    ("top_month", "best-1m"), ("top_year", "best-1y"), ("top_all", "best"),
                    ("activity", None)):
        c = _client()
        c.posts(SLUG, sort=sort)
        q = c.http.gets[0]
        if s:
            assert f"&s={s}" in q, (sort, q)
        else:
            assert "&s=" not in q, "activity is Skool's default, no parameter"


def test_unread_only_is_newest_plus_unr_like_the_website():
    c = _client()
    c.posts(SLUG, unread_only=True, sort="top_week")
    q = c.http.gets[0]
    assert "&s=newest" in q and "&fl=unr" in q, q
    assert "best-1w" not in q, "the website forces 'newest' for the unread tab"


def test_category_and_filters_survive_paging():
    c = _client(pages=2)
    c.posts(SLUG, sort="new", category_id="b" * 32)
    assert len(c.http.gets) == 3, c.http.gets
    for q in c.http.gets:
        assert "&c=" + "b" * 32 in q and "&s=newest" in q, q
    assert "&p=2" in c.http.gets[1], c.http.gets


def test_bad_sort_is_refused_before_skool():
    c = _client()
    try:
        c.posts(SLUG, sort="hot")
    except ValueError as e:
        assert "top_week" in str(e), "the error lists what works"
    else:
        raise AssertionError("unknown sort reached Skool")
    assert c.http.gets == []


def test_categories_resolve_by_name_or_id():
    c = _client()
    cats = c.post_categories(SLUG)
    assert cats == [{"id": "a" * 32, "name": "🐽 General", "posts": 60},
                    {"id": "b" * 32, "name": "📮 Feedback", "posts": 17}], cats
    assert c.resolve_category(SLUG, "feedback") == "b" * 32, "name without emoji, any case"
    assert c.resolve_category(SLUG, "📮 Feedback") == "b" * 32
    assert c.resolve_category(SLUG, "b" * 32) == "b" * 32
    try:
        c.resolve_category(SLUG, "memes")
    except ValueError as e:
        assert "Feedback" in str(e) and "General" in str(e), "say which ones exist"
    else:
        raise AssertionError("unknown category silently ignored")


def test_post_record_carries_category_and_new_comments():
    rec = normalize.post(TREE)
    assert rec["category_id"] == "b" * 32, rec
    assert rec["has_new_comments"] is True, rec


def test_search_posts_query_and_paging():
    c = _client()
    out = c.search(SLUG, "follower & co", kind="posts", page=2)
    q, = c.http.gets
    assert q == f"/{SLUG}/-/search.json?q=follower%20%26%20co&t=posts&group={SLUG}&p=2", q
    assert out["totalPostPages"] == 3


def test_search_members_come_out_as_member_records():
    c = _client()
    out = c.search(SLUG, "mindy", kind="members")
    raw, = out["members"]
    rec = normalize.search_member(raw)
    assert rec["name"] == "mindy" and rec["role"] == "admin", rec
    assert rec["points"] == 7 and rec["level"] == 2, rec
    assert rec["member_id"] == "m1", "the membership id, needed to act on a member"


def test_search_refuses_what_skool_cannot_answer():
    c = _client()
    for kw in ({"query": "", "kind": "posts"}, {"query": "x", "kind": "courses"},
               {"query": "x", "kind": "posts", "page": 0}):
        try:
            c.search(SLUG, **kw)
        except ValueError:
            continue
        raise AssertionError(f"{kw} reached Skool")
    assert c.http.gets == []


def test_the_tools_name_the_category_and_count_the_hits():
    import catknows.mcp_server as m
    fn = lambda t: t.fn if hasattr(t, "fn") else t
    c = _client()
    old = m._get_client
    m._get_client = lambda: c
    try:
        posts = fn(m.list_posts)(SLUG, category="feedback", sort="new")
        assert any(("&c=" + "b" * 32) in q for q in c.http.gets), c.http.gets
        assert posts[0]["category"] == "" and "category_id" in posts[0], posts
        found = fn(m.search_community)(SLUG, "hello", page=2)
        assert found["total"] == 24 and found["pages"] == 3, found
        assert found["results"][0]["category"] == "📮 Feedback", found
        people = fn(m.search_community)(SLUG, "mindy", kind="members")
        assert people["results"][0]["member_id"] == "m1", people
    finally:
        m._get_client = old
    assert isinstance(posts, list)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all green")
