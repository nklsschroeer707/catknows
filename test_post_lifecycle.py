"""Self-check: editing and deleting posts/comments, without touching Skool.

Endpoints captured from real browser traffic 2026-08-17 in `hoomans-9944`:

    POST   /posts/{id}/update   flat body, no metadata wrapper, no group_id
    DELETE /posts/{id}          empty body, empty 200

A comment IS a post, so both verbs serve comments too (measured: the comment
edit hit the same /update path with an empty title).

The trap this file exists for: Skool's editor sends EVERY field on every save,
so a field missing from the body is cleared. "Just change the text" would drop
the post's category and attachments. update_post reads the current values back
and only replaces what the caller passed.

Run: python test_post_lifecycle.py    (no network, no pytest)
"""

from catknows.client import SkoolClient

CURRENT = {
    "id": "p1",
    "label_id": "cat-id",
    "metadata": {"title": "old title", "content": "old body",
                 "attachments": "file1,file2", "labels": "cat-id"},
}


class _FakeHTTP:
    def __init__(self):
        self.writes, self.deletes = [], []

    def get_api2(self, path):
        return CURRENT

    def post_api2(self, path, body):
        self.writes.append((path, body))
        return {"ok": True}

    def delete_api2(self, path):
        self.deletes.append(path)
        return {}


def _client():
    c = SkoolClient.__new__(SkoolClient)
    c.http = _FakeHTTP()
    return c


def test_edit_hits_the_measured_endpoint():
    c = _client()
    c.update_post("p1", content="new body")
    (path, body), = c.http.writes
    assert path == "/posts/p1/update", path
    assert "metadata" not in body, "the captured body is flat, not wrapped"
    assert "group_id" not in body, "the id in the path is the whole address"
    assert body["content"] == "new body", body


def test_unpassed_fields_are_kept_not_cleared():
    """The whole point: an omitted field must not blank the post."""
    c = _client()
    c.update_post("p1", content="new body")
    (_, body), = c.http.writes
    assert body["title"] == "old title", f"title must survive a content-only edit: {body}"
    assert body["attachments"] == "file1,file2", f"attachments must survive: {body}"
    assert body["labels"] == "cat-id", f"category must survive: {body}"


def test_passing_a_field_replaces_it():
    c = _client()
    c.update_post("p1", title="new title", content="new body")
    (_, body), = c.http.writes
    assert body["title"] == "new title" and body["content"] == "new body", body


def test_delete_is_the_same_endpoint_for_both():
    c = _client()
    c.delete_post("p1")
    c.delete_post("c1")
    assert c.http.deletes == ["/posts/p1", "/posts/c1"], c.http.deletes


def test_tools_never_write_without_confirm():
    """Draft-first for edits, confirm-first for deletes: nothing until asked."""
    import catknows.mcp_server as m

    if not hasattr(m, "edit_post"):
        print("  (write tools not registered — set CATKNOWS_ALLOW_WRITE=1 to cover them)")
        return
    c = _client()
    real = m._get_client
    m._get_client = lambda: c
    try:
        drafted = m.edit_post("g", "p1", content="new body")
        assert "DRAFT" in drafted["status"], drafted
        assert drafted["would_edit"]["changes"]["content"]["from"] == "old body", drafted
        gone = m.delete_post("g", "p1")
        assert "NOT DELETED" in gone["status"], gone
        # The draft must name what disappears — an id alone is not reviewable.
        assert gone["would_delete"]["title"] == "old title", gone
        m.edit_comment("g", "c1", content="x")
        m.delete_comment("g", "c1")
    finally:
        m._get_client = real
    assert c.http.writes == [], f"a draft must not write: {c.http.writes}"
    assert c.http.deletes == [], f"a draft must not delete: {c.http.deletes}"


if __name__ == "__main__":
    test_edit_hits_the_measured_endpoint()
    test_unpassed_fields_are_kept_not_cleared()
    test_passing_a_field_replaces_it()
    test_delete_is_the_same_endpoint_for_both()
    test_tools_never_write_without_confirm()
    print("ok — edits keep untouched fields, drafts write nothing")
