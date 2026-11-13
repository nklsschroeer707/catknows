"""Self-check: six existing calls match what Skool's own web client sends.

Skool's page code (build 1794436674711) was read call by call and compared with
what this client sent. Six calls differed, and each difference is a request the
real client never makes, so each is a way to stand out or to get less than the
website gets:

  1. vote-users: no `group-id`, and the website pages it (limit, tab, cursor)
  2. chat-channels: `unread-only` is a switch; `{"channels": null}` when empty
  3. categories: GET /groups/{gid}/labels on request (`via_api=True`), the feed
     page stays the default and the fallback
  4. DELETE: only deletePost carries a body (`{}`), deleteCourse and the rest none
  5. GET /courses/{id}: no `withChildren`
  6. GET /channels/{id}/messages: no `limit`

Everything here runs against fakes: no network, no Skool, no pytest.

Run: python test_client_alignment.py
"""

import os
import tempfile
from urllib.parse import parse_qsl, urlsplit

import catknows.client as cl
import catknows.mcp_server as m
from catknows import normalize
from catknows.client import SkoolClient
from catknows.http import SkoolHTTP, SkoolHTTPError

cl._INTER_PAGE_DELAY_S = 0
# The DELETE cases go through the real write path (gap, audit log): keep both
# out of the way, like test_write_audit.py does.
os.environ["CATKNOWS_WRITE_GAP_S"] = "0"
os.environ["CATKNOWS_AUDIT_LOG"] = os.path.join(tempfile.mkdtemp(), "writes.jsonl")


def _client(http) -> SkoolClient:
    c = SkoolClient.__new__(SkoolClient)
    c.http = http
    return c


def _query(path: str) -> list[tuple[str, str]]:
    """The query as an ordered list: the order is part of what is checked."""
    return parse_qsl(urlsplit(path).query, keep_blank_values=True)


def _with_tool_client(client, fn, *a, **kw):
    real = m._get_client
    m._get_client = lambda: client
    try:
        return fn(*a, **kw)
    finally:
        m._get_client = real


# -- 1. likes ------------------------------------------------------------------

def _liker(i: int) -> dict:
    return {"id": f"u{i}", "name": f"anna{i}", "first_name": "Anna", "last_name": f"Test{i}"}


class LikesHTTP:
    """vote-users as the contract describes it: pages of `limit`, an opaque
    `cursor` per page, `has_more` while people are left."""

    def __init__(self, total=25):
        self.total, self.calls = total, []

    def get_api2(self, path):
        self.calls.append(path)
        q = dict(_query(path))
        start = int(q["cursor"][1:]) if q.get("cursor") else 0
        end = min(start + int(q["limit"]), self.total)
        return {"tab": q["tab"], "users": [_liker(i) for i in range(start, end)],
                "has_more": end < self.total, "cursor": f"c{end}",
                "total_upvote_users": self.total, "total_downvote_users": 2}


def test_likes_page_is_one_call_in_the_contract_order():
    http = LikesHTTP()
    page = _client(http).likes_page("p1")
    assert http.calls == ["/posts/p1/vote-users?limit=10&tab=upvotes&cursor="], http.calls
    assert [k for k, _ in _query(http.calls[0])] == ["limit", "tab", "cursor"], http.calls
    assert len(page["users"]) == 10 and page["has_more"] is True, page
    assert page["total_upvote_users"] == 25, "the raw object comes back, totals included"
    assert page["cursor"] == "c10", page


def test_likes_page_passes_its_arguments_on():
    http = LikesHTTP()
    _client(http).likes_page("p1", limit=5, cursor="c10", tab="tab-x")
    assert http.calls == ["/posts/p1/vote-users?limit=5&tab=tab-x&cursor=c10"], http.calls
    # A cursor is opaque: whatever Skool sent goes back as one safe value.
    http = PathHTTP({"users": []})
    _client(http).likes_page("p1", cursor="a b/c=")
    assert http.calls[0].endswith("&cursor=a%20b%2Fc%3D"), http.calls


def test_likes_walks_every_page_and_never_sends_group_id():
    http = LikesHTTP(total=25)
    got = _client(http).likes("p1", "gid-123")  # second argument: still accepted
    assert [u["id"] for u in got] == [f"u{i}" for i in range(25)], [u["id"] for u in got]
    assert http.calls == [
        "/posts/p1/vote-users?limit=10&tab=upvotes&cursor=",
        "/posts/p1/vote-users?limit=10&tab=upvotes&cursor=c10",
        "/posts/p1/vote-users?limit=10&tab=upvotes&cursor=c20",
    ], http.calls
    assert not any("group-id" in p or "gid-123" in p for p in http.calls), http.calls


def test_likes_needs_no_group_id():
    http = LikesHTTP(total=3)
    assert len(_client(http).likes("p1")) == 3
    assert len(http.calls) == 1, "one page was enough"


def test_likes_stops_when_the_cursor_stands_still():
    class Stuck:
        def __init__(self):
            self.calls = []

        def get_api2(self, path):
            self.calls.append(path)
            return {"users": [_liker(len(self.calls))], "has_more": True, "cursor": "same"}

    http = Stuck()
    got = _client(http).likes("p1")
    assert len(http.calls) == 2, f"second answer repeats the cursor: stop. {http.calls}"
    assert len(got) == 2, got


def test_likes_standing_cursor_does_not_add_the_page_twice():
    """Skool re-serves the same page with the same cursor: the list holds each voter once."""
    class Repeats:
        def __init__(self):
            self.calls = []

        def get_api2(self, path):
            self.calls.append(path)
            return {"users": [_liker(1), _liker(2)], "has_more": True, "cursor": "same"}

    got = _client(Repeats()).likes("p1")
    ids = [u["id"] for u in got]
    assert ids == ["u1", "u2"], f"each voter once, in order: {ids}"


def test_likes_docstring_says_it_ends_quietly():
    doc = " ".join(SkoolClient.likes.__doc__.split())
    assert "max_pages" in doc and "without an error" in doc, doc


def test_likes_stops_at_max_pages():
    class Endless:
        def __init__(self):
            self.calls = []

        def get_api2(self, path):
            self.calls.append(path)
            return {"users": [_liker(len(self.calls))], "has_more": True,
                    "cursor": f"c{len(self.calls)}"}

    http = Endless()
    got = _client(http).likes("p1", max_pages=4)
    assert len(http.calls) == 4 and len(got) == 4, (len(http.calls), len(got))


def test_likes_stop_signals():
    """has_more false, empty page, empty cursor: each ends the walk on its own."""
    for page in ({"users": [_liker(1)], "has_more": False, "cursor": "c1"},
                 {"users": [], "has_more": True, "cursor": "c1"},
                 {"users": [_liker(1)], "has_more": True, "cursor": ""},
                 {"users": [_liker(1)], "has_more": True}):
        http = PathHTTP(page)
        _client(http).likes("p1")
        assert len(http.calls) == 1, (page, http.calls)


class FakeLikesClient:
    """What get_post_likes may call. No group_id_for: the tool must not need it."""

    def __init__(self):
        self.args = None

    def likes_page(self, post_id, *, limit, cursor, tab):
        self.args = (post_id, limit, cursor, tab)
        users = [_liker(1), {"id": "u2", "name": "bob", "first_name": "Bob",
                             "last_name": "Test", "metadata": {"payoutAccountId": "acct_x"}}]
        return {"users": users, "has_more": True, "cursor": "c2",
                "total_upvote_users": 25, "total_downvote_users": 3}


def test_get_post_likes_returns_one_page_with_the_way_on():
    fake = FakeLikesClient()
    out = _with_tool_client(fake, m.get_post_likes, "some-group", "p1")
    assert fake.args == ("p1", 10, "", "upvotes"), fake.args
    assert set(out) == {"likes", "has_more", "cursor", "total_upvotes", "total_downvotes"}, out
    assert out["likes"] == [normalize.like(_liker(1), "p1"),
                            normalize.like({"id": "u2", "name": "bob", "first_name": "Bob",
                                            "last_name": "Test"}, "p1")], out["likes"]
    assert out["has_more"] is True and out["cursor"] == "c2", out
    assert (out["total_upvotes"], out["total_downvotes"]) == (25, 3), out
    assert "acct_x" not in repr(out), "only names leave this tool"


def test_get_post_likes_forwards_paging_arguments():
    fake = FakeLikesClient()
    _with_tool_client(fake, m.get_post_likes, "some-group", "p1", limit=5, cursor="c2",
                      tab="tab-x")
    assert fake.args == ("p1", 5, "c2", "tab-x"), fake.args


def test_get_post_likes_last_page_without_totals():
    class Last:
        def likes_page(self, post_id, *, limit, cursor, tab):
            return {"users": [], "has_more": False}

    out = _with_tool_client(Last(), m.get_post_likes, "g", "p1")
    assert out == {"likes": [], "has_more": False, "cursor": "",
                   "total_upvotes": 0, "total_downvotes": 0}, out


# -- 2. chat channels ----------------------------------------------------------

class PathHTTP:
    """Answers every GET with `answer` and remembers the paths."""

    def __init__(self, answer=None):
        self.answer, self.calls = answer if answer is not None else {}, []

    def get_api2(self, path):
        self.calls.append(path)
        return self.answer


def test_chat_channels_default_query():
    http = PathHTTP({"channels": [{"id": "ch1"}]})
    out = _client(http).chat_channels()
    assert http.calls == ["/self/chat-channels?offset=0&limit=30&last=true&unread-only=false"], \
        http.calls
    assert out == {"channels": [{"id": "ch1"}]}, "the raw object comes back as it is"


def test_chat_channels_unread_only_switch():
    http = PathHTTP({"channels": [{"id": "ch1"}]})
    _client(http).chat_channels(offset=30, limit=10, unread_only=True)
    assert http.calls == ["/self/chat-channels?offset=30&limit=10&last=true&unread-only=true"], \
        http.calls


def test_chat_channels_null_becomes_an_empty_list():
    """Live: unread-only=true and nothing unread answers {"channels": null}."""
    http = PathHTTP({"channels": None, "other": 1})
    out = _client(http).chat_channels(unread_only=True)
    assert out["channels"] == [], out
    assert out["other"] == 1, "the rest of the object stays"


def test_list_chat_channels_tool_has_the_switch_and_never_returns_none():
    http = PathHTTP({"channels": None})
    out = _with_tool_client(_client(http), m.list_chat_channels, unread_only=True)
    assert http.calls == ["/self/chat-channels?offset=0&limit=30&last=true&unread-only=true"], \
        http.calls
    assert out["channels"] == [], out
    http = PathHTTP({"channels": [{"id": "ch1"}]})
    out = _with_tool_client(_client(http), m.list_chat_channels, offset=30, limit=10)
    assert http.calls == ["/self/chat-channels?offset=30&limit=10&last=true&unread-only=false"], \
        http.calls
    assert out["channels"] == [{"id": "ch1"}], out


# -- 3. categories: feed page by default, labels endpoint on request -----------

LABELS_API = {"labels": [
    {"id": "a" * 32, "group_id": "gid-1",
     "metadata": {"display_name": "🐽 General", "posts": 60, "color": "x"}},
    {"id": "b" * 32, "group_id": "gid-1",
     "metadata": {"display_name": "📮 Feedback", "posts": 17, "post_write_role": 0}},
]}
LABELS_PAGE = [
    {"id": "a" * 32, "metadata": {"displayName": "🐽 General", "posts": 60}},
    {"id": "b" * 32, "metadata": {"displayName": "📮 Feedback", "posts": 17}},
]
CATEGORIES = [{"id": "a" * 32, "name": "🐽 General", "posts": 60},
              {"id": "b" * 32, "name": "📮 Feedback", "posts": 17}]


class LabelsHTTP:
    def __init__(self, labels_error=None):
        self.api, self.next, self.labels_error = [], [], labels_error

    def get_api2(self, path):
        self.api.append(path)
        if path == "/groups/some-group":
            return {"id": "gid-1"}
        if path == "/groups/gid-1/labels":
            if self.labels_error is not None:
                raise self.labels_error
            return LABELS_API
        raise AssertionError(f"unexpected api2 call {path}")

    def get_next(self, q, slug):
        self.next.append(q)
        return {"pageProps": {"currentGroup": {"labels": LABELS_PAGE}}}


def test_categories_default_reads_only_the_feed_page():
    """Same single call as before the alignment: no group id, no labels."""
    for kw in ({}, {"via_api": False}):
        http = LabelsHTTP()
        cats = _client(http).post_categories("some-group", **kw)
        assert cats == CATEGORIES, cats
        assert http.api == [], f"the default must not ask api2 at all: {http.api}"
        assert http.next == ["/some-group.json?group=some-group"], http.next


def test_callers_in_this_repo_keep_the_default():
    http = LabelsHTTP()
    c = _client(http)
    assert c.resolve_category("some-group", "feedback") == "b" * 32
    assert http.api == [] and len(http.next) == 1, (http.api, http.next)


def test_via_api_is_keyword_only():
    try:
        _client(LabelsHTTP()).post_categories("some-group", True)
    except TypeError:
        return
    raise AssertionError("via_api must be passed by name")


def test_categories_come_from_the_labels_endpoint_on_request():
    http = LabelsHTTP()
    cats = _client(http).post_categories("some-group", via_api=True)
    assert cats == CATEGORIES, cats
    assert "/groups/gid-1/labels" in http.api, http.api
    assert http.next == [], "the feed page is only the fallback"


def test_categories_fall_back_to_the_feed_page_on_401_403_404():
    for status in (401, 403, 404):
        http = LabelsHTTP(SkoolHTTPError(f"HTTP {status}", status))
        cats = _client(http).post_categories("some-group", via_api=True)
        assert cats == CATEGORIES, (status, cats)
        assert http.next == ["/some-group.json?group=some-group"], (status, http.next)


def test_categories_other_errors_are_not_swallowed():
    for err in (SkoolHTTPError("HTTP 500", 500), SkoolHTTPError("HTTP 429", 429),
                SkoolHTTPError("network", 0)):
        http = LabelsHTTP(err)
        try:
            _client(http).post_categories("some-group", via_api=True)
        except SkoolHTTPError as e:
            assert e is err
        else:
            raise AssertionError(f"status {err.status} must not fall back silently")
        assert http.next == [], "no feed page after a real failure"


def test_categories_have_the_same_shape_on_all_ways():
    default = _client(LabelsHTTP()).post_categories("some-group")
    via_api = _client(LabelsHTTP()).post_categories("some-group", via_api=True)
    fallback = _client(LabelsHTTP(SkoolHTTPError("HTTP 404", 404))).post_categories(
        "some-group", via_api=True)
    assert default == via_api == fallback, (default, via_api, fallback)
    assert all(set(c) == {"id", "name", "posts"} for c in via_api), via_api


def test_categories_empty_or_missing_labels():
    class Empty(LabelsHTTP):
        def get_api2(self, path):
            if path == "/groups/gid-1/labels":
                return {"labels": None}
            return super().get_api2(path)

    assert _client(Empty()).post_categories("some-group", via_api=True) == []


# -- 4. DELETE: a body only for deletePost -------------------------------------

class RecordingTransport:
    """Stands in for curl_cffi: remembers each call with its keyword arguments."""

    class _Resp:
        status_code, text = 200, ""

    def __init__(self):
        self.calls = []

    def _any(self, method):
        def call(url, **kw):
            self.calls.append((method, url, kw))
            return self._Resp()
        return call

    def __getattr__(self, name):
        if name in ("get", "post", "put", "delete"):
            return self._any(name)
        raise AttributeError(name)


def _real_http():
    h = SkoolHTTP.__new__(SkoolHTTP)
    h.session = type("S", (), {"cookie_header": "c", "auth_token": "t", "waf_token": "w"})()
    h._cache = {}
    h._profile = {"ua": "ua", "lang": "en", "sec_ch_ua": "", "platform": ""}
    h._http = RecordingTransport()
    return h


def test_delete_api2_sends_a_body_only_when_given_one():
    h = _real_http()
    h.delete_api2("/things/1")
    h.delete_api2("/things/2", {})
    h.delete_api2("/things/3", {"feedback": "x"})
    (_, _, none), (_, _, empty), (_, _, full) = h._http.calls
    assert "json" not in none, none
    assert empty["json"] == {}, empty
    assert full["json"] == {"feedback": "x"}, full


def test_delete_post_sends_an_empty_json_body():
    c = _client(_real_http())
    c.delete_post("p1")
    (method, url, kw), = c.http._http.calls
    assert method == "delete" and url.endswith("/posts/p1"), (method, url)
    assert kw["json"] == {}, kw


def test_deleting_a_course_item_sends_no_body():
    c = _client(_real_http())
    c.delete_course_item("c1")
    c.delete_course_item("c2", client_id="0" * 32)
    for method, url, kw in c.http._http.calls:
        assert method == "delete", method
        assert "json" not in kw, f"deleteCourse carries no body: {url} {kw}"
    assert c.http._http.calls[1][1].endswith(f"/courses/c2?client_id={'0' * 32}")


# -- 5. course tree ------------------------------------------------------------

def test_course_tree_has_no_with_children():
    http = PathHTTP({"course": {"id": "c1"}, "children": []})
    out = _client(http).course_tree("c1")
    assert http.calls == ["/courses/c1"], http.calls
    assert out["course"]["id"] == "c1"


def test_course_tree_still_falls_back_to_the_classroom_page():
    class Refused:
        calls = []

        def get_api2(self, path):
            self.calls.append(path)
            raise SkoolHTTPError("HTTP 401 action not permitted", 401)

    http = Refused()
    http.calls = []
    c = _client(http)
    page_tree = {"course": {"id": "c1"}, "children": []}
    c._course_tree_via_page = lambda slug, cid: page_tree
    assert c.course_tree("c1", "some-group") is page_tree
    assert http.calls == ["/courses/c1"], http.calls


# -- 6. chat messages ----------------------------------------------------------

def _msg(i: int) -> dict:
    return {"id": f"m{i:03d}", "created_at": f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}Z"}


class ChatHTTP:
    """A channel of `total` messages. Opening read: the newest `before` (+1
    boundary, as live: before=30 gave 31). msg= cursor: a window around it."""

    def __init__(self, total):
        self.total, self.calls = total, []

    def get_api2(self, path):
        self.calls.append(path)
        q = dict(_query(path))
        if "msg" in q:
            at = int(q["msg"][1:])
            lo, hi = max(0, at - int(q["before"])), min(self.total, at + int(q["after"]) + 1)
        else:
            lo, hi = max(0, self.total - int(q["before"]) - 1), self.total
        return {"messages": [_msg(i) for i in range(lo, hi)],
                "has_more_before": lo > 0, "has_more_after": hi < self.total}


def test_chat_messages_sends_no_limit_and_keeps_at_most_count():
    http = ChatHTTP(total=200)
    out = _client(http).chat_messages("ch1", count=30)
    assert http.calls == ["/channels/ch1/messages?before=30"], http.calls
    assert len(out["messages"]) == 30, "31 came back, the caller asked for 30"
    assert out["messages"][-1]["id"] == "m199", "newest last"
    assert [m_["id"] for m_ in out["messages"]] == sorted(m_["id"] for m_ in out["messages"])


def test_chat_messages_walking_back_never_sends_limit():
    http = ChatHTTP(total=200)
    out = _client(http).chat_messages("ch1", count=120)
    ids = [m_["id"] for m_ in out["messages"]]
    assert len(ids) == 120 and len(set(ids)) == 120, len(ids)
    assert ids[-1] == "m199", ids[-1]
    assert http.calls[0] == "/channels/ch1/messages?before=50", http.calls
    assert len(http.calls) > 1, "more than one window was needed"
    assert not any("limit" in p for p in http.calls), http.calls
    assert all(p.startswith("/channels/ch1/messages?") for p in http.calls), http.calls


def test_chat_messages_walking_forward_never_sends_limit():
    class Unread(ChatHTTP):
        """Opening window ends at the read position, has_more_after says so."""

        def get_api2(self, path):
            q = dict(_query(path))
            if "msg" not in q:
                self.calls.append(path)
                return {"messages": [_msg(i) for i in range(0, 8)],
                        "has_more_before": False, "has_more_after": True}
            return super().get_api2(path)

    http = Unread(total=11)
    out = _client(http).chat_messages("ch1", count=10)
    assert [m_["id"] for m_ in out["messages"]][-1] == "m010", out["messages"]
    assert len(http.calls) >= 2 and not any("limit" in p for p in http.calls), http.calls


# -- runner --------------------------------------------------------------------

if __name__ == "__main__":
    tests = [(name, fn) for name, fn in globals().items()
             if name.startswith("test_") and callable(fn)]
    failed = []
    for name, fn in tests:
        try:
            fn()
        except Exception as e:  # keep going: one run lists every broken call
            failed.append(name)
            print(f"FAIL {name}: {type(e).__name__}: {str(e)[:200]}")
    if failed:
        raise SystemExit(f"{len(failed)} of {len(tests)} checks failed")
    print(f"ok — {len(tests)} checks: the six calls match Skool's own client")
