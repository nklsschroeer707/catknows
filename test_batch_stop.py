"""Self-check: a security question from Skool ends a whole batch, an ordinary error does not.

Three batch jobs go through many entries and, for an ordinary error, count the
entry as failed and carry on: `pull_to_vault` (MCP), `python -m catknows pull`
(CLI) and `python -m catknows.snapshot` (four loops). After Skool has answered
with its security question (`SkoolSecurityChallenge`, AUTH-SEC-5xx) every
further request would be an automaton ignoring a question: the batch stops
there, no request for the next entry, files already written stay.

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
from catknows.http import SkoolHTTPError, SkoolSecurityChallenge

CHALLENGE_AT = "challenge"
ORDINARY_AT = "ordinary"


def _challenge() -> SkoolSecurityChallenge:
    return SkoolSecurityChallenge("AUTH-SEC-501", "GET", "/posts/x/comments?group-id=g1")


def _ordinary() -> SkoolHTTPError:
    return SkoolHTTPError("HTTP 500 on https://api2.skool.com/x: boom", 500)


def _raise_for(kind: str):
    raise _challenge() if kind == CHALLENGE_AT else _ordinary()


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

    def members(self, slug):
        return _Members()

    def posts(self, slug):
        return self._trees

    def comments(self, post_id, group_id):
        self.comment_calls.append(post_id)
        if post_id in self.fail:
            _raise_for(self.fail[post_id])
        return {"post_tree": {"children": []}}


def _written_posts(vault_dir: Path, community: str) -> list[str]:
    folder = vault_dir / community / "posts"
    return sorted(p.name for p in folder.glob("*.md")) if folder.exists() else []


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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ok — a security question stops pull_to_vault, the CLI pull and all four snapshot loops; "
          "ordinary errors are still counted per entry")
