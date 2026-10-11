"""Self-check: a community's leaderboards come out flat, per period, without secrets.

skool.com/{slug}/-/leaderboards (docs/API.md §1.10) ships all three boards in one
page payload: allTime, past30Days, past7Days, each the top 10 and nothing more
(the page has no "load more"). The viewer's own standing sits in selfAllTime /
self30Days / self7Days (null when Skool doesn't rank them: owners and admins,
or no points in that period); their level comes from self.metadata.spData,
which exists even then. Custom level names live in `title`, `name` is always
"Level N". Every row carries the full Skool user object, and the viewer's own
carries payoutAccountId and aflCode, so nothing may be passed through raw.
Shapes measured 2026-10-11 in cat-knows-1423 (owner) and skoolers (member).

Run: python test_leaderboard.py    (no network, no pytest)
"""

import json

from catknows import normalize
from catknows.client import SkoolClient

SECRET = "acct_1RQ7M6GbmVshHlaF"


def _row(rank, handle, first, last, points, sp):
    return {"userId": handle + "-id", "rank": rank, "points": points,
            "updatedAt": "2026-10-11T21:13:07Z",
            "user": {"id": handle + "-id", "name": handle, "firstName": first, "lastName": last,
                     "email": "", "metadata": {"bio": "long bio", "spData": json.dumps(sp)}}}


ME = {"id": "me-id", "name": "niklas", "firstName": "Niklas", "lastName": "Schröer",
      "metadata": {"payoutAccountId": SECRET, "aflCode": "e3fa7a09",
                   "spData": '{"pts":335,"lv":5,"pcl":155,"pnl":515,"role":4}'}}

PP = {
    "allTime": {"type": "all", "offset": 0, "limit": 10, "updatedAt": "2026-10-11T21:13:07Z",
                "users": [_row(1, "noob", "Noob", "Master", 116594,
                               {"pts": 116594, "lv": 9, "pcl": 33015, "pnl": 133015}),
                          _row(2, "sarah", "Sarah", "S", 103813,
                               {"pts": 103813, "lv": 9, "pcl": 33015, "pnl": 133015})]},
    "past30Days": {"type": "30d", "offset": 0, "limit": 10, "updatedAt": "2026-10-11T21:13:07Z",
                   "users": [_row(1, "jenna", "Jenna", "J", 3645,
                                  {"pts": 91539, "lv": 9, "pcl": 33015, "pnl": 133015})]},
    "past7Days": {"type": "7d", "offset": 0, "limit": 10, "updatedAt": "2026-10-11T21:13:07Z",
                  "users": [_row(1, "jemjaf", "Jem", "Jaf", 1002,
                                 {"pts": 5000, "lv": 7, "pcl": 2015, "pnl": 8015})]},
    "selfAllTime": {"userId": "me-id", "rank": 2214, "points": 335,
                    "updatedAt": "2026-10-11T21:13:07Z", "user": ME},
    "self30Days": {"userId": "me-id", "rank": 2855, "points": 4,
                   "updatedAt": "2026-10-11T21:13:07Z", "user": ME},
    "self7Days": None,
    "self": ME,
    "groupLevels": [{"number": 1, "name": "Level 1", "title": "🥚 AI Rookie", "percentOfMembers": 84},
                    {"number": 2, "name": "Level 2", "title": "",
                     "actions": [{"name": "members_post"}], "percentOfMembers": 5},
                    {"number": 9, "name": "Level 9", "title": "", "percentOfMembers": 1}],
}


class _FakeHTTP:
    def __init__(self):
        self.gets = []

    def get_next(self, q, slug):
        self.gets.append((q, slug))
        return {"pageProps": PP}


def _client():
    c = SkoolClient.__new__(SkoolClient)
    c.http = _FakeHTTP()
    return c


def test_client_reads_the_page_like_the_website():
    c = _client()
    pp = c.leaderboards("skoolers")
    assert c.http.gets == [("/skoolers/-/leaderboards.json?group=skoolers", "skoolers")], c.http.gets
    assert pp["allTime"]["users"], "raw pageProps expected"


def test_all_time_rows_are_flat():
    out = normalize.leaderboard(PP, "all")
    assert out["period"] == "all" and out["updated_at"] == "2026-10-11T21:13:07Z", out
    assert out["rows"][0] == {"rank": 1, "handle": "noob", "name": "Noob Master",
                              "points": 116594, "level": 9}, out["rows"][0]
    assert len(out["rows"]) == 2


def test_period_picks_its_board_and_period_points():
    thirty = normalize.leaderboard(PP, "30d")
    assert [r["handle"] for r in thirty["rows"]] == ["jenna"], thirty
    assert thirty["rows"][0]["points"] == 3645, "points earned in the period, not total"
    assert thirty["me"]["rank"] == 2855 and thirty["me"]["points"] == 4, thirty["me"]
    seven = normalize.leaderboard(PP, "7d")
    assert seven["rows"][0]["handle"] == "jemjaf", seven


def test_my_level_and_points_to_next():
    me = normalize.leaderboard(PP, "all")["me"]
    assert me["rank"] == 2214 and me["points"] == 335, me
    assert me["level"] == 5 and me["total_points"] == 335, me
    assert me["next_level_at"] == 515 and me["points_to_next_level"] == 180, me


def test_unranked_viewer_still_gets_level_and_a_reason():
    me = normalize.leaderboard(PP, "7d")["me"]
    assert me["rank"] is None and me["points"] is None, me
    assert me["level"] == 5 and me["points_to_next_level"] == 180, me
    assert "not ranked" in me["note"], me


def test_top_level_has_no_next_level():
    top = dict(PP, self={**ME, "metadata": {"spData": '{"pts":200000,"lv":9,"pcl":33015,"pnl":133015}'}})
    me = normalize.leaderboard(top, "all")["me"]
    assert me["level"] == 9 and me["next_level_at"] is None and me["points_to_next_level"] is None, me


def test_levels_use_custom_titles_and_unlocks():
    lv = normalize.leaderboard(PP, "all")["levels"]
    assert lv[0] == {"level": 1, "name": "🥚 AI Rookie", "percent_of_members": 84, "unlocks": []}, lv[0]
    assert lv[1] == {"level": 2, "name": "Level 2", "percent_of_members": 5,
                     "unlocks": ["members_post"]}, lv[1]


def test_no_secret_or_profile_blob_leaks():
    for period in ("all", "30d", "7d"):
        blob = json.dumps(normalize.leaderboard(PP, period), ensure_ascii=False)
        assert SECRET not in blob and "aflCode" not in blob, period
        assert "long bio" not in blob and "metadata" not in blob, period


def test_tool_checks_the_period():
    import catknows.mcp_server as m
    fn = lambda t: t.fn if hasattr(t, "fn") else t
    c = _client()
    old = m._get_client
    m._get_client = lambda: c
    try:
        out = fn(m.get_leaderboard)("skoolers", period="30d")
        try:
            fn(m.get_leaderboard)("skoolers", period="year")
        except ValueError as e:
            assert "7d" in str(e) and "30d" in str(e) and "all" in str(e), e
        else:
            raise AssertionError("unknown period silently ignored")
    finally:
        m._get_client = old
    assert out["community"] == "skoolers" and out["period"] == "30d", out
    assert out["rows"][0]["handle"] == "jenna" and "top 10" in out["shown"], out
    assert "get_leaderboard" in m._READ_ONLY


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all green")
