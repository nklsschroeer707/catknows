"""Self-check: a reply to a reply is re-hung under its top-level comment.

Skool threads are two levels deep. A local write test (2026-09-15) sent 7
replies: the 5 under top-level comments survived, the 2 under a *reply* were
created, counted, then shown as "Comment was deleted". The same texts posted by
hand stayed — Skool's own reply button posts under the top-level comment and
opens with an @-mention. create_comment must do the same.

The lookup behind that (``GET /posts/{id}``) is catknows' own rule, not a call
of Skool's web client. A library caller that already knows the target is a
top-level comment says so with ``parent_is_top_level=True`` and saves the
request; the POST it sends must be the one the lookup path sends for the same
target. The counting stand-in below records every request, GET and POST.

Run: python test_reply_threading.py    (no network, no pytest)
"""

import inspect
import json
import os

from catknows.client import SkoolClient, with_mention

POST, TOP, REPLY = "post1", "top1", "reply1"
BY_ID = {
    TOP: {"id": TOP, "parent_id": POST, "root_id": POST, "user_id": "u1"},
    REPLY: {"id": REPLY, "parent_id": TOP, "root_id": POST, "user_id": "u2"},
}
TREE = {"post_tree": {"children": [
    {"post": {"id": TOP, "user": {"id": "u1", "first_name": "Ann", "last_name": "A"}},
     "children": [{"post": {"id": REPLY, "user": {"id": "u2", "name": "fabiola",
                                                  "first_name": "Bea", "last_name": "B"}},
                   "children": []}]},
]}}


class _FakeHTTP:
    def __init__(self):
        self.writes = []

    def post_api2(self, path, body):
        self.writes.append(body)
        return {"id": "new"}


def _client():
    c = SkoolClient.__new__(SkoolClient)
    c.http = _FakeHTTP()
    c.post_by_id = lambda pid: BY_ID[pid]
    c.comments = lambda post_id, gid: TREE
    c.group_id_for = lambda slug, for_write=False: "g1"
    return c


def test_top_level_comment_hangs_under_the_post():
    c = _client()
    c.create_comment("s", POST, "hi")
    body, = c.http.writes
    assert body["parent_id"] == POST and body["metadata"]["content"] == "hi", body


def test_reply_to_top_level_stays_put():
    c = _client()
    c.create_comment("s", POST, "hi", parent_comment_id=TOP)
    body, = c.http.writes
    assert body["parent_id"] == TOP, body
    assert body["metadata"]["content"] == "hi", "no mention needed at level two"


def test_reply_to_a_reply_goes_under_its_top_level_with_a_mention():
    c = _client()
    c.create_comment("s", POST, "danke!", parent_comment_id=REPLY)
    body, = c.http.writes
    assert body["parent_id"] == TOP, f"third level gets deleted by Skool: {body}"
    assert body["root_id"] == POST, body
    assert body["metadata"]["content"] == "[@Bea B](obj://user/u2) danke!", body


def test_existing_mention_is_not_doubled():
    m = "[@Bea B](obj://user/u2) "
    already = "[@Bea B](obj://user/u2) schon erwähnt"
    assert with_mention(m, already) == already
    assert with_mention("", "x") == "x"


# -- the hint: the caller knows the level, so the lookup is skipped -------------

GROUP = {"id": "g1", "name": "s", "metadata": {}}
GET_GROUP = ("GET", "/groups/s")
POST_COMMENT = ("POST", "/posts?follow=false")


class _CountingHTTP:
    """Records every request in order; answers from the fixtures above.

    A GET for a path the fixtures do not know raises KeyError, so a request the
    test did not expect fails loudly instead of getting a made-up answer.
    """

    def __init__(self, group=GROUP):
        self.group, self.log, self.writes = group, [], []

    def get_api2(self, path):
        self.log.append(("GET", path))
        if path == "/groups/s":
            return self.group
        if path.startswith(f"/posts/{POST}/comments"):
            return TREE
        return BY_ID[path.removeprefix("/posts/")]

    def post_api2(self, path, body):
        self.log.append(("POST", path))
        self.writes.append((path, body))
        return {"id": "new"}


def _counting_client(group=GROUP):
    """Real create_comment, reply_target, post_by_id, comments and group_id_for."""
    c = SkoolClient.__new__(SkoolClient)
    c.http = _CountingHTTP(group)
    return c


def test_known_top_level_target_skips_the_lookup():
    c = _counting_client()
    c.create_comment("s", POST, "danke!", parent_comment_id=TOP, parent_is_top_level=True)
    assert c.http.log == [GET_GROUP, POST_COMMENT], c.http.log
    (_, body), = c.http.writes
    assert body["parent_id"] == TOP and body["root_id"] == POST, body
    assert body["metadata"]["content"] == "danke!", "no mention at level two"


def test_the_post_with_the_hint_is_the_post_of_the_lookup_path():
    for extra in ({}, {"attachments": "f1,f2"}):
        old, new = _counting_client(), _counting_client()
        old.create_comment("s", POST, "danke!", parent_comment_id=TOP, **extra)
        new.create_comment("s", POST, "danke!", parent_comment_id=TOP,
                           parent_is_top_level=True, **extra)
        assert ("GET", f"/posts/{TOP}") in old.http.log, "the old path does look it up"
        assert ("GET", f"/posts/{TOP}") not in new.http.log
        (old_path, old_body), = old.http.writes
        (new_path, new_body), = new.http.writes
        assert new_path == old_path == "/posts?follow=false", (old_path, new_path)
        # json.dumps keeps the key order, so this is the same request body
        assert json.dumps(new_body) == json.dumps(old_body), (old_body, new_body)


def test_hint_without_a_comment_target_comments_under_the_post():
    for parent in ("", POST):
        old, new = _counting_client(), _counting_client()
        old.create_comment("s", POST, "hi", parent_comment_id=parent)
        new.create_comment("s", POST, "hi", parent_comment_id=parent, parent_is_top_level=True)
        assert new.http.log == old.http.log == [GET_GROUP, POST_COMMENT], (new.http.log, old.http.log)
        assert json.dumps(new.http.writes) == json.dumps(old.http.writes)
        assert new.http.writes[0][1]["parent_id"] == POST, new.http.writes


def test_without_the_hint_the_lookup_stays():
    for kwargs in ({}, {"parent_is_top_level": False}):
        c = _counting_client()
        c.create_comment("s", POST, "hi", parent_comment_id=TOP, **kwargs)
        assert c.http.log == [("GET", f"/posts/{TOP}"), GET_GROUP, POST_COMMENT], c.http.log


def test_without_the_hint_a_reply_to_a_reply_is_still_rehung_with_the_mention():
    for kwargs in ({}, {"parent_is_top_level": False}):
        c = _counting_client()
        c.create_comment("s", POST, "danke!", parent_comment_id=REPLY, **kwargs)
        log = c.http.log
        assert log[0] == ("GET", f"/posts/{REPLY}"), log
        assert any(m == "GET" and p.startswith(f"/posts/{POST}/comments") for m, p in log), log
        assert log[-1] == POST_COMMENT, log
        (_, body), = c.http.writes
        assert body["parent_id"] == TOP, body
        assert body["metadata"]["content"] == "[@Bea B](obj://user/u2) danke!", body


def test_only_a_real_true_counts_as_the_hint():
    """A stray truthy value (1, "false") must not switch the safeguard off."""
    for value in (1, "false", "yes"):
        c = _counting_client()
        c.create_comment("s", POST, "hi", parent_comment_id=TOP, parent_is_top_level=value)
        assert ("GET", f"/posts/{TOP}") in c.http.log, (value, c.http.log)


def test_the_archived_check_still_stops_a_hinted_write():
    archived = {"id": "g1", "archived": True, "metadata": {"archived": 1}}
    c = _counting_client(archived)
    try:
        c.create_comment("s", POST, "hi", parent_comment_id=TOP, parent_is_top_level=True)
    except ValueError as e:
        assert "archived" in str(e).lower(), e
    else:
        raise AssertionError("a write into an archived community must refuse, hint or not")
    assert POST_COMMENT not in c.http.log, c.http.log


def test_the_mcp_tool_does_not_pass_the_hint_on():
    """Whoever writes through the tool cannot prove the level, so the lookup stays on."""
    os.environ["CATKNOWS_ALLOW_WRITE"] = "1"  # the write tools only exist with this
    import catknows.mcp_server as m

    seen = {}

    class _FakeClient:
        def reply_target(self, slug, post_id, parent):
            seen["reply_target"] = parent
            return {"parent_id": parent, "flattened": False, "mention": ""}

        def create_comment(self, slug, post_id, content, **kwargs):
            seen["kwargs"] = kwargs
            return {"id": "new"}

    tool = m.create_comment
    fn = tool.fn if hasattr(tool, "fn") else tool
    assert "parent_is_top_level" not in inspect.signature(fn).parameters
    real = m._get_client
    m._get_client = lambda: _FakeClient()
    try:
        fn("s", POST, "hi", parent_comment_id=TOP)  # the draft looks the target up
        assert seen.pop("reply_target") == TOP, seen
        out = fn("s", POST, "hi", parent_comment_id=TOP, confirm=True)
    finally:
        m._get_client = real
    assert out["status"] == "commented", out
    assert "parent_is_top_level" not in seen["kwargs"], seen


if __name__ == "__main__":
    test_top_level_comment_hangs_under_the_post()
    test_reply_to_top_level_stays_put()
    test_reply_to_a_reply_goes_under_its_top_level_with_a_mention()
    test_existing_mention_is_not_doubled()
    test_known_top_level_target_skips_the_lookup()
    test_the_post_with_the_hint_is_the_post_of_the_lookup_path()
    test_hint_without_a_comment_target_comments_under_the_post()
    test_without_the_hint_the_lookup_stays()
    test_without_the_hint_a_reply_to_a_reply_is_still_rehung_with_the_mention()
    test_only_a_real_true_counts_as_the_hint()
    test_the_archived_check_still_stops_a_hinted_write()
    test_the_mcp_tool_does_not_pass_the_hint_on()
    print("ok — replies to replies hang under the top-level comment, with a mention; "
          "a caller that knows the level skips the lookup")
