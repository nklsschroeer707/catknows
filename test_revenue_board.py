"""Self-check: the skoolers revenue leaderboard ("games") comes out readable.

skool.com/skoolers/-/games ranks Skool's top-earning communities (docs/API.md
§6.1): 100 rows overall, 50 per category via &category={id}; p=2 is ignored,
so that is all there is. mrr is in CENTS and arrives as a float. Measured
2026-09-22, Hobbies id from Niklas' link.

Run: python test_revenue_board.py    (no network, no pytest)
"""

from catknows import normalize
from catknows.client import SkoolClient

HOBBIES = "8a7678583d3246a1a1a0a4a994321146"
CATS = [{"id": HOBBIES, "name": "🎨 Hobbies"}, {"id": "b" * 32, "name": "💻 Tech"}]
ROW = {"globalRank": 46, "categoryRank": 1, "category": "🎨 Hobbies",
       "mrr": 3502050.5, "mrrGrowth": -12000.0, "traffic": 3880,
       "user": {"id": "u1", "name": "mario-vreco", "firstName": "Mario", "lastName": "Vreco",
                "metadata": {"mrrStatus": "crown", "actStatus": ""}},
       "group": {"id": "g1", "name": "talasakademija",
                 "metadata": {"displayName": "Talas Akademija", "logoUrl": "x"}}}


class _FakeHTTP:
    def __init__(self):
        self.gets = []

    def get_next(self, q, slug):
        self.gets.append((q, slug))
        return {"pageProps": {"rows": [ROW] * (50 if "category=" in q else 100),
                              "categories": CATS}}


def _client():
    c = SkoolClient.__new__(SkoolClient)
    c.http = _FakeHTTP()
    return c


def test_a_row_is_dollars_badge_and_link():
    r = normalize.revenue_row(ROW)
    assert r["global_rank"] == 46 and r["category_rank"] == 1, r
    assert r["community"] == "talasakademija" and r["community_name"] == "Talas Akademija", r
    assert r["owner"] == "Mario Vreco" and r["owner_handle"] == "mario-vreco", r
    assert r["mrr_usd"] == 35021 and r["mrr_growth_usd"] == -120, "cents -> whole dollars"
    assert r["owner_badge"] == "👑 crown ($30k+)", r
    assert r["traffic"] == 3880 and r["url"] == "https://www.skool.com/talasakademija", r


def test_unknown_badge_passes_through_and_none_is_empty():
    odd = {**ROW, "user": {**ROW["user"], "metadata": {"mrrStatus": "unicorn"}}}
    assert normalize.revenue_row(odd)["owner_badge"] == "unicorn"
    none = {**ROW, "user": {**ROW["user"], "metadata": {}}}
    assert normalize.revenue_row(none)["owner_badge"] == ""


def test_board_queries_like_the_website():
    c = _client()
    c.revenue_leaderboard()
    c.revenue_leaderboard(category_id=HOBBIES)
    assert c.http.gets == [("/skoolers/-/games.json?group=skoolers", "skoolers"),
                           (f"/skoolers/-/games.json?group=skoolers&category={HOBBIES}",
                            "skoolers")], c.http.gets


def test_the_tool_takes_the_category_by_name():
    import catknows.mcp_server as m
    fn = lambda t: t.fn if hasattr(t, "fn") else t
    c = _client()
    old = m._get_client
    m._get_client = lambda: c
    try:
        out = fn(m.get_revenue_leaderboard)(category="hobbies", limit=10)
        try:
            fn(m.get_revenue_leaderboard)(category="knitting")
        except ValueError as e:
            assert "Tech" in str(e), "say which categories exist"
        else:
            raise AssertionError("unknown category silently ignored")
    finally:
        m._get_client = old
    assert f"category={HOBBIES}" in c.http.gets[1][0], c.http.gets
    assert out["category"] == "🎨 Hobbies" and out["count"] == 10 and out["available"] == 50, out
    assert out["categories"] == ["🎨 Hobbies", "💻 Tech"], out


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all green")
