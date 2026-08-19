"""Self-check: a 401 must not always read as "your session went stale".

Skool answers 401 for two unrelated reasons, and the advice differs:

  * a stale WAF token  -> re-login fixes it
  * "action not permitted" -> the account simply isn't in that community,
    and re-logging in will NEVER fix it

Measured 2026-08-19 on /groups/{gid}/courses and /courses/{id}: an account
outside the community gets 401 "action not permitted", while every course in
a community it IS in answers 200 across all privacy levels. Des chased the
wrong advice twice before this was told apart.

Run: python test_not_permitted.py    (no network, no pytest)
"""

from catknows.http import SkoolHTTP, SkoolHTTPError

URL = "https://api2.skool.com/courses/abc?withChildren=true"


def _client(code, body):
    """A SkoolHTTP whose transport always answers (code, body)."""
    http = SkoolHTTP.__new__(SkoolHTTP)  # no login, no real session
    http._build_id = "b"
    http._cache = {}
    http._profile = {"ua": "test", "lang": "en", "sec_ch_ua": ""}
    http._cookie_header = lambda: "test"

    class Resp:
        def __init__(self, code, body):
            self.status_code, self.text = code, body

    class Transport:
        # the write path uses post/put/delete (and passes json=), the read path
        # get — every one of them rejects the same way
        def get(self, url, headers=None, timeout=None, json=None):
            return Resp(code, body)

        post = put = delete = get

    http._http = Transport()

    class Session:
        auth_token = waf_token = ""
        cookie_header = "test"

    http.session = Session()
    return http


def _fails_with(http, write: bool) -> str:
    try:
        if write:
            http.post_api2("/courses", {})
        else:
            http.get_api2("/courses/abc?withChildren=true")
    except SkoolHTTPError as e:
        return str(e)
    raise AssertionError("a 401 must raise")


def main() -> None:
    # 1. The membership gate: say so, and do NOT send them to the login page.
    for write in (False, True):
        msg = _fails_with(_client(401, "action not permitted"), write)
        where = "write" if write else "read"
        assert "isn't in that community" in msg, f"{where}: must name the real cause: {msg}"
        assert "re-logging in will not change this" in msg.lower(), \
            f"{where}: must rule out the re-login dead end: {msg}"
        assert "forget_skool_session" not in msg, \
            f"{where}: must NOT prescribe a re-login here: {msg}"

    # 2. A genuine stale session keeps the old, correct advice.
    for write in (False, True):
        msg = _fails_with(_client(401, "<html>WAF challenge</html>"), write)
        where = "write" if write else "read"
        assert "gone stale" in msg, f"{where}: stale sessions still say so: {msg}"
        assert "forget_skool_session" in msg, f"{where}: keep the re-login fix: {msg}"

    # 3. 403 takes the same fork — Skool uses both codes for the gate.
    msg = _fails_with(_client(403, "action not permitted"), False)
    assert "isn't in that community" in msg, f"403 must fork too: {msg}"

    # 4. Case must not decide the outcome.
    msg = _fails_with(_client(401, "Action Not Permitted"), False)
    assert "isn't in that community" in msg, f"matching must be case-insensitive: {msg}"

    # 5. course_tree falls back to the classroom page when api2 slams the door,
    #    but ONLY with a slug to fall back to — and a failed fallback must
    #    surface the original 401, never a None.
    _course_tree_fallback()

    print("ok: 401 tells a membership gate apart from a stale session")


def _course_tree_fallback() -> None:
    """api2 401 + a slug -> read it the way the website does."""
    from catknows.client import SkoolClient

    TREE = {"course": {"id": "c1"}, "children": [{"course": {"id": "m1"}}]}

    def _client(page_tree):
        c = SkoolClient.__new__(SkoolClient)
        c.http = None  # never touched: api2 is stubbed to fail
        c.http_calls = []

        def api2(_path):
            raise SkoolHTTPError("HTTP 401 — action not permitted", 401)

        class H:
            get_api2 = staticmethod(api2)

        c.http = H()
        c._course_tree_via_page = lambda slug, cid: page_tree
        return c

    got = _client(TREE).course_tree("c1", "somecommunity")
    assert got == TREE, f"a visible course must come back through the page: {got}"

    # No slug -> nothing to fall back to, the 401 stands.
    try:
        _client(TREE).course_tree("c1")
        raise AssertionError("without a slug the 401 must surface")
    except SkoolHTTPError as e:
        assert e.status == 401

    # Slug given but the course isn't on the page either -> the 401, not None.
    try:
        _client(None).course_tree("c1", "somecommunity")
        raise AssertionError("an invisible course must raise, not return None")
    except SkoolHTTPError as e:
        assert e.status == 401


if __name__ == "__main__":
    main()
