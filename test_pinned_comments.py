"""Self-check: pinned comments and their replies are part of a post's comments.

catnose 2026-11-01: Skool leaves pinned comments out of `post_tree` and only
returns them as `pinned_post_tree` when the first page asks `pinned=true`.
Without that the autopilot never saw them and even marked them deleted.

Run: python test_pinned_comments.py    (no network, no pytest)
"""

import catknows.client as cl
from catknows import normalize
from catknows.client import SkoolClient

cl._INTER_PAGE_DELAY_S = 0


def _node(i, kids=()):
    return {"post": {"id": i, "root_id": "P", "metadata": {"content": i}, "user": {}}, "children": list(kids)}


class FakeHttp:
    def __init__(self):
        self.calls = []

    def get_api2(self, path):
        self.calls.append(path)
        d = {"post_tree": {"children": [_node("a", [_node("a1")])]}, "last": 0}
        if "pinned=true" in path:
            d["pinned_post_tree"] = {"children": [_node("pin", [_node("pin1")])]}
        return d


def test_pinned_comes_back():
    c = SkoolClient.__new__(SkoolClient)
    c.http = FakeHttp()
    ids = [x["skool_id"] for x in normalize.comments(c.comments("P", "G"))]
    assert sorted(ids) == ["a", "a1", "pin", "pin1"], ids
    assert "pinned=true" in c.http.calls[0]


def test_no_duplicates():
    merged = {"post_tree": {"children": [_node("a")]}, "pinned_post_tree": {"children": [_node("a")]}}
    assert [x["skool_id"] for x in normalize.comments(merged)] == ["a"]


if __name__ == "__main__":
    test_pinned_comes_back()
    test_no_duplicates()
    print("ok")
