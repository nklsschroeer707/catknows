"""Self-check: after 401, 403 or 429 nothing more goes out, and no write at all.

Skool's 401/403 (session or WAF rejected) and 429 (rate limit) are the signs
that an account is under watch. A client that answers them with a second
request, or with the write it was about to send, is exactly the automaton they
are meant to stop. Three places used to read such an answer as "nothing there"
and carried on:

  1. ``group_id_for``: any failed ``GET /groups/{slug}`` fell through to the
     feed page (a second request), and on the write paths to the write itself.
     Now 401/403/429 are raised on every path, and on a write path every error
     is: what cannot be checked for "archived" is not written to.
  2. ``update_course_item``: any failed tree read meant "not a root unit" and a
     blind PUT followed. Now 401/403/429 and a network error are raised.
  3. the ``send_dm`` tool: any failed channel lookup became "Unknown channel".
     Now 401/403/429 and a network error are raised as they are.

Plus the two read fallbacks that stay on 401/403 by decision (categories via
api, course tree with a slug) never fall back after a 429.

Everything here runs against fakes: no network, no Skool, no pytest.

Run: python test_stop_on_rejection.py
"""

import os

os.environ["CATKNOWS_ALLOW_WRITE"] = "1"  # send_dm only registers in WRITE mode

import catknows.client as cl
from catknows import mcp_server
from catknows.client import SkoolClient
from catknows.http import SkoolHTTPError

cl._INTER_PAGE_DELAY_S = 0

STOP = (401, 403, 429)


class RecordingHTTP:
    """Records every request; ``GET /groups/{slug}`` (or any api2 GET) fails with ``error``."""

    def __init__(self, error=None, *, group=None):
        self.error, self.group, self.calls = error, group, []

    def get_api2(self, path):
        self.calls.append(("GET", path))
        if self.error is not None:
            raise self.error
        return self.group or {"id": "g" * 32}

    def get_next(self, q, slug=""):
        self.calls.append(("NEXT", q))
        return {"pageProps": {"postTrees": [{"post": {"groupId": "f" * 32}}],
                              "currentGroup": {"labels": []}}}

    def _write(self, verb, path, body=None):
        self.calls.append((verb, path))
        return {"id": "new"}

    def post_api2(self, path, body):
        return self._write("POST", path, body)

    def put_api2(self, path, body):
        return self._write("PUT", path, body)

    def delete_api2(self, path, body=None):
        return self._write("DELETE", path, body)

    def post_api2_read(self, path, body=None):
        self.calls.append(("POST-READ", path))
        return {"users": []}


def _client(http) -> SkoolClient:
    c = SkoolClient.__new__(SkoolClient)
    c.http = http
    return c


def _err(status):
    return SkoolHTTPError(f"HTTP {status}" if status else "Network error", status)


def _raises(call, err):
    try:
        call()
    except SkoolHTTPError as e:
        assert e is err, f"expected the original error, got {e!r}"
        return
    raise AssertionError(f"status {err.status}: expected the error to pass through")


# -- 1. group_id_for -----------------------------------------------------------

def test_group_id_for_read_stops_on_401_403_429():
    for status in STOP:
        err = _err(status)
        http = RecordingHTTP(err)
        _raises(lambda: _client(http).group_id_for("some-group"), err)
        assert http.calls == [("GET", "/groups/some-group")], (status, http.calls)


def test_group_id_for_read_still_falls_back_on_other_errors():
    for status in (500, 404, 0):
        http = RecordingHTTP(_err(status))
        assert _client(http).group_id_for("some-group") == "f" * 32, status
        assert http.calls[0] == ("GET", "/groups/some-group"), http.calls
        assert http.calls[1][0] == "NEXT", f"the feed page is the read fallback: {http.calls}"


def test_group_id_for_write_stops_on_every_error():
    for status in STOP + (500, 404, 0):
        err = _err(status)
        http = RecordingHTTP(err)
        _raises(lambda: _client(http).group_id_for("some-group", for_write=True), err)
        assert http.calls == [("GET", "/groups/some-group")], (status, http.calls)


WRITES = {
    "create_post": lambda c: c.create_post("some-group", "t", "b"),
    "create_comment": lambda c: c.create_comment("some-group", "p1", "b"),
    "create_poll": lambda c: c.create_poll("some-group", ["a", "b"]),
    "create_course": lambda c: c.create_course("some-group", "t"),
}


def test_writes_send_nothing_after_a_failed_group_lookup():
    for name, call in WRITES.items():
        for status in STOP + (500, 0):
            err = _err(status)
            http = RecordingHTTP(err)
            _raises(lambda: call(_client(http)), err)
            assert http.calls == [("GET", "/groups/some-group")], (name, status, http.calls)


def test_reads_built_on_group_id_for_stop_too():
    """categories via api and the member search: no labels, no search after a 429."""
    for status in STOP:
        err = _err(status)
        http = RecordingHTTP(err)
        _raises(lambda: _client(http).post_categories("some-group", via_api=True), err)
        assert http.calls == [("GET", "/groups/some-group")], (status, http.calls)
        http = RecordingHTTP(err)
        _raises(lambda: _client(http).search_members("some-group", "anna"), err)
        assert http.calls == [("GET", "/groups/some-group")], (status, http.calls)


# -- 2. update_course_item -----------------------------------------------------

def test_update_course_item_sends_no_put_after_a_stop_status_or_network_error():
    for status in STOP + (0,):
        err = _err(status)
        http = RecordingHTTP(err)
        _raises(lambda: _client(http).update_course_item("u1", {"title": "t"}), err)
        assert not any(v == "PUT" for v, _ in http.calls), (status, http.calls)
        assert http.calls == [("GET", "/courses/u1")], (status, http.calls)


def test_update_course_item_non_root_still_updates_flat():
    """A folder or page answers the tree read with 400: that is "not a root unit"."""
    http = RecordingHTTP(_err(400))
    _client(http).update_course_item("u1", {"title": "t"})
    assert http.calls == [("GET", "/courses/u1"), ("PUT", "/courses/u1")], http.calls


# -- 3. send_dm tool -----------------------------------------------------------

_tool = mcp_server.send_dm
send_dm = _tool if callable(_tool) else (getattr(_tool, "fn", None) or _tool.func)


def test_send_dm_lookup_failure_is_not_an_unknown_channel():
    for status in STOP + (0,):
        err = _err(status)
        http = RecordingHTTP(err)
        c = _client(http)
        c.upload_file = lambda gid, path: (_ for _ in ()).throw(AssertionError("must not upload"))
        mcp_server._client = c
        try:
            send_dm(channel_id="ch1", content="hi", attachments="x.gif", confirm=True)
        except ValueError as e:
            raise AssertionError(f"status {status} was reported as: {e}")
        except SkoolHTTPError as e:
            assert e is err, e
        else:
            raise AssertionError(f"status {status}: the send must stop")
        assert http.calls == [("GET", "/channels/ch1/messages?before=1")], (status, http.calls)


def test_send_dm_unknown_channel_still_says_so():
    http = RecordingHTTP(_err(404))
    c = _client(http)
    mcp_server._client = c
    try:
        send_dm(channel_id="ch1", content="hi", attachments="x.gif", confirm=True)
    except ValueError as e:
        assert "Unknown channel" in str(e), e
    else:
        raise AssertionError("a 404 on the lookup is an unknown channel")
    assert not any(v == "POST" for v, _ in http.calls), http.calls


# -- 4. the read fallbacks never follow a 429 ----------------------------------

def test_course_tree_never_falls_back_after_429():
    for status in (429, 0):
        err = _err(status)
        http = RecordingHTTP(err)
        c = _client(http)
        c._course_tree_via_page = lambda slug, cid: (_ for _ in ()).throw(
            AssertionError("no page after a 429"))
        _raises(lambda: c.course_tree("c1", "some-group"), err)
        assert http.calls == [("GET", "/courses/c1")], http.calls


def test_categories_never_fall_back_after_429_on_the_labels():
    class LabelsRateLimited(RecordingHTTP):
        def get_api2(self, path):
            self.calls.append(("GET", path))
            if path.endswith("/labels"):
                raise self.error
            return {"id": "g" * 32}

    err = _err(429)
    http = LabelsRateLimited(err)
    _raises(lambda: _client(http).post_categories("some-group", via_api=True), err)
    assert not any(v == "NEXT" for v, _ in http.calls), http.calls


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
    print(f"ok — {len(tests)} checks: after 401/403/429 nothing more goes out")
