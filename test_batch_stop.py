"""Self-check: a security question or a 401/403/429 from Skool ends a whole batch, an ordinary error does not.

Three batch jobs go through many entries and, for an ordinary error, count the
entry as failed and carry on: `pull_to_vault` (MCP), `python -m catknows pull`
(CLI) and `python -m catknows.snapshot` (four loops). After Skool has answered
with its security question (`SkoolSecurityChallenge`, AUTH-SEC-5xx) every
further request would be an automaton ignoring a question: the batch stops
there, no request for the next entry, files already written stay.

The same holds for HTTP 401, 403 and 429 (session or WAF rejected, rate limit):
the account is being turned away, and asking on for the next entry is the
pattern of an automaton. Same ways out as for the question: `pull_to_vault`
raises the error with how far it got, the CLI pull exits 1, the snapshot
exits 3. A 500, a 404 or a network error is still one failed entry.

A 202 that Skool repeats until the retries are used up is its other rate-limit
answer (docs/API.md §3, "429 / repeated 202"). It stops the batch the same way;
without that every further entry would be asked three more times. A body that
stays empty is not that answer and is still one failed entry.

In the CLI pull the same stop holds before the loop, while the members or the
list of posts are read: the stop sentence and exit 1, not a traceback.

Offline: the client is a fake that counts the calls it gets.

Run: python test_batch_stop.py    (no network, no pytest)
"""

from __future__ import annotations

import contextlib
import io
import tempfile
from argparse import Namespace
from pathlib import Path

import catknows.__main__ as cli
from catknows import mcp_server, snapshot
import catknows.http as http_mod
from catknows.http import SkoolHTTP, SkoolHTTPError, SkoolSecurityChallenge, turned_away

http_mod.RETRY_202_DELAY_S = 0

CHALLENGE_AT = "challenge"
ORDINARY_AT = "ordinary"          # HTTP 500
REJECTED = (401, 403, 429)        # an int as the kind: that HTTP status
ORDINARY = (500, 404, 0)          # 0 = network error
REPEATED_202 = "repeated 202"     # Skool's other rate-limit answer
STOPPING = (*REJECTED, REPEATED_202)


def _challenge() -> SkoolSecurityChallenge:
    return SkoolSecurityChallenge("AUTH-SEC-501", "GET", "/posts/x/comments?group-id=g1")


def _ordinary() -> SkoolHTTPError:
    return SkoolHTTPError("HTTP 500 on https://api2.skool.com/x: boom", 500)


def _status(status: int) -> SkoolHTTPError:
    return SkoolHTTPError(f"HTTP {status} on https://api2.skool.com/x: no", status)


class _Net:
    """A fake transport that gives the same answer every time and counts."""

    def __init__(self, status: int, text: str):
        self.status, self.text, self.gets = status, text, 0

    def get(self, url, **kw):
        self.gets += 1
        return type("R", (), {"status_code": self.status, "text": self.text})()


def _through_http(status: int, text: str) -> tuple[SkoolHTTPError, int]:
    """The error the real GET path raises for this answer, and how often it asked."""
    h = SkoolHTTP.__new__(SkoolHTTP)
    h._cache, h._http = {}, _Net(status, text)
    try:
        h._get_with_retry("https://www.example.com/x.json", {})
    except SkoolHTTPError as e:
        return e, h._http.gets
    raise AssertionError("an answer that never becomes data must end in an error")


def _raise_for(kind):
    if kind == REPEATED_202:
        raise _through_http(202, "")[0]
    if isinstance(kind, int):
        raise _status(kind)
    raise _challenge() if kind == CHALLENGE_AT else _ordinary()


def _names(kind) -> str:
    """How a stop message names the answer."""
    return "HTTP 202" if kind == REPEATED_202 else f"HTTP {kind}"


class _Members(list):
    incomplete = False
    capped_by_role = False
    total_members = 0


def _trees(n: int) -> list[dict]:
    return [{"post": {"id": f"p{i}", "name": f"post-{i}", "groupId": "g1",
                      "metadata": {"title": f"t{i}", "comments": 2}}} for i in range(n)]


class _PullClient:
    """What pull_to_vault and the CLI pull use. `fail` maps a post id to CHALLENGE_AT / ORDINARY_AT."""

    def __init__(self, posts: int, fail: dict[str, str]):
        self._trees, self.fail, self.comment_calls = _trees(posts), fail, []
        self.list_calls = []      # "members" / "posts": the two reads before the loop

    def members(self, slug):
        self.list_calls.append("members")
        if "members" in self.fail:
            _raise_for(self.fail["members"])
        return _Members([{"id": "u1", "name": "test-person-1", "firstName": "Test",
                          "lastName": "Person", "metadata": {}, "member": {}}])

    def posts(self, slug):
        self.list_calls.append("posts")
        if "posts" in self.fail:
            _raise_for(self.fail["posts"])
        return self._trees

    def comments(self, post_id, group_id):
        self.comment_calls.append(post_id)
        if post_id in self.fail:
            _raise_for(self.fail[post_id])
        return {"post_tree": {"children": []}}


def _written_posts(vault_dir: Path, community: str) -> list[str]:
    folder = vault_dir / community / "posts"
    return sorted(p.name for p in folder.glob("*.md")) if folder.exists() else []


# -- the repeated 202 ---------------------------------------------------------

def test_a_202_repeated_until_the_retries_are_used_up_counts_as_turned_away():
    e, gets = _through_http(202, "")
    assert gets == http_mod.MAX_RETRIES_202, gets
    assert turned_away(e), "a repeated 202 is Skool's rate limit: a batch must stop on it"
    assert e.status == 0, "the status of that error stays what it was"


def test_an_empty_body_or_a_500_through_http_is_not_turned_away():
    e, _ = _through_http(200, "   ")
    assert not turned_away(e), "a body that stays empty is no rate limit"
    e, _ = _through_http(500, "boom")
    assert not turned_away(e) and e.status == 500


# -- pull_to_vault (MCP tool) -------------------------------------------------

_tool = mcp_server.pull_to_vault
pull_to_vault = _tool if callable(_tool) else (getattr(_tool, "fn", None) or _tool.func)


def test_pull_to_vault_stops_on_a_challenge():
    with tempfile.TemporaryDirectory() as tmp:
        c = _PullClient(5, {"p2": CHALLENGE_AT})
        mcp_server._client = c
        try:
            pull_to_vault("some-community", vault_dir=tmp)
        except SkoolSecurityChallenge as e:
            msg = str(e)
            assert e.code == "AUTH-SEC-501" and "AUTH-SEC-501" in msg, msg
            assert "pull_to_vault" in msg and "stopped" in msg, msg
            assert "2 of 5" in msg, f"how far it got must be in the message: {msg}"
        else:
            raise AssertionError("a challenge must end the pull with an error, not a partial result")
        assert c.comment_calls == ["p0", "p1", "p2"], \
            f"no request for post p3 after the challenge on p2: {c.comment_calls}"
        written = _written_posts(Path(tmp), "some-community")
        assert len(written) == 2, f"p0 and p1 were written and stay; p2 and later were not: {written}"


def test_pull_to_vault_still_counts_an_ordinary_error_and_goes_on():
    with tempfile.TemporaryDirectory() as tmp:
        c = _PullClient(5, {"p1": ORDINARY_AT, "p3": ORDINARY_AT})
        mcp_server._client = c
        out = pull_to_vault("some-community", vault_dir=tmp)
        assert out["comments_failed"] == 2 and out["posts"] == 5, out
        assert c.comment_calls == ["p0", "p1", "p2", "p3", "p4"], c.comment_calls
        assert len(_written_posts(Path(tmp), "some-community")) == 5


def test_pull_to_vault_stops_on_401_403_429_and_a_repeated_202():
    for status in STOPPING:
        with tempfile.TemporaryDirectory() as tmp:
            c = _PullClient(5, {"p2": status})
            mcp_server._client = c
            try:
                pull_to_vault("some-community", vault_dir=tmp)
            except SkoolHTTPError as e:
                msg = str(e)
                assert not isinstance(e, SkoolSecurityChallenge), status
                if status == REPEATED_202:
                    assert turned_away(e), "the stop error still says: turned away"
                else:
                    assert e.status == status, f"the status stays on the error: {e.status}"
                assert "pull_to_vault" in msg and "stopped" in msg, msg
                assert "2 of 5" in msg and _names(status) in msg, msg
            else:
                raise AssertionError(f"{status} must end the pull with an error, not a partial result")
            assert c.comment_calls == ["p0", "p1", "p2"], \
                f"{status}: no request for post p3 after the rejection on p2: {c.comment_calls}"
            written = _written_posts(Path(tmp), "some-community")
            assert len(written) == 2, f"{status}: p0 and p1 stay, p2 and later not written: {written}"


def test_pull_to_vault_goes_on_after_500_404_and_a_network_error():
    for status in ORDINARY:
        with tempfile.TemporaryDirectory() as tmp:
            c = _PullClient(5, {"p2": status})
            mcp_server._client = c
            out = pull_to_vault("some-community", vault_dir=tmp)
            assert out["comments_failed"] == 1 and out["posts"] == 5, (status, out)
            assert c.comment_calls == ["p0", "p1", "p2", "p3", "p4"], (status, c.comment_calls)


# -- CLI: python -m catknows pull ---------------------------------------------

def _run_cli(client: _PullClient, tmp: str) -> tuple[int, str, str]:
    cli.session_from_cookie = lambda cookie: type("S", (), {"is_valid": True})()
    cli.SkoolClient = lambda session: client
    args = Namespace(cookie="auth_token=x", vault=tmp, community="some-community",
                     no_comments=False, profile_dir="", headless=True)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli._pull(args)
    return rc, out.getvalue(), err.getvalue()


def test_cli_pull_stops_on_a_challenge_with_a_failing_exit_code():
    with tempfile.TemporaryDirectory() as tmp:
        c = _PullClient(5, {"p2": CHALLENGE_AT})
        rc, out, err = _run_cli(c, tmp)
        assert rc == 1, f"a stopped pull must exit non-zero, got {rc}"
        assert "AUTH-SEC-501" in err and "stopped" in err, err
        assert "Done" not in out, "a stopped pull must not end with the success line"
        assert c.comment_calls == ["p0", "p1", "p2"], c.comment_calls
        assert len(_written_posts(Path(tmp), "some-community")) == 2, "written notes stay"


def test_cli_pull_still_counts_an_ordinary_error_and_goes_on():
    with tempfile.TemporaryDirectory() as tmp:
        c = _PullClient(5, {"p1": ORDINARY_AT})
        rc, out, err = _run_cli(c, tmp)
        assert rc == 0 and "Done" in out, (rc, out)
        assert "comments failed for p1" in err, err
        assert c.comment_calls == ["p0", "p1", "p2", "p3", "p4"], c.comment_calls


def test_cli_pull_stops_on_401_403_429_and_a_repeated_202_with_a_failing_exit_code():
    for status in STOPPING:
        with tempfile.TemporaryDirectory() as tmp:
            c = _PullClient(5, {"p2": status})
            rc, out, err = _run_cli(c, tmp)
            assert rc == 1, f"{status}: a stopped pull must exit 1, got {rc}"
            assert _names(status) in err and "stopped after 2 of 5" in err, err
            assert "Done" not in out, "a stopped pull must not end with the success line"
            assert c.comment_calls == ["p0", "p1", "p2"], (status, c.comment_calls)
            assert len(_written_posts(Path(tmp), "some-community")) == 2, "written notes stay"


def test_cli_pull_goes_on_after_500_404_and_a_network_error():
    for status in ORDINARY:
        with tempfile.TemporaryDirectory() as tmp:
            c = _PullClient(5, {"p2": status})
            rc, out, err = _run_cli(c, tmp)
            assert rc == 0 and "Done" in out, (status, rc, out)
            assert "stopped" not in err, err
            assert c.comment_calls == ["p0", "p1", "p2", "p3", "p4"], (status, c.comment_calls)


def test_cli_pull_stops_before_the_loop_without_a_traceback():
    for at, asked in (("members", ["members"]), ("posts", ["members", "posts"])):
        for kind in (*STOPPING, CHALLENGE_AT):
            with tempfile.TemporaryDirectory() as tmp:
                c = _PullClient(5, {at: kind})
                rc, out, err = _run_cli(c, tmp)     # an exception here is the traceback
                where = f"{at} {kind}"
                assert rc == 1, f"{where}: a stopped pull must exit 1, got {rc}"
                named = "AUTH-SEC-501" if kind == CHALLENGE_AT else _names(kind)
                assert "error: pull stopped" in err and named in err, (where, err)
                assert "nothing more was requested" in err, (where, err)
                assert "Done" not in out, where
                assert c.list_calls == asked and c.comment_calls == [], \
                    f"{where}: nothing may be requested after it: {c.list_calls} {c.comment_calls}"
                members = list((Path(tmp) / "some-community" / "members").glob("*.md"))
                assert len(members) == (1 if at == "posts" else 0), \
                    f"{where}: member notes already written stay: {members}"


def test_cli_pull_still_raises_an_ordinary_error_before_the_loop():
    for at in ("members", "posts"):
        with tempfile.TemporaryDirectory() as tmp:
            c = _PullClient(5, {at: 500})
            try:
                _run_cli(c, tmp)
            except SkoolHTTPError as e:
                assert e.status == 500, e.status
            else:
                raise AssertionError(f"{at}: a 500 before the loop is no stop, it stays the error it was")


# -- snapshot: four loops -----------------------------------------------------

class _SnapClient:
    """What snapshot.main asks for. `fail` maps (method, key) to CHALLENGE_AT / ORDINARY_AT."""

    def __init__(self, fail: dict[tuple[str, str], str]):
        self.fail, self.calls = fail, []

    def _hit(self, method: str, key: str) -> None:
        self.calls.append((method, key))
        if (method, key) in self.fail:
            _raise_for(self.fail[(method, key)])

    def community_about(self, slug):
        self._hit("about", slug)
        return {"pageProps": {"currentGroup": {"metadata": {"totalMembers": 5}}}}

    def discovery(self, page=1):
        self._hit("discovery", "page1")
        return {"groups": []}

    def group_id_for(self, slug):
        return slug          # the slug doubles as the id, so later calls name their entry

    def discovery_rank(self, gid):
        self._hit("rank", gid)
        return {"rank": 7, "category": {"name": "x"}, "category_rank": 1}

    def growth_overview(self, gid):
        self._hit("growth", gid)
        return {"num_visitors": 10, "num_signups": 2, "num_members": 5}

    def analytics_chart(self, gid, name):
        return []


def _run_snapshot(client: _SnapClient, tmp: str, *argv: str) -> tuple[int, str]:
    snapshot.login = lambda **kw: object()
    snapshot.SkoolClient = lambda session: client
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        rc = snapshot.main(["--vault", tmp, *argv])
    return rc, err.getvalue()


def _trend(tmp: str, name: str) -> Path:
    return Path(tmp) / "trends" / f"{name}.jsonl"


def test_snapshot_slugs_stop_on_a_challenge():
    with tempfile.TemporaryDirectory() as tmp:
        c = _SnapClient({("about", "b"): CHALLENGE_AT})
        rc, err = _run_snapshot(c, tmp, "a", "b", "c", "--discovery", "--rank", "r1", "--growth", "g1")
        assert rc == 3, f"a stopped snapshot must exit non-zero (3), got {rc}"
        assert "AUTH-SEC-501" in err and "stopped" in err, err
        assert c.calls == [("about", "a"), ("about", "b")], \
            f"nothing after the challenge on b (not c, not discovery, rank or growth): {c.calls}"
        assert _trend(tmp, "a").exists(), "rows already written stay"
        assert not _trend(tmp, "c").exists()


def test_snapshot_discovery_stops_on_a_challenge():
    with tempfile.TemporaryDirectory() as tmp:
        c = _SnapClient({("discovery", "page1"): CHALLENGE_AT})
        rc, err = _run_snapshot(c, tmp, "a", "--discovery", "--rank", "r1", "--growth", "g1")
        assert rc == 3 and "AUTH-SEC-501" in err, (rc, err)
        assert c.calls == [("about", "a"), ("discovery", "page1")], c.calls
        assert _trend(tmp, "a").exists()


def test_snapshot_rank_stops_on_a_challenge():
    with tempfile.TemporaryDirectory() as tmp:
        c = _SnapClient({("rank", "r2"): CHALLENGE_AT})
        rc, err = _run_snapshot(c, tmp, "--rank", "r1", "r2", "r3", "--growth", "g1")
        assert rc == 3 and "AUTH-SEC-501" in err, (rc, err)
        assert c.calls == [("rank", "r1"), ("rank", "r2")], c.calls
        assert _trend(tmp, "rank").exists(), "the row for r1 stays"
        assert len(_trend(tmp, "rank").read_text("utf-8").splitlines()) == 1


def test_snapshot_growth_stops_on_a_challenge():
    with tempfile.TemporaryDirectory() as tmp:
        c = _SnapClient({("growth", "g2"): CHALLENGE_AT})
        rc, err = _run_snapshot(c, tmp, "--growth", "g1", "g2", "g3")
        assert rc == 3 and "AUTH-SEC-501" in err, (rc, err)
        assert c.calls == [("growth", "g1"), ("growth", "g2")], c.calls
        assert len(_trend(tmp, "growth").read_text("utf-8").splitlines()) == 1


def test_snapshot_still_counts_an_ordinary_error_and_goes_on():
    with tempfile.TemporaryDirectory() as tmp:
        c = _SnapClient({("about", "b"): ORDINARY_AT, ("discovery", "page1"): ORDINARY_AT,
                         ("rank", "r1"): ORDINARY_AT, ("growth", "g1"): ORDINARY_AT})
        rc, err = _run_snapshot(c, tmp, "a", "b", "c", "--discovery", "--rank", "r1", "r2",
                                "--growth", "g1", "g2")
        assert rc == 0, f"some rows were collected, so the run is not a total failure: {rc}"
        assert c.calls == [("about", "a"), ("about", "b"), ("about", "c"),
                           ("discovery", "page1"),
                           ("rank", "r1"), ("rank", "r2"),
                           ("growth", "g1"), ("growth", "g2")], c.calls
        for what in ("!! b:", "!! discovery:", "!! rank r1:", "!! growth g1:"):
            assert what in err, (what, err)
        assert "stopped" not in err


def test_snapshot_stops_on_401_403_429_and_a_repeated_202_in_all_four_loops():
    argv = ("a", "b", "c", "--discovery", "--rank", "r1", "r2", "r3", "--growth", "g1", "g2", "g3")
    walk = [("about", "a"), ("about", "b"), ("about", "c"), ("discovery", "page1"),
            ("rank", "r1"), ("rank", "r2"), ("rank", "r3"),
            ("growth", "g1"), ("growth", "g2"), ("growth", "g3")]
    for at in (("about", "b"), ("discovery", "page1"), ("rank", "r2"), ("growth", "g2")):
        for status in STOPPING:
            with tempfile.TemporaryDirectory() as tmp:
                c = _SnapClient({at: status})
                rc, err = _run_snapshot(c, tmp, *argv)
                assert rc == 3, f"{at} {status}: a stopped snapshot must exit 3, got {rc}"
                assert _names(status) in err and "stopped" in err, (at, status, err)
                assert c.calls == walk[:walk.index(at) + 1], \
                    f"{at} {status}: nothing may be requested after the rejection: {c.calls}"
                assert _trend(tmp, "a").exists(), "rows already written stay"


def test_snapshot_goes_on_after_500_404_and_a_network_error():
    for status in ORDINARY:
        with tempfile.TemporaryDirectory() as tmp:
            c = _SnapClient({("about", "a"): status, ("discovery", "page1"): status,
                             ("rank", "r1"): status, ("growth", "g1"): status})
            rc, err = _run_snapshot(c, tmp, "a", "b", "--discovery", "--rank", "r1", "r2",
                                    "--growth", "g1", "g2")
            assert rc == 0 and "stopped" not in err, (status, rc, err)
            assert c.calls == [("about", "a"), ("about", "b"), ("discovery", "page1"),
                               ("rank", "r1"), ("rank", "r2"),
                               ("growth", "g1"), ("growth", "g2")], (status, c.calls)


if __name__ == "__main__":
    failed = []
    tests = [(n, f) for n, f in list(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
        except Exception as e:  # keep going: one run lists every gap
            failed.append(name)
            print(f"FAIL {name}: {type(e).__name__}: {str(e)[:300]}")
    if failed:
        raise SystemExit(f"{len(failed)} of {len(tests)} checks failed")
    print(f"ok — {len(tests)} checks: a security question, a 401/403/429 and a repeated 202 stop "
          "pull_to_vault, the CLI pull and all four snapshot loops; ordinary errors are still "
          "counted per entry")
