"""Self-check: an id from the caller cannot steer a request somewhere else.

Newer methods already sent every id through `_id_segment` (refuses blank text
and text made only of dots, percent-encodes everything else). Older ones put
the id into the path raw, so ``..`` let libcurl fold the path onto another
endpoint (``/posts/../pin`` becomes ``/pin``) and a ``/`` or ``?`` could add a
segment or a query. This file checks, method by method:

  * ``..``, ``.``, blank text: ``ValueError`` before any request,
  * ``/``, ``?``, ``&``, ``#``: encoded, so the id stays ONE segment or value,
  * real ids (32 hex) and slugs with hyphens: sent unchanged.

The same holds for the ``www`` paths (the Next.js data routes behind
``get_next``): community slug, member handle and post name come from the caller
and are checked the same way. Real slugs, handles and post names go out byte for
byte as before (the table ``WWW`` holds every expected path in full).

Plus the two guards of the read-POSTs: `post_api2_read` takes only the two
paths docs/API.md §5.13 names, and the download link must be https with a host.

Everything here runs against fakes: no network, no Skool, no pytest.

Run: python test_id_segments.py
"""

import os
import tempfile
from urllib.parse import urlsplit

os.environ["CATKNOWS_WRITE_GAP_S"] = "0"
os.environ.setdefault("CATKNOWS_AUDIT_LOG", os.path.join(tempfile.mkdtemp(), "w.jsonl"))

import catknows.client as cl
from catknows.client import SkoolClient
from catknows.http import SkoolHTTP, SkoolHTTPError

cl._INTER_PAGE_DELAY_S = 0

HEX = "0123456789abcdef" * 2
SLUG = "some-group-1234"


class RecordingHTTP:
    def __init__(self):
        self.calls = []
        self.next_slugs = []   # get_next's second argument: buildId page and Referer

    def build_id(self, slug):
        return "build"

    def get_api2(self, path):
        self.calls.append(("GET", path))
        if path.startswith("/groups/") and path.count("/") == 2:
            return {"id": HEX}
        return {"messages": [], "users": [], "post_tree": {"children": []}}

    def get_api2_text(self, path):
        self.calls.append(("TEXT", path))
        return "completed"

    def get_next(self, q, slug=""):
        self.calls.append(("NEXT", q))
        self.next_slugs.append(slug)
        if q.startswith("/@") or "/classroom" in q:
            # a profile and a classroom with one course, so the walks go on
            return {"pageProps": {"currentUser": {"id": HEX}, "course": {"id": HEX},
                                  "allCourses": [{"id": HEX, "name": "abc12345"}]}}
        return {}

    def _w(self, verb, path):
        self.calls.append((verb, path))
        return {}

    def post_api2(self, path, body):
        return self._w("POST", path)

    def put_api2(self, path, body):
        return self._w("PUT", path)

    def delete_api2(self, path, body=None):
        return self._w("DELETE", path)


def _client():
    c = SkoolClient.__new__(SkoolClient)
    c.http = RecordingHTTP()
    return c


# Each entry: name -> call(client, value). The value lands in exactly one path
# segment (or query value) of the first request.
CALLS = {
    "comments post": lambda c, v: c.comments(v, HEX),
    "comments group": lambda c, v: c.comments(HEX, v),
    "likes_page": lambda c, v: c.likes_page(v),
    "likes": lambda c, v: c.likes(v),
    "group_info": lambda c, v: c.group_info(v),
    "discovery_rank": lambda c, v: c.discovery_rank(v),
    "admin_metrics group": lambda c, v: c.admin_metrics(v),
    "admin_metrics range": lambda c, v: c.admin_metrics(HEX, range_=v),
    "growth_overview": lambda c, v: c.growth_overview(v),
    "analytics_chart": lambda c, v: c.analytics_chart(v, "members"),
    "decide_join_request": lambda c, v: c.decide_join_request(v, "approve"),
    "course_tree": lambda c, v: c.course_tree(v),
    "update_course_item": lambda c, v: c.update_course_item(v, {"title": "t"}),
    "set_course_state": lambda c, v: c.set_course_state(v, True),
    "move_course_item": lambda c, v: c.move_course_item(v, 0),
    "delete_course_item": lambda c, v: c.delete_course_item(v),
    "delete_course_item client_id": lambda c, v: c.delete_course_item(HEX, client_id=v),
    "chat_messages": lambda c, v: c.chat_messages(v),
    "post_by_id": lambda c, v: c.post_by_id(v),
    "update_post": lambda c, v: c.update_post(v, title="t"),
    "delete_post": lambda c, v: c.delete_post(v),
    "send_dm": lambda c, v: c.send_dm(v, "hi"),
    "group_id_for": lambda c, v: c.group_id_for(v),
    "create_comment post": lambda c, v: c.create_comment(SLUG, v, "hi"),
    "create_comment parent": lambda c, v: c.create_comment(
        SLUG, HEX, "hi", parent_comment_id=v, parent_is_top_level=True),
}

REFUSED = ("..", ".", "", "   ")
OPTIONAL = ("delete_course_item client_id", "create_comment parent")
ENCODED = {"a/b": "a%2Fb", "a?b=1": "a%3Fb%3D1", "a&b=1": "a%26b%3D1", "a#b": "a%23b",
           "../pin": "..%2Fpin"}


def _sent(c):
    return [p for _, p in c.http.calls]


def test_dots_and_blank_are_refused_before_any_request():
    for name, call in CALLS.items():
        for bad in REFUSED:
            if bad == "" and name in OPTIONAL:
                continue  # empty means "none given" there, which is allowed
            c = _client()
            try:
                call(c, bad)
            except ValueError:
                pass
            else:
                raise AssertionError(f"{name}: {bad!r} must be refused")
            assert c.http.calls == [], f"{name}: {bad!r} sent {c.http.calls}"


def test_slash_question_mark_and_friends_stay_inside_their_segment():
    for name, call in CALLS.items():
        for raw, enc in ENCODED.items():
            c = _client()
            call(c, raw)
            paths = _sent(c)
            if name == "create_comment post" or name == "create_comment parent":
                # the id goes into the JSON body, not a path: only the check runs
                assert all(raw not in p for p in paths), (name, paths)
                continue
            assert any(enc in p for p in paths), f"{name}: {raw!r} not encoded in {paths}"
            assert all(raw not in p for p in paths), f"{name}: {raw!r} went out raw: {paths}"


def test_real_ids_and_slugs_go_out_unchanged():
    for name, call in CALLS.items():
        value = SLUG if name == "group_id_for" else ("30d" if "range" in name else HEX)
        c = _client()
        call(c, value)
        if name.startswith("create_comment"):
            continue
        assert any(value in p for p in _sent(c)), f"{name}: {value} missing in {_sent(c)}"


def test_likes_tab_is_encoded():
    c = _client()
    c.likes_page(HEX, tab="upvotes&cursor=x")
    (_, path), = c.http.calls
    assert "tab=upvotes%26cursor%3Dx&" in path, path


# -- www paths (get_next): slug, handle and post name ---------------------------

HANDLE = "test.person-1234"
POST = "hello-world-2"
S, H, P = SLUG, HANDLE, POST

# name -> (call(client, slug, handle, post), the slots the call uses,
#          every get_next path it sends for the real values, in full)
WWW = {
    "members": (lambda c, s, h, p: c.members(s), "s",
                [f"/{S}/-/members.json?sortType=-memberlastoffline&group={S}"]),
    "posts": (lambda c, s, h, p: c.posts(s), "s", [f"/{S}.json?group={S}"]),
    "posts sorted": (lambda c, s, h, p: c.posts(s, sort="new"), "s",
                     [f"/{S}.json?group={S}&s=newest"]),
    "resolve_member": (lambda c, s, h, p: c.resolve_member(s, h), "sh",
                       [f"/@{H}.json?g={S}&group=@{H}"]),
    "posts_by": (lambda c, s, h, p: c.posts_by(s, h), "sh",
                 [f"/@{H}.json?g={S}&group=@{H}", f"/@{H}.json?g={S}&group=@{H}"]),
    "post_categories": (lambda c, s, h, p: c.post_categories(s), "s", [f"/{S}.json?group={S}"]),
    "search": (lambda c, s, h, p: c.search(s, "cat food"), "s",
               [f"/{S}/-/search.json?q=cat%20food&t=posts&group={S}"]),
    "post_detail": (lambda c, s, h, p: c.post_detail(s, p), "sp", [f"/{S}/{P}.json?group={S}"]),
    "video_transcript": (lambda c, s, h, p: c.video_transcript(s, p), "sp",
                         [f"/{S}/{P}.json?group={S}"]),
    "profile": (lambda c, s, h, p: c.profile(h, s), "sh", [f"/@{H}.json?g={S}&group=@{H}"]),
    "follows": (lambda c, s, h, p: c.follows(h), "h", [f"/@{H}.json?t=followers&group=@{H}"]),
    "follows following": (lambda c, s, h, p: c.follows(h, "following"), "h",
                          [f"/@{H}.json?t=following&group=@{H}"]),
    "community_about": (lambda c, s, h, p: c.community_about(s), "s",
                        [f"/{S}/about.json?group={S}"]),
    "leaderboards": (lambda c, s, h, p: c.leaderboards(s), "s",
                     [f"/{S}/-/leaderboards.json?group={S}"]),
    "join_requests": (lambda c, s, h, p: c.join_requests(s), "s",
                      [f"/{S}/-/pending.json?group={S}"]),
    "join_requests page 2": (lambda c, s, h, p: c.join_requests(s, page=2), "s",
                             [f"/{S}/-/pending.json?group={S}&p=2"]),
    "calendar": (lambda c, s, h, p: c.calendar(s), "s", [f"/{S}/calendar.json?group={S}"]),
    "calendar month": (lambda c, s, h, p: c.calendar(s, 1700000000), "s",
                       [f"/{S}/calendar.json?calDate=1700000000&group={S}"]),
    "classroom": (lambda c, s, h, p: c.classroom(s), "s", [f"/{S}/classroom.json?group={S}"]),
    "course tree via page": (lambda c, s, h, p: c._course_tree_via_page(s, HEX), "s",
                             [f"/{S}/classroom.json?group={S}",
                              f"/{S}/classroom/abc12345.json?group={S}"]),
}

WWW_ENCODED = dict(ENCODED, **{"a b": "a%20b", "a\nb": "a%0Ab", "a\x00b": "a%00b", "a%2Fb": "a%252Fb"})


def _next_paths(c):
    return [p for kind, p in c.http.calls if kind == "NEXT"]


def _shape(q):
    """What a value must not change: number of path pieces, query names, no fragment."""
    u = urlsplit("https://www.example.com" + q)
    return (u.path.count("/"), [kv.partition("=")[0] for kv in u.query.split("&")], u.fragment)


def _values(slot, value):
    return {"s": SLUG, "h": HANDLE, "p": POST, slot: value}


def test_www_real_slug_handle_and_post_name_go_out_byte_for_byte():
    for name, (call, _slots, expected) in WWW.items():
        c = _client()
        call(c, SLUG, HANDLE, POST)
        assert _next_paths(c) == expected, f"{name}: {_next_paths(c)}"
        assert c.http.calls == [("NEXT", p) for p in expected], f"{name}: {c.http.calls}"
        assert set(c.http.next_slugs) <= {SLUG, ""}, f"{name}: {c.http.next_slugs}"


def test_www_slash_question_mark_and_friends_stay_inside_their_piece():
    for name, (call, slots, expected) in WWW.items():
        for slot in slots:
            for raw, enc in WWW_ENCODED.items():
                if name == "resolve_member" and slot == "h" and " " in raw:
                    continue  # with a space it is a display name: the search, not the handle route
                c = _client()
                v = _values(slot, raw)
                call(c, v["s"], v["h"], v["p"])
                paths = _next_paths(c)
                where = f"{name} [{slot}={raw!r}]"
                assert paths, f"{where}: nothing was sent"
                assert [_shape(q) for q in paths] == [_shape(q) for q in expected[:len(paths)]], \
                    f"{where}: the request left its path or query: {paths}"
                assert all(enc in q for q in paths), f"{where}: not encoded in {paths}"
                if raw != "a%2Fb":      # that one is a part of its own encoded form
                    assert all(raw not in q for q in paths), f"{where}: went out raw: {paths}"
                for slug in c.http.next_slugs:   # the buildId page and the Referer
                    assert not set(slug) & set("/?#& \n\x00"), f"{where}: slug argument {slug!r}"


def test_www_dots_and_blank_are_refused_before_any_request():
    for name, (call, slots, _expected) in WWW.items():
        for slot in slots:
            for bad in REFUSED:
                c = _client()
                v = _values(slot, bad)
                try:
                    call(c, v["s"], v["h"], v["p"])
                except ValueError as e:
                    assert bad.strip() == "" or f"'{bad}'" not in str(e)
                else:
                    raise AssertionError(f"{name} [{slot}={bad!r}] must be refused")
                assert c.http.calls == [], f"{name} [{slot}={bad!r}] sent {c.http.calls}"


def test_group_id_for_fallback_reads_the_feed_of_the_checked_slug():
    """`group_id_for` falls back to the feed page: the same slug, checked once."""
    c = _client()
    c.http.get_api2 = lambda path: {}
    c.http.get_next = lambda q, slug="": (
        c.http.calls.append(("NEXT", q)) or {"pageProps": {"postTrees": [{"post": {"id": "p", "groupId": HEX}}]}})
    assert c.group_id_for(SLUG) == HEX
    assert c.http.calls == [("NEXT", f"/{SLUG}.json?group={SLUG}")], c.http.calls


# -- post_api2_read takes only the two documented read-POST paths --------------

class _Net:
    def __init__(self):
        self.calls = []

    def post(self, url, **kw):
        self.calls.append(url)
        return type("R", (), {"status_code": 200, "text": '{"users": []}'})()


def _http():
    h = SkoolHTTP.__new__(SkoolHTTP)
    h.session = type("S", (), {"cookie_header": "c", "auth_token": "t", "waf_token": "w"})()
    h._cache = {}
    h._profile = {"ua": "ua", "lang": "en", "sec_ch_ua": "", "platform": ""}
    h._http = _Net()
    return h


def test_post_api2_read_accepts_the_two_documented_paths():
    for path in ("/search/users", f"/files/{HEX}/download-url",
                 f"/files/{HEX}/download-url?expire=60"):
        h = _http()
        h.post_api2_read(path, None)
        assert len(h._http.calls) == 1, path


def test_post_api2_read_refuses_every_other_path_before_sending():
    for path in ("/posts/p1/update", "/search/users?cursor=x", "/search/users/x",
                 "/files/../download-url", "/files/./download-url", "/files//download-url",
                 "/files/a/b/download-url", f"/files/{HEX}/download-url?expire=60&x=1",
                 f"/files/{HEX}/download-url?expire=", f"/files/{HEX}/download-url#x",
                 "/self/chat-channels/read", "search/users", ""):
        h = _http()
        try:
            h.post_api2_read(path, None)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{path!r} must be refused")
        assert h._http.calls == [], (path, h._http.calls)


# -- the download link must be https with a host --------------------------------

def _link_client(answer):
    c = SkoolClient.__new__(SkoolClient)
    c.http = type("H", (), {"post_api2_read": lambda self, p, b=None: answer})()
    return c


def test_download_link_needs_a_host():
    for bad in ("https:///x", "https://?a=1", "https://#frag", "https://@/x", "https://:443/x"):
        try:
            _link_client(bad).file_download_url(HEX)
        except SkoolHTTPError as e:
            assert bad not in str(e), "the answer must not be shown"
        else:
            raise AssertionError(f"{bad!r} is not a link with a host")


def test_download_link_with_a_host_passes():
    link = "https://files.example.com/f/abc?sig=1"
    assert _link_client(link).file_download_url(HEX) == link


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in globals().items()
             if name.startswith("test_") and callable(fn)]
    failed = []
    for name, fn in tests:
        try:
            fn()
        except Exception as e:  # keep going: one run lists every gap
            failed.append(name)
            print(f"FAIL {name}: {type(e).__name__}: {str(e)[:300]}")
    if failed:
        raise SystemExit(f"{len(failed)} of {len(tests)} checks failed")
    print(f"ok — {len(tests)} checks: ids stay in their segment, read-POSTs and links are checked")
