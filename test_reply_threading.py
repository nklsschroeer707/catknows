"""Self-check: a reply to a reply is re-hung under its top-level comment.

Skool threads are two levels deep. Dan's local write test (2026-09-15) sent 7
replies: the 5 under top-level comments survived, the 2 under a *reply* were
created, counted, then shown as "Comment was deleted". The same texts posted by
hand stayed — Skool's own reply button posts under the top-level comment and
opens with an @-mention. create_comment must do the same.

Run: python test_reply_threading.py    (no network, no pytest)
"""

from catknows.client import SkoolClient, with_mention

POST, TOP, REPLY = "post1", "top1", "reply1"
BY_ID = {
    TOP: {"id": TOP, "parent_id": POST, "root_id": POST, "user_id": "u1"},
    REPLY: {"id": REPLY, "parent_id": TOP, "root_id": POST, "user_id": "u2"},
}
TREE = {"post_tree": {"children": [
    {"post": {"id": TOP, "user": {"id": "u1", "first_name": "Ann", "last_name": "A"}},
     "children": [{"post": {"id": REPLY, "user": {"id": "u2", "name": "fabiola",
                                                  "first_name": "Fabiola", "last_name": "B"}},
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
    assert body["metadata"]["content"] == "[@Fabiola B](obj://user/u2) danke!", body


def test_existing_mention_is_not_doubled():
    m = "[@Fabiola B](obj://user/u2) "
    already = "[@Fabiola B](obj://user/u2) schon erwähnt"
    assert with_mention(m, already) == already
    assert with_mention("", "x") == "x"


if __name__ == "__main__":
    test_top_level_comment_hangs_under_the_post()
    test_reply_to_top_level_stays_put()
    test_reply_to_a_reply_goes_under_its_top_level_with_a_mention()
    test_existing_mention_is_not_doubled()
    print("ok — replies to replies hang under the top-level comment, with a mention")
