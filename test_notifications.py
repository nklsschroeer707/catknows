"""Self-check: notifications come out flat, and each points at what it's about.

The point of get_notifications is "what just happened?" followed by reading
the thing itself. So every notification that concerns a post must carry the
community slug, the post's id, its URL slug and — for a comment — the
comment's id, in exactly the shape get_post / get_post_comments take.

Reading must never mark anything read: Skool does that with its own POSTs
(/messages, /messages/{id}/read), which this path must not send.

Shapes captured live 2026-09-22 (docs/API.md §1.7); names here are made up.

Run: python test_notifications.py    (no network, no pytest)
"""

import json

from catknows import normalize
from catknows.client import SkoolClient


def _raw(action, data, created="2026-09-22T07:23:09.38925Z", unread=False):
    return {"id": "n-" + action, "created_at": created, "updated_at": created,
            "user_id": "me", "group_id": data.get("group_id", ""), "action": action,
            "unread": unread, "metadata": {"data": json.dumps({"action": action, **data})}}


MENTION = _raw("mention-comment", {
    "src_user_id": "u1", "post_id": "c0ffee11aa", "root_post_id": "b0b0b0b0",
    "group_id": "g1", "display_name": "Ann Example", "text": "mentioned you in reply",
    "group_display_name": "Some Group.", "link_href": "/[group]/[post]?p=c0ffee11",
    "link_as": "/some-group-1234/a-post-title?p=c0ffee11",
    "content": "@Niklas Example thanks for the fix", "image_url": "https://x/y",
}, unread=True)
BROADCAST = _raw("admin-post", {
    "src_user_id": "u2", "post_id": "p9", "root_post_id": "p9", "group_id": "g2",
    "display_name": "Bea Owner", "text": "(broadcast) new post",
    "group_display_name": "Other", "link_as": "/other/big-news?p=p9p9p9p9",
    "content": "x" * 900,
})
REQUEST = _raw("membership-request", {
    "group_id": "g1", "display_name": "New membership request!",
    "group_display_name": "Some Group.", "link_as": "/some-group-1234/-/pending",
    "content": "",
})


def test_a_mention_points_at_post_and_comment():
    n = normalize.notification(MENTION)
    assert n["kind"] == "mention-comment" and n["what"] == "mentioned you in reply", n
    assert n["who"] == "Ann Example" and n["who_id"] == "u1", n
    assert n["when"].year == 2026 and n["unread"] is True, n
    assert n["community"] == "some-group-1234", "slug comes from the link, not the display name"
    assert n["community_name"] == "Some Group.", n
    assert n["preview"].startswith("@Niklas Example"), n
    t = n["target"]
    assert t["post_id"] == "b0b0b0b0", "get_post_comments needs the ROOT post id"
    assert t["comment_id"] == "c0ffee11aa", "the comment itself, to find it in the thread"
    assert t["post_name"] == "a-post-title", "get_post takes the URL slug"
    assert t["url"] == "https://www.skool.com/some-group-1234/a-post-title?p=c0ffee11", t


def test_a_post_notification_has_no_comment_id():
    n = normalize.notification(BROADCAST)
    assert n["target"]["post_id"] == "p9" and n["target"]["comment_id"] == "", n
    assert len(n["preview"]) <= 281, "a preview, not the whole post"


def test_a_request_links_to_the_pending_page_without_a_post():
    n = normalize.notification(REQUEST)
    assert n["target"]["post_id"] == "" and n["target"]["post_name"] == "", n
    assert n["target"]["url"].endswith("/some-group-1234/-/pending"), n
    assert n["who_id"] == "", n


def test_a_broken_data_string_does_not_kill_the_list():
    bad = {**MENTION, "metadata": {"data": "{not json"}}
    n = normalize.notification(bad)
    assert n["kind"] == "mention-comment" and n["target"]["post_id"] == "", n


class _FakeHTTP:
    def __init__(self):
        self.gets = []

    def get_api2(self, q):
        self.gets.append(q)
        return {"messages": [MENTION], "has_more": True, "cursor": ":a:0:2026", "type": "all"}

    def _no_write(self, *a, **k):
        raise AssertionError("reading notifications sent a write (would mark them read)")

    post_api2 = put_api2 = delete_api2 = _no_write


def _client():
    c = SkoolClient.__new__(SkoolClient)
    c.http = _FakeHTTP()
    c.group_id_for = lambda slug, for_write=False: "gid-" + slug
    return c


def test_the_query_matches_what_the_website_sends():
    c = _client()
    c.notifications()
    assert c.http.gets == ["/self/notifications?limit=30&type=all"], c.http.gets


def test_filters_and_cursor():
    c = _client()
    c.notifications(limit=10, category="mentions", community_slug="cat-knows-1423",
                    cursor=":a:0:2026-09-14T16:58:41Z")
    q, = c.http.gets
    assert "type=group&group=gid-cat-knows-1423" in q, q
    assert "category=mentions" in q and "limit=10" in q, q
    assert "cursor=%3Aa%3A0%3A2026-09-14T16%3A58%3A41Z" in q, "cursor must be URL-encoded"


def test_limits_skool_would_refuse_are_caught_first():
    c = _client()
    for bad in ({"limit": 31}, {"limit": 0}, {"category": "bogus"}):
        try:
            c.notifications(**bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad} reached Skool, which answers 400")
    assert c.http.gets == []


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all green")
