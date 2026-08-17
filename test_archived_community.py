"""Self-check: an archived community is visible, and writes to it fail loudly.

Dan Schaad, 2026-08-17: Skool states the state twice (outer ``archived: true``
plus ``metadata.archived: 1``) and the compact record passed on neither, so an
agent could not tell a read-only community from a live one. Archiving leaves a
community readable and turns posting, commenting and liking off, which means a
write attempt failed with a Skool error that looked like a catknows bug.

Fixtures copied from a live ``self/groups`` / ``GET /groups/{slug}`` response
(`vrooms-3264`, 17.08.), not from what the payload was expected to look like.

Run: python test_archived_community.py    (no network, no pytest)
"""

from catknows.client import SkoolClient
from catknows.normalize import my_community

# Real shape, 17.08.: both spellings arrive together.
ARCHIVED_GROUP = {
    "id": "a" * 32, "name": "vrooms-3264", "archived": True, "public": True,
    "metadata": {"display_name": "vRooms - Real Connections", "archived": 1},
}
LIVE_GROUP = {"id": "b" * 32, "name": "hoomans-9944", "metadata": {}}


class _FakeHTTP:
    def __init__(self, group):
        self.group, self.calls = group, 0

    def get_api2(self, path):
        self.calls += 1
        return self.group

    def post_api2(self, path, body):
        return {"posted": body}


def _client(group):
    c = SkoolClient.__new__(SkoolClient)
    c.http = _FakeHTTP(group)
    return c


def test_compact_record_states_archived():
    assert my_community(ARCHIVED_GROUP)["archived"] is True
    assert my_community(LIVE_GROUP)["archived"] is False, "a live group must stay quiet"


def test_reads_still_work_on_an_archived_community():
    """Archiving does not hide the community — only writes are off."""
    c = _client(ARCHIVED_GROUP)
    assert c.group_id_for("vrooms-3264") == "a" * 32


def test_writes_refuse_before_touching_skool():
    c = _client(ARCHIVED_GROUP)
    for call in (lambda: c.create_post("vrooms-3264", "t", "b"),
                 lambda: c.create_comment("vrooms-3264", "p1", "b"),
                 lambda: c.create_poll("vrooms-3264", ["a", "b"]),
                 lambda: c.create_course("vrooms-3264", "t")):
        try:
            call()
            raise AssertionError("a write into an archived community must refuse")
        except ValueError as e:
            assert "archived" in str(e).lower(), e


def test_either_spelling_alone_is_enough():
    """Nothing measured says Skool always sends both, so accept either."""
    for group in ({"id": "c" * 32, "archived": True, "metadata": {}},
                  {"id": "c" * 32, "metadata": {"archived": 1}}):
        try:
            _client(group).create_post("g", "t", "b")
            raise AssertionError(f"must refuse on {group}")
        except ValueError:
            pass


def test_live_community_writes_unchanged():
    c = _client(LIVE_GROUP)
    out = c.create_post("hoomans-9944", "t", "b")
    assert out["posted"]["group_id"] == "b" * 32, out


if __name__ == "__main__":
    test_compact_record_states_archived()
    test_reads_still_work_on_an_archived_community()
    test_writes_refuse_before_touching_skool()
    test_either_spelling_alone_is_enough()
    test_live_community_writes_unchanged()
    print("ok — archived is visible, reads work, writes refuse")
