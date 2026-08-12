"""Self-check: a stale Next.js buildId must heal itself, once.

Skool changes its buildId on every frontend deploy. We cache it, so a
long-running server used to 404 every request until someone restarted it
(2026-08-12 outage). `get_next` now re-discovers the id and retries.

Run: python test_build_id_retry.py    (no network, no pytest)
"""

from catknows.http import SkoolHTTP, SkoolHTTPError

STALE, FRESH = "old-build-id", "new-build-id"


def _client(responses):
    """A SkoolHTTP whose transport is `responses`: url-substring -> (code, body)."""
    http = SkoolHTTP.__new__(SkoolHTTP)  # no login, no real session
    http._build_id = STALE
    http._cache = {}
    http._profile = {"ua": "test", "lang": "en", "sec_ch_ua": ""}
    http._cookie_header = lambda: "test"
    http.build_id = lambda slug: http._build_id or _set(http, FRESH)
    http.calls = []

    class Resp:
        def __init__(self, code, body):
            self.status_code, self.text = code, body

    def get(url, headers=None, timeout=None):
        http.calls.append(url)
        for needle, (code, body) in responses.items():
            if needle in url:
                return Resp(code, body)
        return Resp(404, "<html>404</html>")

    http._http = type("T", (), {"get": staticmethod(get)})()
    return http


def _set(http, value):
    http._build_id = value
    return value


def test_stale_build_id_retries_and_heals():
    c = _client({STALE: (404, "<html>404 Error</html>"),
                 FRESH: (200, '{"pageProps":{"users":[{"id":"u1"}]}}')})
    data = c.get_next("/x/-/members.json?group=x", "x")
    assert data["pageProps"]["users"] == [{"id": "u1"}], data
    assert c._build_id == FRESH, c._build_id
    assert len(c.calls) == 2, c.calls  # exactly one retry, not a loop


def test_real_404_still_raises():
    """Same id after re-discovery = genuinely missing, so don't swallow it."""
    c = _client({STALE: (404, "<html>404 Error</html>")})
    c.build_id = lambda slug: STALE  # deploy didn't happen; id is unchanged
    try:
        c.get_next("/x/-/members.json?t=active&group=x", "x")
    except SkoolHTTPError as e:
        assert e.status == 404, e.status
        return
    raise AssertionError("a real 404 must propagate")


if __name__ == "__main__":
    test_stale_build_id_retries_and_heals()
    test_real_404_still_raises()
    print("ok — stale buildId heals once, real 404s still raise")
