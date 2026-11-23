"""Self-check: a page or folder can be updated — Skool's empty 400 keeps its status.

``GET /courses/{id}`` for a folder or page answers **400 with an empty body**
(docs/API.md §7): that is how ``update_course_item`` tells "not a root unit"
and sends the flat PUT. The request layer used to read every empty body as
"still building", retried it and raised with status 0 — the mark of a network
error, after which no PUT goes out. Every page update failed that way
(reported 2026-11-23). An empty 4xx is an answer, not a deferral: it is raised
at once, with its status.

Run: python test_page_update_empty_400.py    (no network, no pytest)
"""

import catknows.http as h
from catknows.client import SkoolClient
from catknows.http import SkoolHTTP, SkoolHTTPError

h.RETRY_202_DELAY_S = 0


def _http(code, body):
    """A real SkoolHTTP whose transport answers every GET with (code, body)."""
    http = SkoolHTTP.__new__(SkoolHTTP)  # no login, no real session
    http._cache = {}
    http._profile = {"ua": "test", "lang": "en", "sec_ch_ua": "", "platform": ""}
    http._cookie_header = lambda: "test"
    http._api2_headers = lambda: {}
    http.gets, http.puts = [], []

    class Resp:
        status_code, text = code, body

    def get(url, headers=None, timeout=None):
        http.gets.append(url)
        return Resp()

    http._http = type("T", (), {"get": staticmethod(get)})()
    http.put_api2 = lambda path, fields: http.puts.append((path, fields)) or fields
    return http


def test_empty_400_is_raised_once_with_its_status():
    http = _http(400, "")
    try:
        http.get_api2("/courses/m1")
    except SkoolHTTPError as e:
        assert e.status == 400, (e.status, str(e))
    else:
        raise AssertionError("an empty 400 must raise")
    assert len(http.gets) == 1, http.gets  # an answer, not a deferral: no retry


def test_page_update_sends_the_flat_put():
    http = _http(400, "")
    c = SkoolClient.__new__(SkoolClient)
    c.http = http
    c.update_course_item("m1", {"title": "t", "desc": "plain"})
    assert len(http.puts) == 1, http.puts
    path, fields = http.puts[0]
    assert path == "/courses/m1" and fields["title"] == "t", http.puts
    assert fields["desc"].startswith("[v2]"), fields


def test_empty_200_and_202_are_still_retried_and_stay_status_0():
    for code in (200, 202):
        http = _http(code, "")
        try:
            http.get_api2("/courses/m1")
        except SkoolHTTPError as e:
            assert not e.status, (code, e.status)
        else:
            raise AssertionError(f"{code} with an empty body must raise")
        assert len(http.gets) == h.MAX_RETRIES_202, (code, http.gets)


if __name__ == "__main__":
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in tests:
        try:
            fn()
        except Exception as e:
            failed.append(name)
            print(f"FAIL {name}: {type(e).__name__}: {str(e)[:300]}")
    if failed:
        raise SystemExit(f"{len(failed)} of {len(tests)} checks failed")
    print(f"ok — {len(tests)} checks: an empty 400 keeps its status, pages update")
