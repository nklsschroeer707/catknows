"""Self-check: chat read marks, the member search and the file download link.

Nothing here touches Skool. The client talks to a stand-in that records every
call, and the HTTP layer (for the wire, audit and gap tests) to a stand-in
transport. All names, addresses and links below are made up.

The four calls (basis https://api2.skool.com). Source: Skool's own page code
(build 1794436674711), read from the plan notes `vertraege.md` (rows
markChatMessageRead, markAllChatMessageRead, searchUsers, getResourceUrl), the
catalogue (`skool-re-api-katalog-rest`, sections /channels, /search, /files,
/self), the function maps (`skool-re-funktionskarte-chat-live` section 10,
`-inhalte` section 3, `-mitglieder` A2.3) and the page code itself
(`static_chunks_8402`, `static_chunks_pages__app`). Never run live.

    markChatMessageRead      POST /channels/{id}/read?last={last}   no body
        `last`: the page code passes the id of the chat message that just came
        into view (8402: `markChatMessageRead({channelId, last: message.id})`),
        and NO `last` when the newest message is in view or when a chat is
        marked read from the channel list. catknows sends the text as given and
        leaves the query out when none is given. What the server does with it
        was not run.
    markAllChatMessageRead   POST /self/chat-channels/read          no query, no body
    searchUsers              POST /search/users                     {query, group_id, limit: 7}
        Reads only, although it is a POST. The page's @mention search sends
        query, group id and limit (5 on a phone, 7 on desktop) and never a
        cursor (JSON.stringify drops the undefined key). catknows sends only
        that one documented desktop form: the body is always exactly
        {query, group_id, limit: 7}, with no cursor and no other limit. What a
        cursor or another limit does is not documented (contracts, not
        guesses), and paging a search would be a detour around the member-list
        limit (LEGAL.md boundary 4). The tool's `limit` only shortens the
        OUTPUT (1 to 7). The answer is `{users: [...]}` in the page code;
        whatever else it carries is not documented.
    getResourceUrl           POST /files/{id}/download-url?expire={n}   no body
        The page's own name for `expire` is `expireSecs` (28800 for chat
        attachments, none for a course attachment and for an export file). The
        answer is used as the link itself (`window.location = data`).

Deliberately NOT asserted, because the notes do not say: what Skool answers to
the two marks, the unit and the allowed range of `expire`, whether the answer
to getResourceUrl is JSON or plain text (both are read), what else the member
search answers, whether the other person sees a read mark.

Run: python test_chat_search_files.py    (no network, no pytest)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ["CATKNOWS_ALLOW_WRITE"] = "1"  # the two write tools only exist with this
os.environ["CATKNOWS_WRITE_GAP_S"] = "0"  # not a test of the write gap (the gap test sets its own)

from curl_cffi.requests.exceptions import RequestException

from catknows import audit, normalize
from catknows import http as http_mod
from catknows.client import SkoolClient
from catknows.http import SkoolHTTP, SkoolHTTPError, SkoolSecurityChallenge

SLUG = "some-group"
GID = "gid-1"
LINK = "https://files.example.com/f/9?sig=SECRET-SIG-123"
WRITE_KINDS = ("POST", "PUT", "DELETE")


class _FakeHTTP:
    """Records every call of every kind. A write is POST/PUT/DELETE, a read POST is POST-READ."""

    def __init__(self, group=None, answer=None, read_answer=None):
        self.calls = []
        self.group = {"id": GID} if group is None else group
        self.answer = {"ok": True} if answer is None else answer
        self.read_answer = {"users": []} if read_answer is None else read_answer

    @property
    def writes(self):
        return [c for c in self.calls if c[0] in WRITE_KINDS]

    @property
    def read_posts(self):
        return [c for c in self.calls if c[0] == "POST-READ"]

    def get_api2(self, path):
        self.calls.append(("GET", path, None))
        return self.group

    def post_api2(self, path, body):
        self.calls.append(("POST", path, body))
        return self.answer

    def post_api2_read(self, path, body=None):
        self.calls.append(("POST-READ", path, body))
        return self.read_answer


class _ChallengedHTTP(_FakeHTTP):
    """Skool answers every POST with its security question (AUTH-SEC)."""

    def _ask(self, kind, path):
        self.calls.append((kind, path, None))
        raise SkoolSecurityChallenge("AUTH-SEC-501", "POST", path)

    def post_api2(self, path, body):
        self._ask("POST", path)

    def post_api2_read(self, path, body=None):
        self._ask("POST-READ", path)


def _client(http=None):
    c = SkoolClient.__new__(SkoolClient)
    c.http = http or _FakeHTTP()
    return c


def _refused(call):
    try:
        call()
    except ValueError as e:
        return str(e)
    raise AssertionError("expected a ValueError")


def _fn(tool):
    return tool.fn if hasattr(tool, "fn") else tool


def _with_client(c, call):
    import catknows.mcp_server as m
    old = m._get_client
    m._get_client = lambda: c
    try:
        return call(m)
    finally:
        m._get_client = old


# -- client: chat read marks ----------------------------------------------------

def test_mark_chat_read_posts_to_the_channel_without_body_or_query():
    c = _client()
    out = c.mark_chat_read("ch1")
    assert out == {"ok": True}, "the client hands back Skool's answer as it is"
    assert c.http.calls == [("POST", "/channels/ch1/read", None)], c.http.calls


def test_mark_chat_read_puts_last_in_the_query_as_given():
    c = _client()
    c.mark_chat_read("ch1", "msg-7")
    c.mark_chat_read("ch1", last="1700000000123")  # whatever text the caller holds, unchanged
    assert [x[1] for x in c.http.calls] == [
        "/channels/ch1/read?last=msg-7",
        "/channels/ch1/read?last=1700000000123",
    ], c.http.calls
    assert all(x[0] == "POST" and x[2] is None for x in c.http.calls), c.http.calls


def test_chat_ids_are_encoded_into_the_path_and_the_query():
    c = _client()
    c.mark_chat_read("a b/c", "x&y=z?w")
    assert c.http.calls[0][1] == "/channels/a%20b%2Fc/read?last=x%26y%3Dz%3Fw", c.http.calls


def test_mark_all_chats_read_posts_without_body_or_query():
    c = _client()
    out = c.mark_all_chats_read()
    assert out == {"ok": True}
    assert c.http.calls == [("POST", "/self/chat-channels/read", None)], c.http.calls


def test_an_empty_or_odd_chat_id_is_refused_before_any_call():
    for bad in ("", "  ", None, 7, ["ch1"]):
        c = _client()
        _refused(lambda: c.mark_chat_read(bad))
        _refused(lambda: c.mark_chat_read("ch1", bad if bad is not None else ""))
        assert c.http.calls == [], (bad, c.http.calls)


def test_ids_made_only_of_dots_are_refused_before_any_call():
    """Dots stay dots when percent-encoded, and libcurl folds `/channels/../read` to `/read`."""
    for bad in (".", "..", "...", "....."):
        c = _client()
        _refused(lambda: c.mark_chat_read(bad))
        _refused(lambda: c.mark_chat_read("ch1", bad))
        assert c.http.calls == [], (bad, c.http.calls)
    c = _client()
    c.mark_chat_read("a.b", ".a")  # a dot between or in front of other characters is an ordinary id
    assert c.http.calls[0][1] == "/channels/a.b/read?last=.a", c.http.calls


def test_the_chat_id_refusals_name_the_argument_not_the_value():
    msg = _refused(lambda: _client().mark_chat_read(".."))
    assert "channel_id" in msg and ".." not in msg, msg
    msg = _refused(lambda: _client().mark_chat_read("ch1", ""))
    assert "last" in msg, msg
    msg = _refused(lambda: _client().mark_chat_read(["secret-id-value"]))
    assert "secret-id-value" not in msg and "channel_id" in msg, msg


def test_a_security_question_on_a_mark_goes_through_and_is_never_retried():
    for call in (lambda c: c.mark_chat_read("ch1"), lambda c: c.mark_chat_read("ch1", "m1"),
                 lambda c: c.mark_all_chats_read()):
        c = _client(_ChallengedHTTP())
        try:
            call(c)
        except SkoolSecurityChallenge as e:
            assert e.code == "AUTH-SEC-501", e
        else:
            raise AssertionError("the security question was swallowed")
        assert len(c.http.writes) == 1, f"a second try was sent: {c.http.writes}"


# -- client: member search ------------------------------------------------------

def test_search_members_posts_query_group_id_and_limit_through_the_read_channel():
    c = _client(_FakeHTTP(read_answer={"users": [{"id": "u1"}]}))
    out = c.search_members(SLUG, "Test Person")
    assert out == {"users": [{"id": "u1"}]}, "the client hands back Skool's answer as it is"
    assert c.http.calls == [
        ("GET", f"/groups/{SLUG}", None),  # group_id_for: slug -> uuid, no archived guard (a read)
        ("POST-READ", "/search/users", {"query": "Test Person", "group_id": GID, "limit": 7}),
    ], c.http.calls


def test_search_members_always_sends_exactly_the_documented_desktop_body():
    c = _client()
    c.search_members(SLUG, "x")
    c.search_members(SLUG, "  padded query  ")
    assert [x[2] for x in c.http.read_posts] == [
        {"query": "x", "group_id": GID, "limit": 7},
        {"query": "  padded query  ", "group_id": GID, "limit": 7},
    ], c.http.read_posts  # the page's desktop form: limit 7 and no cursor, nothing else


def test_search_members_has_no_cursor_and_no_limit_parameter():
    import inspect
    assert list(inspect.signature(SkoolClient.search_members).parameters) == [
        "self", "community_slug", "query"], "the one documented request has nothing to vary"
    for kw in (dict(cursor="next-1"), dict(cursor=""), dict(limit=5), dict(limit=7),
               dict(limit=500)):
        c = _client()
        try:
            c.search_members(SLUG, "x", **kw)
        except TypeError:
            pass
        else:
            raise AssertionError(f"the client accepted {kw}: there must be no such parameter")
        assert c.http.calls == [], f"a refused search must cause no call at all: {kw}"


def test_search_members_never_uses_the_write_channel():
    c = _client()
    c.search_members(SLUG, "x")
    assert c.http.writes == [], "a search is a read: no write call, so no audit line and no gap"


def test_search_members_refuses_odd_input_before_any_call():
    for bad in ("", "   ", None, 5, ["a"]):
        c = _client()
        _refused(lambda: c.search_members(SLUG, bad))
        assert c.http.calls == [], f"a refused search must cause no call at all: {bad!r}"


def test_the_search_refusal_names_the_argument():
    msg = _refused(lambda: _client().search_members(SLUG, "  "))
    assert "query" in msg, msg


def test_a_search_answer_that_is_no_object_is_an_error_without_the_answer():
    for odd in ("plain text with a@b.example", ["u1"], 7, ""):
        c = _client(_FakeHTTP(read_answer=odd))
        try:
            c.search_members(SLUG, "x")
        except SkoolHTTPError as e:
            assert "a@b.example" not in str(e), e
        else:
            raise AssertionError(f"an answer that is no object must not pass: {odd!r}")


def test_a_security_question_on_the_search_goes_through_and_is_never_retried():
    c = _client(_ChallengedHTTP())
    try:
        c.search_members(SLUG, "x")
    except SkoolSecurityChallenge as e:
        assert e.code == "AUTH-SEC-501", e
    else:
        raise AssertionError("the security question was swallowed")
    assert len(c.http.read_posts) == 1, f"a second try was sent: {c.http.calls}"


# -- client: file download link -------------------------------------------------

# Answers a plain-text or JSON-string POST can carry that are NOT a download link: a
# WAF challenge or login page (HTML, HTTP 200), a progress word, a non-https link, a
# link with white space or a line break in it, several lines. Each holds a marker that
# must never show up in an error message.
NOT_A_LINK = (
    "<html><body>challenge SECRET-PAGE-TEXT</body></html>",
    "<!DOCTYPE html>\n<title>Log in</title> SECRET-PAGE-TEXT",
    "in-progress",
    "completed",
    "http://files.example.com/f/9?sig=SECRET-SIG-123",
    "ftp://files.example.com/f/9?sig=SECRET-SIG-123",
    "//files.example.com/f/9?sig=SECRET-SIG-123",
    "files.example.com/f/9?sig=SECRET-SIG-123",
    "HTTPS://files.example.com/f/9?sig=SECRET-SIG-123",
    "https://",
    "https://files.example.com/f/9 ?sig=SECRET-SIG-123",
    "https://files.example.com/f/9\t?sig=SECRET-SIG-123",
    "https://files.example.com/f/9\n?sig=SECRET-SIG-123",
    "https://files.example.com/f/9?sig=SECRET-SIG-123\nhttps://other.example.com/x",
    "see https://files.example.com/f/9?sig=SECRET-SIG-123",
)
NOT_SHOWN = ("SECRET-PAGE-TEXT", "SECRET-SIG", "files.example.com", "challenge", "in-progress",
             "completed", "<html", "Log in")


def _not_shown(text):
    return not any(mark in text for mark in NOT_SHOWN)

def test_download_url_posts_without_body_and_without_query_by_default():
    c = _client(_FakeHTTP(read_answer=LINK))
    out = c.file_download_url("f1")
    assert out == LINK, out
    assert c.http.calls == [("POST-READ", "/files/f1/download-url", None)], c.http.calls
    assert c.http.writes == [], "asking for a link is a read"


def test_download_url_puts_expire_in_the_query_only_when_given():
    c = _client(_FakeHTTP(read_answer=LINK))
    c.file_download_url("f1", expire=3600)
    c.file_download_url("f1", expire=None)
    assert [x[1] for x in c.http.calls] == [
        "/files/f1/download-url?expire=3600", "/files/f1/download-url"], c.http.calls


def test_download_url_expire_must_be_a_whole_positive_number():
    """The unit and the allowed range are open; only what cannot be a number is refused."""
    for bad in (0, -1, True, False, "60", "", 1.5, [60]):
        c = _client(_FakeHTTP(read_answer=LINK))
        _refused(lambda: c.file_download_url("f1", expire=bad))
        assert c.http.calls == [], (bad, c.http.calls)


def test_download_url_id_is_checked_and_encoded():
    for bad in ("", "  ", None, 7, ".", "..", "...", ["f1"]):
        c = _client(_FakeHTTP(read_answer=LINK))
        _refused(lambda: c.file_download_url(bad))
        assert c.http.calls == [], (bad, c.http.calls)
    c = _client(_FakeHTTP(read_answer=LINK))
    c.file_download_url("a b/c?x=1")
    assert c.http.calls[0][1] == "/files/a%20b%2Fc%3Fx%3D1/download-url", c.http.calls
    msg = _refused(lambda: _client().file_download_url(".."))
    assert "file_id" in msg and ".." not in msg, msg


def test_download_url_returns_the_link_trimmed():
    c = _client(_FakeHTTP(read_answer=f"  {LINK}\n"))
    assert c.file_download_url("f1") == LINK


def test_an_answer_that_is_no_link_is_an_error_that_does_not_show_the_answer():
    """A dict could carry the link under a key nobody documented: it is not shown either."""
    for odd in ({"url": LINK}, {"data": {"u": LINK}}, [LINK], {}, "", "   ", None, 7):
        c = _client(_FakeHTTP(read_answer=odd))
        try:
            c.file_download_url("f1")
        except SkoolHTTPError as e:
            assert "SECRET-SIG" not in str(e) and "files.example.com" not in str(e), e
        else:
            raise AssertionError(f"an answer that is no link must not pass: {odd!r}")


def test_only_one_https_text_without_white_space_counts_as_a_link():
    """HTML (a challenge or login page), `in-progress`, `http://`, white space: all refused."""
    for odd in NOT_A_LINK:
        c = _client(_FakeHTTP(read_answer=odd))
        try:
            out = c.file_download_url("f1")
        except SkoolHTTPError as e:
            assert _not_shown(str(e)), f"the answer was shown for {odd!r}: {e}"
        else:
            raise AssertionError(f"{odd!r} must not pass as a link, got {out!r}")
        assert len(c.http.read_posts) == 1, "refused after the one call, never asked again"


def test_a_good_https_link_passes_unchanged_and_only_the_edges_are_trimmed():
    for good in (LINK, "https://a.example/x", "https://cdn.example.com/p/f.pdf?a=1&b=2#frag"):
        for wrapped in (good, f"  {good}\n", f"\t{good}  "):
            c = _client(_FakeHTTP(read_answer=wrapped))
            assert c.file_download_url("f1") == good, wrapped


def test_a_security_question_on_the_download_link_goes_through_and_is_never_retried():
    c = _client(_ChallengedHTTP())
    try:
        c.file_download_url("f1", expire=60)
    except SkoolSecurityChallenge as e:
        assert e.code == "AUTH-SEC-501" and "expire" not in str(e), e
    else:
        raise AssertionError("the security question was swallowed")
    assert len(c.http.read_posts) == 1, f"a second try was sent: {c.http.calls}"


# -- the real HTTP layer: the read POST -----------------------------------------

class _Resp:
    def __init__(self, code, text):
        self.status_code, self.text = code, text


class _Transport:
    """Stands in for curl_cffi: the layer under test is ours, the wire is not."""

    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def _record(self, verb):
        def send(url, **kw):
            self.calls.append((verb, url, kw))
            if isinstance(self.resp, Exception):
                raise self.resp
            return self.resp
        return send

    def __getattr__(self, name):
        if name in ("get", "post", "put", "delete"):
            return self._record(name.upper())
        raise AttributeError(name)


def _real_http(resp) -> SkoolHTTP:
    h = SkoolHTTP.__new__(SkoolHTTP)
    h.session = type("S", (), {"cookie_header": "c", "auth_token": "t", "waf_token": "w"})()
    h._cache = {}
    h._profile = {"ua": "ua", "lang": "en", "sec_ch_ua": "", "platform": ""}
    h._http = _Transport(resp)
    return h


def _real_client(resp=None):
    c = SkoolClient.__new__(SkoolClient)
    c.http = _real_http(resp or _Resp(200, json.dumps({"users": []})))
    c.group_id_for = lambda slug, for_write=False: GID  # no read of Skool here
    return c


class _AuditLog:
    """Points the audit log at a fresh file for one test."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.prev = os.environ.get("CATKNOWS_AUDIT_LOG")
        os.environ["CATKNOWS_AUDIT_LOG"] = str(Path(self.tmp.name) / "w.jsonl")
        return self

    @property
    def lines(self):
        p = audit.log_path()
        return [json.loads(x) for x in p.read_text("utf-8").splitlines()] if p.exists() else []

    def __exit__(self, *exc):
        if self.prev is None:
            os.environ.pop("CATKNOWS_AUDIT_LOG", None)
        else:
            os.environ["CATKNOWS_AUDIT_LOG"] = self.prev
        self.tmp.cleanup()


def test_the_read_post_sends_a_json_post_with_the_api2_headers():
    h = _real_http(_Resp(200, json.dumps({"users": []})))
    out = h.post_api2_read("/search/users", {"query": "x", "group_id": GID, "limit": 7})
    assert out == {"users": []}
    (verb, url, kw), = h._http.calls
    assert (verb, url) == ("POST", "https://api2.skool.com/search/users"), (verb, url)
    assert kw["json"] == {"query": "x", "group_id": GID, "limit": 7}, kw
    hd = kw["headers"]
    assert hd["Authorization"] == "Bearer t" and hd["x-aws-waf-token"] == "w", hd
    assert hd["Origin"] == "https://www.skool.com" and hd["Cookie"] == "c", hd


def test_the_read_post_without_a_body_sends_no_json():
    h = _real_http(_Resp(200, json.dumps(LINK)))
    assert h.post_api2_read("/files/f1/download-url?expire=60") == LINK
    (_, url, kw), = h._http.calls
    assert url == "https://api2.skool.com/files/f1/download-url?expire=60", url
    assert "json" not in kw, kw


def test_the_read_post_reads_json_objects_json_texts_plain_text_and_nothing():
    cases = [
        (json.dumps({"users": [{"id": "u1"}]}), {"users": [{"id": "u1"}]}),
        (json.dumps(LINK), LINK),            # a JSON string
        (f"{LINK}\n", LINK),                 # plain text, as `c.text()` gives it in Skool's client
        ("", {}),
        ("  \n", {}),
    ]
    for text, want in cases:
        h = _real_http(_Resp(200, text))
        assert h.post_api2_read("/files/f1/download-url") == want, (text, want)


def test_the_read_post_leaves_no_audit_line():
    with _AuditLog() as log:
        h = _real_http(_Resp(200, json.dumps({"users": []})))
        h.post_api2_read("/search/users", {"query": "Test Person", "group_id": GID, "limit": 7})
        h2 = _real_http(_Resp(500, "boom"))
        try:
            h2.post_api2_read("/files/f1/download-url")
        except SkoolHTTPError:
            pass
        assert log.lines == [], f"a read must not write to the write-audit log: {log.lines}"
        assert not audit.log_path().exists(), "not even an empty file"


def test_the_read_post_is_not_spaced_and_does_not_count_as_a_write():
    class _Clock:
        now = 1000.0
        slept: list = []

        def monotonic(self):
            return self.now

        def sleep(self, s):
            self.slept.append(s)
            self.now += s

    clock = _Clock()
    old = (http_mod._clock, http_mod._sleep, os.environ.get("CATKNOWS_WRITE_GAP_S"))
    http_mod._clock, http_mod._sleep = clock.monotonic, clock.sleep
    os.environ["CATKNOWS_WRITE_GAP_S"] = "15"
    http_mod._last_write_at.clear()
    try:
        with _AuditLog():
            h = _real_http(_Resp(200, json.dumps({"id": "x"})))
            h.post_api2("/posts", {})                     # a real write: the gap now runs
            stamp = dict(http_mod._last_write_at)
            h.post_api2_read("/search/users", {"query": "x", "group_id": GID, "limit": 7})
            h.post_api2_read("/files/f1/download-url")
            assert clock.slept == [], f"a read POST waited for the write gap: {clock.slept}"
            assert http_mod._last_write_at == stamp, "a read POST must not restart the write gap"
            h.post_api2("/posts", {})                     # the next write still waits
            assert clock.slept == [15], clock.slept
    finally:
        http_mod._clock, http_mod._sleep = old[0], old[1]
        if old[2] is None:
            os.environ.pop("CATKNOWS_WRITE_GAP_S", None)
        else:
            os.environ["CATKNOWS_WRITE_GAP_S"] = old[2]
        http_mod._last_write_at.clear()


def test_the_read_post_does_not_clear_the_read_cache():
    h = _real_http(_Resp(200, json.dumps({"users": []})))
    h._cache["https://api2.skool.com/groups/x"] = (1.0, {"id": "g"})
    h.post_api2_read("/search/users", {"query": "x", "group_id": GID, "limit": 7})
    assert "https://api2.skool.com/groups/x" in h._cache, "only a write makes cached reads stale"
    assert len(h._cache) == 1, "and its own answer is never cached"


def test_the_read_post_stops_on_a_security_question_and_never_retries():
    nonce = "NONCE-DO-NOT-SHOW"
    body = json.dumps({"code": "AUTH-SEC-501", "nonce": nonce, "message": "needs a nonce"})
    with _AuditLog() as log:
        h = _real_http(_Resp(200, body))
        try:
            h.post_api2_read("/files/f1/download-url?expire=60")
        except SkoolSecurityChallenge as e:
            assert e.code == "AUTH-SEC-501" and e.status == 200, e
            assert (e.method, e.path) == ("POST", "/files/f1/download-url"), (e.method, e.path)
            assert "expire" not in str(e) and nonce not in str(e), e
        else:
            raise AssertionError("the security question was swallowed")
        assert len(h._http.calls) == 1, f"a second try was sent: {h._http.calls}"
        assert log.lines == [], log.lines
    for ordinary in ({"code": "AUTH-SEC-401"}, {"code": 501}, {"data": {"code": "AUTH-SEC-501"}}):
        h = _real_http(_Resp(200, json.dumps(ordinary)))
        assert h.post_api2_read("/search/users", {}) == ordinary, ordinary


def test_the_read_post_error_texts_keep_the_diagnosis_but_never_a_link():
    h = _real_http(_Resp(422, '{"fields":[{"error":"expire is too long"}]}'))
    try:
        h.post_api2_read("/files/f1/download-url?expire=99999999")
    except SkoolHTTPError as e:
        assert e.status == 422 and "expire is too long" in str(e), e
    else:
        raise AssertionError("expected an error")
    h = _real_http(_Resp(500, f"upstream said: fetch {LINK} failed"))
    try:
        h.post_api2_read("/files/f1/download-url")
    except SkoolHTTPError as e:
        assert e.status == 500 and "upstream said" in str(e), e
        assert "SECRET-SIG" not in str(e) and "files.example.com" not in str(e), e
    else:
        raise AssertionError("expected an error")


def test_the_read_post_auth_and_network_errors():
    h = _real_http(_Resp(401, '{"error":"action not permitted"}'))
    try:
        h.post_api2_read("/search/users", {})
    except SkoolHTTPError as e:
        assert e.status == 401 and "isn't in that community" in str(e), e
    else:
        raise AssertionError("expected an error")
    h = _real_http(_Resp(403, "<html>blocked</html>"))
    try:
        h.post_api2_read("/search/users", {})
    except SkoolHTTPError as e:
        assert e.status == 403, e
    else:
        raise AssertionError("expected an error")
    h = _real_http(RequestException("boom"))
    try:
        h.post_api2_read("/search/users", {})
    except SkoolHTTPError as e:
        assert e.status == 0 and "Network error" in str(e), e
    else:
        raise AssertionError("expected an error")
    assert len(h._http.calls) == 1, "no automatic retry"


# -- tools: flags and registration ----------------------------------------------

WRITE_TOOLS = ("mark_chat_read", "mark_all_chats_read")
READ_TOOLS = ("search_members", "get_file_download_url")


def test_the_tools_exist_and_are_flagged():
    import catknows.mcp_server as m
    for name in WRITE_TOOLS:
        assert hasattr(m, name), f"{name} is not registered with CATKNOWS_ALLOW_WRITE=1"
        assert name in m._DESTRUCTIVE, f"{name} must be flagged destructive"
        assert name not in m._READ_ONLY, f"{name} changes the unread state"
    for name in READ_TOOLS:
        assert hasattr(m, name), f"{name} is not registered"
        assert name in m._READ_ONLY, f"{name} must be flagged read-only"
        assert name not in m._DESTRUCTIVE, f"{name} changes nothing"
    hints = {t.name: t.annotations for t in m.mcp._tool_manager.list_tools()}
    for name in WRITE_TOOLS:
        assert hints[name].read_only_hint is False and hints[name].destructive_hint is True, name
    for name in READ_TOOLS:
        assert hints[name].read_only_hint is True and hints[name].destructive_hint is False, name


def test_the_read_tools_exist_without_write_mode_and_the_write_tools_do_not():
    env = {k: v for k, v in os.environ.items() if k != "CATKNOWS_ALLOW_WRITE"}
    code = ("import catknows.mcp_server as m;"
            "print(sorted(n for n in ('search_members','get_file_download_url',"
            "'mark_chat_read','mark_all_chats_read') if hasattr(m, n)))")
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                         text=True, timeout=120, check=True).stdout.strip()
    assert out == "['get_file_download_url', 'search_members']", out


# -- tools: the two marks -------------------------------------------------------

def _drafts(c):
    return {
        "mark_chat_read": _with_client(c, lambda m: _fn(m.mark_chat_read)("ch1", "msg-7")),
        "mark_chat_read_all_of_it": _with_client(c, lambda m: _fn(m.mark_chat_read)("ch1")),
        "mark_all_chats_read": _with_client(c, lambda m: _fn(m.mark_all_chats_read)()),
    }


def test_the_mark_drafts_send_nothing_and_say_what_would_happen():
    c = _client()
    drafts = _drafts(c)
    assert c.http.calls == [], f"a draft must not touch Skool at all: {c.http.calls}"
    for name, out in drafts.items():
        assert out["status"].startswith("DRAFT"), (name, out)
        assert "confirm=true" in out["next_step"], (name, out)
        assert "before_you_show_this" not in out, f"{name}: no prose, no humanizing pass"
    one = drafts["mark_chat_read"]["would_mark_read"]
    assert (one["channel_id"], one["last"]) == ("ch1", "msg-7"), one
    whole = drafts["mark_chat_read_all_of_it"]["would_mark_read"]
    assert whole["channel_id"] == "ch1" and "none" in whole["last"], whole
    assert "whole chat" in whole["does"], whole


def test_the_mark_all_draft_says_unmistakably_that_it_is_every_chat():
    import catknows.mcp_server as m
    draft = _drafts(_client())["mark_all_chats_read"]
    would = draft["would_mark_all_read"]
    text = json.dumps(would)
    assert "EVERY chat" in would["does"], would
    assert "all communities" in text and "not just one" in text, would
    doc = _fn(m.mark_all_chats_read).__doc__
    assert "ALL" in doc and "every chat" in doc.lower(), doc


def test_the_mark_drafts_carry_a_fixed_note_on_what_is_not_verified():
    drafts = _drafts(_client())
    for name, out in drafts.items():
        note = out.get("not_verified")
        assert isinstance(note, str) and len(note) > 40, (name, out)
    assert "last" in drafts["mark_chat_read"]["not_verified"]
    assert "unread" in drafts["mark_all_chats_read"]["not_verified"]


def test_a_mark_draft_already_refuses_what_the_client_would_refuse():
    c = _client()
    refused = [
        lambda m: _fn(m.mark_chat_read)(""),
        lambda m: _fn(m.mark_chat_read)(" "),
        lambda m: _fn(m.mark_chat_read)(".."),
        lambda m: _fn(m.mark_chat_read)("."),
        lambda m: _fn(m.mark_chat_read)("ch1", ".."),
        lambda m: _fn(m.mark_chat_read)("ch1", "..."),
    ]
    for call in refused:
        _refused(lambda: _with_client(c, call))
    assert c.http.calls == [], c.http.calls


def test_an_empty_last_in_the_tool_means_not_given():
    c = _client()
    out = _with_client(c, lambda m: _fn(m.mark_chat_read)("ch1", "", confirm=True))
    assert out["status"] == "marked_read", out
    assert c.http.writes == [("POST", "/channels/ch1/read", None)], c.http.writes


def test_confirmed_marks_send_exactly_the_documented_calls():
    cases = [
        (lambda m: _fn(m.mark_chat_read)("ch1", confirm=True), "marked_read",
         [("POST", "/channels/ch1/read", None)]),
        (lambda m: _fn(m.mark_chat_read)("ch1", "msg-7", confirm=True), "marked_read",
         [("POST", "/channels/ch1/read?last=msg-7", None)]),
        (lambda m: _fn(m.mark_all_chats_read)(confirm=True), "all_chats_marked_read",
         [("POST", "/self/chat-channels/read", None)]),
    ]
    for call, status, writes in cases:
        c = _client()
        out = _with_client(c, call)
        assert out["status"] == status, (status, out)
        assert c.http.calls == writes, (status, c.http.calls)  # no read of a community before it


def test_the_mark_answers_go_through_the_scrub():
    answer = {"ok": True, "apiKeys": ["SECRET-KEY"]}
    for call in (lambda m: _fn(m.mark_chat_read)("ch1", confirm=True),
                 lambda m: _fn(m.mark_all_chats_read)(confirm=True)):
        c = _client(_FakeHTTP(answer=answer))
        out = _with_client(c, call)
        assert "SECRET-KEY" not in json.dumps(out), "the answer must go through _safe_raw"


def test_a_security_question_is_not_turned_into_a_result_by_the_mark_tools():
    for call in (lambda m: _fn(m.mark_chat_read)("ch1", confirm=True),
                 lambda m: _fn(m.mark_all_chats_read)(confirm=True)):
        c = _client(_ChallengedHTTP())
        try:
            _with_client(c, call)
        except SkoolSecurityChallenge:
            pass
        else:
            raise AssertionError("the tool swallowed Skool's security question")
        assert len(c.http.writes) == 1, f"a second try was sent: {c.http.writes}"


# -- tools: the member search ---------------------------------------------------

def _hit(**over):
    """One made-up `users[]` entry: api2 spells the top level snake_case, metadata camelCase."""
    hit = {
        "id": "u-1", "name": "test-person", "first_name": "Test", "last_name": "Person",
        "email": "anna@example.com",
        "metadata": {"bio": "Makes small things.",
                     "pictureProfile": "https://img.example.com/p.png", "online": 1,
                     "lastOffline": 1_700_000_000_000_000_000,
                     "spData": json.dumps({"pts": 42, "lv": 3})},
    }
    hit.update(over)
    return hit


def test_the_search_tool_returns_list_members_records_without_email():
    c = _client(_FakeHTTP(read_answer={"users": [_hit()]}))
    out = _with_client(c, lambda m: _fn(m.search_members)(SLUG, "Test"))
    (rec,) = out["members"]
    assert rec["skool_id"] == "u-1" and rec["name"] == "test-person", rec
    assert (rec["first_name"], rec["last_name"]) == ("Test", "Person"), rec
    assert (rec["points"], rec["level"], rec["is_online"]) == (42, 3, True), rec
    assert rec["bio"] == "Makes small things." and rec["picture_url"].endswith("p.png"), rec
    assert set(rec) == set(normalize.member({})), "the same fields as list_members"
    blob = json.dumps(out)
    assert "anna@example.com" not in blob and "email" not in blob, "no email in the output"
    assert (out["query"], out["community"], out["returned"]) == ("Test", SLUG, 1), out


def test_the_search_tool_reads_both_spellings_of_the_name_fields():
    camel = _hit()
    camel.pop("first_name"), camel.pop("last_name")
    camel.update(firstName="Camel", lastName="Case")
    c = _client(_FakeHTTP(read_answer={"users": [camel, _hit()]}))
    out = _with_client(c, lambda m: _fn(m.search_members)(SLUG, "x"))
    assert [(r["first_name"], r["last_name"]) for r in out["members"]] == [
        ("Camel", "Case"), ("Test", "Person")], out


def test_the_search_tool_copes_with_no_users():
    for answer in ({"users": None}, {"users": []}, {}):
        c = _client(_FakeHTTP(read_answer=answer))
        out = _with_client(c, lambda m: _fn(m.search_members)(SLUG, "x"))
        assert out["members"] == [] and out["returned"] == 0, out


def test_the_search_tool_raw_mode_goes_through_the_scrub_and_has_no_email():
    raw = _hit(apiKeys=["SECRET-KEY"], survey=[{"answer": "SURVEY-ANSWER"}],
               member={"role": "member", "billingEmail": "b@example.com"})
    c = _client(_FakeHTTP(read_answer={"users": [raw]}))
    out = _with_client(c, lambda m: _fn(m.search_members)(SLUG, "x", raw=True))
    blob = json.dumps(out)
    for secret in ("SECRET-KEY", "SURVEY-ANSWER", "b@example.com", "anna@example.com"):
        assert secret not in blob, f"{secret} reached the raw output"
    assert out["members"][0]["id"] == "u-1", out


def _seven_hits():
    return {"users": [_hit(id=f"u-{i}", name=f"person-{i}") for i in range(1, 8)]}


def test_the_search_body_is_the_same_for_every_tool_limit():
    for limit in (1, 3, 7, 8, 20, 500):
        for raw in (False, True):
            c = _client(_FakeHTTP(read_answer=_seven_hits()))
            _with_client(c, lambda m: _fn(m.search_members)(SLUG, "x", limit=limit, raw=raw))
            assert [x[2] for x in c.http.read_posts] == [
                {"query": "x", "group_id": GID, "limit": 7}], (limit, raw, c.http.read_posts)


def test_the_search_tool_limit_only_shortens_the_output():
    c = _client(_FakeHTTP(read_answer=_seven_hits()))
    out = _with_client(c, lambda m: _fn(m.search_members)(SLUG, "x", limit=3))
    assert [r["skool_id"] for r in out["members"]] == ["u-1", "u-2", "u-3"], out
    assert out["returned"] == 3 and "limit_capped" not in out, out
    c = _client(_FakeHTTP(read_answer=_seven_hits()))
    out = _with_client(c, lambda m: _fn(m.search_members)(SLUG, "x", limit=3, raw=True))
    assert [r["id"] for r in out["members"]] == ["u-1", "u-2", "u-3"], out
    c = _client(_FakeHTTP(read_answer=_seven_hits()))
    out = _with_client(c, lambda m: _fn(m.search_members)(SLUG, "x"))
    assert out["returned"] == 7 and "limit_capped" not in out, "the default is 7, no cut"
    out = _with_client(c, lambda m: _fn(m.search_members)(SLUG, "x", limit=7))
    assert out["returned"] == 7 and "limit_capped" not in out, out


def test_a_tool_limit_above_7_is_lowered_to_7_and_says_so():
    for raw in (False, True):
        c = _client(_FakeHTTP(read_answer=_seven_hits()))
        out = _with_client(c, lambda m: _fn(m.search_members)(SLUG, "x", limit=20, raw=raw))
        assert out["returned"] == 7 and len(out["members"]) == 7, out
        cap = out["limit_capped"]
        assert (cap["requested"], cap["returned"], cap["ceiling"]) == (20, 7, 7), cap
        assert "at most 7" in cap["note"] and "Narrow the query" in cap["note"], cap
    c = _client(_FakeHTTP(read_answer=_seven_hits()))  # even when Skool sent fewer than 7
    c.http.read_answer = {"users": [_hit()]}
    out = _with_client(c, lambda m: _fn(m.search_members)(SLUG, "x", limit=8))
    assert out["returned"] == 1 and out["limit_capped"]["requested"] == 8, out


def test_the_search_tool_refuses_a_limit_that_is_no_whole_number_of_1_or_more():
    for bad in (0, -1, -3, True, False, "3", "", None, 2.5, [3]):
        c = _client()
        _refused(lambda: _with_client(c, lambda m: _fn(m.search_members)(SLUG, "x", limit=bad)))
        assert c.http.calls == [], (bad, c.http.calls)


def test_the_search_tool_has_no_cursor_parameter():
    import inspect
    import catknows.mcp_server as m
    params = list(inspect.signature(_fn(m.search_members)).parameters)
    assert params == ["community_slug", "query", "limit", "raw"], params
    c = _client()
    try:
        _with_client(c, lambda m: _fn(m.search_members)(SLUG, "x", cursor="c-1"))
    except TypeError:
        pass
    else:
        raise AssertionError("the tool accepted a cursor")
    assert c.http.calls == [], c.http.calls


def test_a_cursor_in_skools_answer_never_reaches_the_output():
    answer = {"users": [_hit()], "cursor": "CURSOR-FROM-SKOOL", "has_more": True}
    for raw in (False, True):
        c = _client(_FakeHTTP(read_answer=dict(answer)))
        out = _with_client(c, lambda m: _fn(m.search_members)(SLUG, "x", raw=raw))
        assert "CURSOR-FROM-SKOOL" not in json.dumps(out), (raw, out)
        assert "cursor" not in out, (raw, out)
        assert out["has_more"] is True, "has_more stays when Skool sends it"
        assert len(c.http.read_posts) == 1, "one call, even when Skool says there is more"
    c = _client(_FakeHTTP(read_answer={"users": []}))
    out = _with_client(c, lambda m: _fn(m.search_members)(SLUG, "x"))
    assert "has_more" not in out and "cursor" not in out, "no claim about more or less pages"


def test_the_search_tool_makes_no_write_and_refuses_what_the_client_refuses():
    c = _client()
    _with_client(c, lambda m: _fn(m.search_members)(SLUG, "x"))
    assert c.http.writes == [], c.http.writes
    for call in (lambda m: _fn(m.search_members)(SLUG, ""),
                 lambda m: _fn(m.search_members)(SLUG, "  "),
                 lambda m: _fn(m.search_members)(SLUG, None)):
        c = _client()
        _refused(lambda: _with_client(c, call))
        assert c.http.calls == [], c.http.calls


# -- tools: the file download link ----------------------------------------------

def test_the_download_tool_returns_the_link_and_says_what_it_is():
    c = _client(_FakeHTTP(read_answer=LINK))
    out = _with_client(c, lambda m: _fn(m.get_file_download_url)("f1"))
    assert out["file_id"] == "f1" and out["download_url"] == LINK, out
    assert "access link" in out["note"], out
    assert c.http.calls == [("POST-READ", "/files/f1/download-url", None)], c.http.calls
    assert c.http.writes == [], c.http.writes


def test_the_download_tool_sends_expire_only_when_given():
    c = _client(_FakeHTTP(read_answer=LINK))
    _with_client(c, lambda m: _fn(m.get_file_download_url)("f1"))
    _with_client(c, lambda m: _fn(m.get_file_download_url)("f1", 0))
    out = _with_client(c, lambda m: _fn(m.get_file_download_url)("f1", 3600))
    assert [x[1] for x in c.http.calls] == [
        "/files/f1/download-url", "/files/f1/download-url",
        "/files/f1/download-url?expire=3600"], c.http.calls
    assert out["expire"] == 3600, out


def test_the_download_tool_refuses_bad_input_and_shows_no_link_in_an_error():
    c = _client(_FakeHTTP(read_answer=LINK))
    for call in (lambda m: _fn(m.get_file_download_url)(""),
                 lambda m: _fn(m.get_file_download_url)(".."),
                 lambda m: _fn(m.get_file_download_url)("f1", -5)):
        _refused(lambda: _with_client(c, call))
    assert c.http.calls == [], c.http.calls
    c = _client(_FakeHTTP(read_answer={"url": LINK}))
    try:
        _with_client(c, lambda m: _fn(m.get_file_download_url)("f1"))
    except SkoolHTTPError as e:
        assert "SECRET-SIG" not in str(e), e
    else:
        raise AssertionError("an answer that is no link must not pass")


def test_the_download_tool_refuses_what_is_no_link_without_showing_it():
    """The check lives in the client; the tool inherits it."""
    for odd in NOT_A_LINK:
        c = _client(_FakeHTTP(read_answer=odd))
        try:
            out = _with_client(c, lambda m: _fn(m.get_file_download_url)("f1"))
        except SkoolHTTPError as e:
            assert _not_shown(str(e)), f"the answer was shown for {odd!r}: {e}"
        else:
            raise AssertionError(f"{odd!r} must not pass as a link, got {out!r}")
    for good in (LINK, f" {LINK}\n"):
        c = _client(_FakeHTTP(read_answer=good))
        out = _with_client(c, lambda m: _fn(m.get_file_download_url)("f1"))
        assert out["download_url"] == LINK, out


def test_the_download_tool_says_it_is_for_files_not_for_videos():
    import catknows.mcp_server as m
    doc = _fn(m.get_file_download_url).__doc__ or ""
    assert "video" in doc.lower() and "access link" in doc.lower(), doc


# -- audit: nothing of a read, no content from a write --------------------------

def test_a_download_link_never_reaches_the_audit_log_or_an_error():
    with _AuditLog() as log:
        c = _real_client(_Resp(200, LINK))                       # plain text answer
        out = _with_client(c, lambda m: _fn(m.get_file_download_url)("f-1", 60))
        assert out["download_url"] == LINK, "the link belongs in the tool result"
        c = _real_client(_Resp(200, json.dumps(LINK)))           # JSON string answer
        out = _with_client(c, lambda m: _fn(m.get_file_download_url)("f-1"))
        assert out["download_url"] == LINK, out
        assert log.lines == [], f"a read left an audit line: {log.lines}"

        # a write afterwards writes its line, and the log still has no link in it
        _with_client(_real_client(), lambda m: _fn(m.mark_chat_read)("ch-1", confirm=True))
        assert len(log.lines) == 1, log.lines

        errors = []
        for resp in (_Resp(200, json.dumps({"url": LINK})),       # not a link: shape unknown
                     _Resp(200, json.dumps([LINK])),
                     _Resp(500, f"cannot sign {LINK}"),
                     _Resp(409, LINK)):
            try:
                _with_client(_real_client(resp), lambda m: _fn(m.get_file_download_url)("f-1"))
            except SkoolHTTPError as e:
                errors.append(str(e))
            else:
                raise AssertionError(f"expected an error for {resp.text!r}")
        assert len(errors) == 4
        for text in errors:
            assert "SECRET-SIG" not in text and "files.example.com" not in text, text
        blob = "\n".join(json.dumps(x) for x in log.lines)
        assert "SECRET-SIG" not in blob and "files.example.com" not in blob, blob
        assert len(log.lines) == 1, "a failed read adds no line either"


def test_a_search_leaves_no_line_and_a_mark_leaves_one_without_content():
    with _AuditLog() as log:
        c = _real_client(_Resp(200, json.dumps({"users": [_hit()]})))
        _with_client(c, lambda m: _fn(m.search_members)(SLUG, "Secret Query Name"))
        assert log.lines == [], f"a member search is a read: {log.lines}"
        assert "Secret Query Name" not in json.dumps(c.http._http.calls[0][2]["headers"])

        _with_client(_real_client(), lambda m: _fn(m.mark_chat_read)("ch-1", "m-7", confirm=True))
        _with_client(_real_client(), lambda m: _fn(m.mark_all_chats_read)(confirm=True))
        one, every = log.lines
        assert (one["tool"], one["method"], one["path"]) == (
            "mark_chat_read", "POST", "/channels/ch-1/read?last=m-7"), one
        assert one["dm_channel_id"] == "ch-1" and one["status"] == 200, one
        assert (every["tool"], every["method"], every["path"]) == (
            "mark_all_chats_read", "POST", "/self/chat-channels/read"), every
        assert every["status"] == 200 and "dm_channel_id" not in every, every
        blob = "\n".join(json.dumps(x) for x in log.lines)
        assert "Secret Query Name" not in blob and "anna@example.com" not in blob, blob


def test_a_mark_goes_through_the_write_gap():
    """The marks are real writes: they wait like every other write."""
    class _Clock:
        now = 1000.0
        slept: list = []

        def monotonic(self):
            return self.now

        def sleep(self, s):
            self.slept.append(s)
            self.now += s

    clock = _Clock()
    old = (http_mod._clock, http_mod._sleep, os.environ.get("CATKNOWS_WRITE_GAP_S"))
    http_mod._clock, http_mod._sleep = clock.monotonic, clock.sleep
    os.environ["CATKNOWS_WRITE_GAP_S"] = "15"
    http_mod._last_write_at.clear()
    try:
        with _AuditLog():
            c = _real_client(_Resp(200, json.dumps({"ok": True})))
            c.mark_chat_read("ch-1")
            c.mark_all_chats_read()
            assert clock.slept == [15], clock.slept
    finally:
        http_mod._clock, http_mod._sleep = old[0], old[1]
        if old[2] is None:
            os.environ.pop("CATKNOWS_WRITE_GAP_S", None)
        else:
            os.environ["CATKNOWS_WRITE_GAP_S"] = old[2]
        http_mod._last_write_at.clear()


# -- normalize ------------------------------------------------------------------

def test_search_user_is_a_member_record_without_contact_data():
    rec = normalize.search_user(_hit())
    want = normalize.member({"id": "u-1", "name": "test-person", "firstName": "Test",
                             "lastName": "Person", "metadata": _hit()["metadata"]})
    assert rec == want, rec
    assert "email" not in rec and "anna@example.com" not in json.dumps(rec, default=str), rec


def _deep_payload():
    """Secrets, contact data and plain fields, nested in objects and lists, depth 3 and more."""
    return {
        "users": [
            {"id": "u1", "email": "a@example.com", "apiKeys": ["k"], "survey": [{"a": 1}],
             "member": {"role": "member", "email": "b@example.com", "billingEmail": "c@example.com",
                        "metadata": {"deeper": [{"email": "d@example.com", "numRequests": 3,
                                                 "keep": "yes"}]}}},
            [{"nested": [{"email": "e@example.com", "self": {"x": 1}, "keep": "yes"}]}],
        ],
        "total": 2, "env": {"KEY": "v"},
    }


def _all_keys(obj):
    keys = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(k)
            keys |= _all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            keys |= _all_keys(v)
    return keys


def test_scrub_without_a_key_set_removes_exactly_the_secret_and_private_keys():
    """The default must not drift: `scrub(obj)` is what every raw path relied on before."""
    out = normalize.scrub(_deep_payload())
    left = _all_keys(out)
    assert not left & (normalize.SECRET_KEYS | normalize.PRIVATE_KEYS), left
    assert "email" in left, "contact keys are not part of the default set"
    for gone in ("apiKeys", "survey", "billingEmail", "numRequests", "self", "env"):
        assert gone not in left, gone
    assert {"id", "member", "role", "metadata", "deeper", "keep", "total", "nested"} <= left, left
    assert out["users"][0]["member"]["metadata"]["deeper"][0] == {
        "email": "d@example.com", "keep": "yes"}, out


def test_scrub_takes_the_key_set_through_every_level_of_the_recursion():
    out = normalize.scrub(_deep_payload(), normalize.CONTACT_KEYS)
    left = _all_keys(out)
    assert "email" not in left, left
    # a non-default set takes only that set: the secret keys stay (and the default is not mixed in)
    assert {"apiKeys", "survey", "billingEmail", "numRequests", "self", "env"} <= left, left
    assert out["users"][0]["member"]["metadata"]["deeper"][0] == {
        "numRequests": 3, "keep": "yes"}, out
    assert out["users"][1][0]["nested"][0] == {"self": {"x": 1}, "keep": "yes"}, out
    only = normalize.scrub(_deep_payload(), frozenset({"keep"}))
    assert "keep" not in _all_keys(only) and "email" in _all_keys(only), only


def test_strip_contact_removes_exactly_the_contact_keys_and_is_scrub_with_that_set():
    assert normalize.CONTACT_KEYS == frozenset({"email"}), normalize.CONTACT_KEYS
    stripped = normalize.strip_contact(_deep_payload())
    assert stripped == normalize.scrub(_deep_payload(), normalize.CONTACT_KEYS), stripped
    left = _all_keys(stripped)
    assert "email" not in left and "apiKeys" in left and "survey" in left, left
    assert normalize.strip_contact(["x", 3, None]) == ["x", 3, None], "plain values pass through"


def test_strip_contact_removes_email_at_every_depth_and_leaves_the_rest():
    obj = {"users": [{"id": "u1", "email": "a@example.com",
                      "member": {"email": "b@example.com", "role": "member"}}], "total": 1}
    out = normalize.strip_contact(obj)
    assert out is obj, "in place, like scrub"
    assert out == {"users": [{"id": "u1", "member": {"role": "member"}}], "total": 1}, out


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all green")
