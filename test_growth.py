"""Self-check: the dashboard's growth numbers come through Skool's wait-token dance.

The admin dashboard (community settings -> Dashboard) asks for its numbers in
three steps, measured 2026-09-22 (docs/API.md §1.9): the first GET answers
{"token": t}, GET /wait?token=t answers plain text "in-progress" or
"completed", and the same GET again with &token=t carries the data. Skool
only shows "last 30 days", so a history exists only if we write it down.

Run: python test_growth.py    (no network, no pytest)
"""

import json
import os
import tempfile
from pathlib import Path

from catknows import client as client_mod
from catknows import snapshot
from catknows.client import SkoolClient

client_mod._WAIT_POLL_S = 0  # no real sleeping offline

DATA = {
    "/analytics-growth-overview-v2": {"data": {"num_visitors": 325, "num_signups": 15,
                                               "new_mrr": None}},
    "/analytics-overview-v2": {"data": {"num_members": 59}},
    "/analytics-v2?chart=signups_by_source": {"data": {"chart_data": {"items": [
        {"attribution": "skool", "total": 7, "percent": 0.4667},
        {"attribution": "affiliate", "total": 5, "percent": 0.3333},
        {"attribution": "direct", "total": 3, "percent": 0.2}]}}},
    "/analytics-v2?chart=members": {"data": {"chart_data": {"items": [
        {"date": "2026-09-01", "new": 15, "existing": 44, "churned": 0, "total": 59}]}}},
}


class _FakeHTTP:
    def __init__(self, waits=("in-progress", "completed")):
        self.gets, self.waits = [], list(waits)

    def get_api2(self, q):
        self.gets.append(q)
        base = q.split("/groups/gid")[1]
        if "token=" not in base:
            return {"token": "tok-" + base.split("?")[0].strip("/")}
        key = base.split("token=")[0].rstrip("?&")
        return DATA[key]

    def get_api2_text(self, q):
        self.gets.append(q)
        return self.waits.pop(0) if self.waits else "completed"

    def _no_write(self, *a, **k):
        raise AssertionError("a read sent a write")

    post_api2 = put_api2 = delete_api2 = _no_write


def _client(**kw):
    c = SkoolClient.__new__(SkoolClient)
    c.http = _FakeHTTP(**kw)
    c.group_id_for = lambda slug, for_write=False: "gid"
    return c


def test_the_token_dance_like_the_website():
    c = _client()
    out = c.growth_overview("gid")
    assert out == {"num_visitors": 325, "num_signups": 15, "new_mrr": None, "num_members": 59}, out
    g = c.http.gets
    assert g[0] == "/groups/gid/analytics-growth-overview-v2", g
    assert g[1].startswith("/wait?token=tok-") and g[2].startswith("/wait?token="), \
        "in-progress means ask again"
    assert g[3] == "/groups/gid/analytics-growth-overview-v2?token=tok-analytics-growth-overview-v2", g


def test_a_chart_comes_back_as_its_items():
    c = _client()
    items = c.analytics_chart("gid", "signups_by_source")
    assert [i["attribution"] for i in items] == ["skool", "affiliate", "direct"], items
    assert c.http.gets[-1].endswith("chart=signups_by_source&token=tok-analytics-v2"), c.http.gets


def test_a_failed_or_endless_wait_raises():
    for waits in (("failed",), ("in-progress",) * 50):
        c = _client(waits=waits)
        try:
            c.growth_overview("gid")
        except RuntimeError as e:
            assert "wait" in str(e), e
            continue
        raise AssertionError(f"wait {waits[0]!r} passed as data")


def test_unknown_chart_is_refused_before_skool():
    c = _client()
    try:
        c.analytics_chart("gid", "visitors_by_moon")
    except ValueError as e:
        assert "signups_by_source" in str(e)
    else:
        raise AssertionError("unknown chart reached Skool")
    assert c.http.gets == []


def test_the_tool_adds_the_conversion_rate():
    import catknows.mcp_server as m
    fn = lambda t: t.fn if hasattr(t, "fn") else t
    c = _client()
    old = m._get_client
    m._get_client = lambda: c
    try:
        out = fn(m.get_growth)("cat-knows-1423", charts="signups_by_source,members")
    finally:
        m._get_client = old
    assert out["period"] == "last 30 days", out
    assert out["visitors"] == 325 and out["signups"] == 15 and out["members"] == 59, out
    assert out["conversion_rate"] == 0.046, "15 / 325, like the dashboard's 4.6 %"
    assert set(out["charts"]) == {"signups_by_source", "members"}, out


def test_snapshot_writes_a_growth_row_and_refuses_an_empty_one():
    c = _client()
    row = snapshot._growth_row(c, "cat-knows-1423")
    assert row["visitors"] == 325 and row["signups"] == 15, row
    assert row["sources"] == {"skool": 7, "affiliate": 5, "direct": 3}, row

    class _Empty:
        def group_id_for(self, slug):
            return "gid"

        def growth_overview(self, gid):
            return {}

    try:
        snapshot._growth_row(_Empty(), "x")
    except RuntimeError:
        pass
    else:
        raise AssertionError("an empty growth payload became a row of nulls")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all green")
