"""Self-check: join requests are readable, searchable, and decided only on purpose.

Reading: /{slug}/-/pending.json (docs/API.md §6.5). Deciding: the website's
Approve / Decline buttons send POST /members/{member_id}/role with
{"new": "member"} or {"new": "declined"} (read from Skool's page code
2026-09-22, not yet watched live). That same endpoint changes ANY member's
role, so a decision is only ever sent for an id that is in the queue right now.

Run: python test_join_requests.py    (no network, no pytest)
"""

import json
import os

os.environ["CATKNOWS_ALLOW_WRITE"] = "1"  # the write tool only exists with this

from catknows import normalize
from catknows.client import SkoolClient

SLUG = "some-group"


def _pending(i, name, answers):
    return {"id": f"u{i}", "name": name, "firstName": name.title(), "lastName": "X",
            "metadata": {"bio": f"bio {i}"},
            "member": {"id": f"m{i}", "role": "pending", "userId": f"u{i}",
                       "searchAnswer": f"{name}@example.com",
                       "metadata": {"requestedAt": 1786008427315217000,
                                    "requestLocation": "berlin (germany)",
                                    "highRiskScore": i % 2, "numRequests": 1,
                                    "attrSrcComp": "discovery_browse_group_link",
                                    "survey": json.dumps({"survey": answers})}}}


QUEUE = [
    _pending(1, "anna", [{"question": "Why join?", "type": "text", "answer": "I build MCP tools"}]),
    _pending(2, "bob", [{"question": "Why join?", "type": "text", "answer": "cats"}]),
]


class _FakeHTTP:
    def __init__(self, queue):
        self.queue, self.gets, self.writes = queue, [], []

    def get_next(self, q, slug):
        self.gets.append(q)
        page = int(q.split("&p=")[1]) if "&p=" in q else 1
        users = self.queue if page == 1 else []
        return {"pageProps": {"users": users, "total": len(self.queue),
                              "totalPages": 1, "page": page}}

    def post_api2(self, path, body):
        self.writes.append((path, body))
        return {}

    put_api2 = delete_api2 = post_api2


def _client(queue=QUEUE):
    c = SkoolClient.__new__(SkoolClient)
    c.http = _FakeHTTP(list(queue))
    return c


def test_a_request_comes_out_flat_with_its_answers():
    r = normalize.join_request(QUEUE[0])
    assert r["member_id"] == "m1" and r["user_id"] == "u1", r
    assert r["name"] == "anna" and r["first_name"] == "Anna", r
    assert r["requested_at"].year == 2026, r
    assert r["location"] == "berlin (germany)" and r["risk_flag"] is True, r
    assert r["source"] == "discovery_browse_group_link", r
    assert r["answers"] == [{"question": "Why join?", "type": "text",
                             "answer": "I build MCP tools"}], r
    assert r["email_answer"] == "anna@example.com", r


def test_reading_the_queue_uses_the_pending_page():
    c = _client()
    data = c.join_requests(SLUG)
    assert c.http.gets == [f"/{SLUG}/-/pending.json?group={SLUG}"], c.http.gets
    assert len(data["users"]) == 2 and c.http.writes == []


def test_the_role_call_is_what_the_buttons_send():
    c = _client()
    c.decide_join_request("m1", "approve")
    c.decide_join_request("m2", "decline")
    assert c.http.writes == [("/members/m1/role", {"new": "member"}),
                             ("/members/m2/role", {"new": "declined"})], c.http.writes
    try:
        c.decide_join_request("m1", "ban")
    except ValueError:
        pass
    else:
        raise AssertionError("only approve and decline exist here")


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


def test_list_tool_searches_names_and_answers():
    c = _client()
    out = _with_client(c, lambda m: _fn(m.list_join_requests)(SLUG, query="mcp"))
    assert out["total"] == 2 and out["count"] == 1, out
    assert out["requests"][0]["name"] == "anna", "matched on the survey answer"
    out = _with_client(c, lambda m: _fn(m.list_join_requests)(SLUG, query="BOB"))
    assert [r["name"] for r in out["requests"]] == ["bob"], out


def test_a_decision_is_a_draft_until_confirmed():
    c = _client()
    out = _with_client(c, lambda m: _fn(m.review_join_request)(SLUG, "m1", "approve"))
    assert "DRAFT" in out["status"] and c.http.writes == [], out
    assert out["would_approve"]["name"] == "anna", "show who it is, not just an id"
    out = _with_client(c, lambda m: _fn(m.review_join_request)(SLUG, "m1", "approve",
                                                               confirm=True))
    assert c.http.writes == [("/members/m1/role", {"new": "member"})], c.http.writes
    assert out["status"] == "approved", out


def test_an_id_outside_the_queue_is_never_sent():
    """The role endpoint would happily 'decline' a paying member."""
    c = _client()
    for confirm in (False, True):
        out = _with_client(c, lambda m: _fn(m.review_join_request)(
            SLUG, "m-existing-member", "decline", confirm=confirm))
        assert "not in the join queue" in out["status"], out
    assert c.http.writes == [], c.http.writes


def test_the_tools_are_annotated():
    import catknows.mcp_server as m
    assert "list_join_requests" in m._READ_ONLY
    assert "review_join_request" in m._DESTRUCTIVE


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all green")
