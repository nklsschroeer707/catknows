"""Self-check: the buildId can come from outside, and a stale one heals once.

A program that already has a browser tab open on Skool knows the buildId
(`window.__NEXT_DATA__.buildId`) without loading a single HTML page. It hands
that knowledge to the HTTP layer through ``SkoolHTTP.build_id_provider``:

    provider(False)  -> the current value
    provider(True)   -> fetch it anew (the caller just saw a 404)

With a provider set, the HTML page is never loaded to find the buildId, not
even as a fallback. Without one, nothing changed (the public library users).

Run: python test_build_id_provider.py    (no network, no pytest)
"""

from catknows.http import SkoolHTTP, SkoolHTTPError

OLD, NEW = "old-build-id", "new-build-id"
NEXT = "/_next/data/"
HTML_URL = "https://www.skool.com/x"
HTML_WITH_ID = f'<html><script id="__NEXT_DATA__">{{"buildId":"{NEW}"}}</script></html>'
PAGE_OK = '{"pageProps":{"users":[{"id":"u1"}]}}'
PAGE_404 = "<html>404 Error</html>"


class _Resp:
    def __init__(self, code, text):
        self.status_code, self.text = code, text


class _Net:
    """Stands in for curl_cffi. `handler(url) -> (code, body)`; records every GET."""

    def __init__(self, handler):
        self.handler = handler
        self.gets: list[tuple[str, dict]] = []

    def get(self, url, headers=None, timeout=None):
        self.gets.append((url, dict(headers or {})))
        return _Resp(*self.handler(url))

    @property
    def urls(self) -> list[str]:
        return [u for u, _ in self.gets]

    @property
    def html_urls(self) -> list[str]:
        """Every GET that was NOT a Next.js data call, i.e. a page load."""
        return [u for u in self.urls if NEXT not in u]


class _Provider:
    """A build_id_provider. `provider(True)` switches to `fresh` and returns it."""

    def __init__(self, current, fresh=None):
        self.current, self.fresh, self.calls = current, fresh, []

    def __call__(self, refresh):
        self.calls.append(refresh)
        if refresh and self.fresh is not None:
            self.current = self.fresh
        return self.current

    @property
    def refreshes(self) -> int:
        return self.calls.count(True)


def _session():
    return type("S", (), {"cookie_header": "c=1", "auth_token": "t", "waf_token": "w"})()


def _client(handler, provider=None):
    """A real SkoolHTTP (real __init__), with the transport swapped for a fake.

    The provider is set AFTER construction, the way an outside program wraps
    `SkoolHTTP.__init__` and sets the attribute on the finished instance.
    """
    h = SkoolHTTP(_session())
    h._http = _Net(handler)
    h.build_id_provider = provider
    return h


def _by_id(ok_id, ok_body=PAGE_OK):
    """Next.js data answers 200 for `ok_id`, 404 for any other id."""
    def handler(url):
        if f"{NEXT}{ok_id}/" in url:
            return 200, ok_body
        return 404, PAGE_404
    return handler


# -- the attribute ------------------------------------------------------------

def test_default_is_none_and_attribute_is_settable_after_init():
    h = SkoolHTTP(_session())
    assert h.build_id_provider is None, "no provider unless someone sets one"
    assert SkoolHTTP.__new__(SkoolHTTP).build_id_provider is None, "no __init__ needed for the default"

    original_init = SkoolHTTP.__init__
    prov = _Provider(OLD)

    def wrapped(self, session):
        original_init(self, session)
        self.build_id_provider = prov      # set AFTER the original __init__

    SkoolHTTP.__init__ = wrapped
    try:
        wrapped_client = SkoolHTTP(_session())
    finally:
        SkoolHTTP.__init__ = original_init
    assert wrapped_client.build_id_provider is prov
    assert prov.calls == [], "nothing may be read from the provider while constructing"


def test_provider_is_read_at_call_time_not_frozen_at_construction():
    h = SkoolHTTP(_session())
    h._http = _Net(_by_id(NEW))
    first, second = _Provider(OLD), _Provider(NEW)
    h.build_id_provider = first
    h.build_id_provider = second            # replaced before any request
    assert h.build_id("x") == NEW
    assert first.calls == [] and second.calls == [False]


# -- with a provider ----------------------------------------------------------

def test_with_provider_no_html_page_is_ever_loaded():
    prov = _Provider(OLD)
    h = _client(_by_id(OLD), prov)
    data = h.get_next("/x/-/members.json?group=x", "x")
    assert data["pageProps"]["users"] == [{"id": "u1"}], data
    assert h._http.urls == [f"https://www.skool.com{NEXT}{OLD}/x/-/members.json?group=x"], h._http.urls
    assert h._http.html_urls == [], "the HTML page must not be loaded to find the buildId"
    assert prov.calls == [False] and prov.refreshes == 0


def test_build_id_returns_provider_value_without_any_request():
    prov = _Provider(OLD)
    h = _client(_by_id(OLD), prov)
    assert h.build_id("x") == OLD
    assert h.build_id("some-other-slug") == OLD
    assert h._http.gets == [], "build_id() must not touch the network with a provider"


def test_with_provider_a_call_without_community_slug_uses_it_too():
    prov = _Provider(OLD)
    h = _client(_by_id(OLD), prov)
    h.get_next("/skoolers/-/games.json")
    assert h._http.urls == [f"https://www.skool.com{NEXT}{OLD}/skoolers/-/games.json"], h._http.urls
    assert prov.calls == [False]


def test_404_asks_once_and_retries_once():
    prov = _Provider(OLD, fresh=NEW)
    h = _client(_by_id(NEW), prov)
    data = h.get_next("/x/-/members.json?group=x", "x")
    assert data["pageProps"]["users"] == [{"id": "u1"}], data
    assert prov.calls == [False, True], prov.calls
    assert h._http.urls == [f"https://www.skool.com{NEXT}{OLD}/x/-/members.json?group=x",
                            f"https://www.skool.com{NEXT}{NEW}/x/-/members.json?group=x"], h._http.urls
    assert h._http.html_urls == []
    # And the healed value is what the next call starts with.
    h.get_next("/x/-/members.json?group=x&p=2", "x")
    assert h._http.urls[-1] == f"https://www.skool.com{NEXT}{NEW}/x/-/members.json?group=x&p=2"
    assert prov.refreshes == 1, "no second refresh when the new id works"


def test_404_without_community_slug_heals_the_same_way():
    prov = _Provider(OLD, fresh=NEW)
    h = _client(_by_id(NEW), prov)
    data = h.get_next("/skoolers/-/games.json")
    assert data["pageProps"]["users"] == [{"id": "u1"}], data
    assert prov.calls == [False, True] and len(h._http.gets) == 2
    assert h._http.html_urls == []


def test_404_same_value_after_refresh_raises_without_another_request():
    prov = _Provider(OLD, fresh=OLD)        # refresh brings the same id: a real 404
    h = _client(_by_id("never"), prov)
    try:
        h.get_next("/x/-/members.json?group=x", "x")
    except SkoolHTTPError as e:
        assert e.status == 404, e.status
    else:
        raise AssertionError("a real 404 must propagate")
    assert prov.calls == [False, True], prov.calls
    assert len(h._http.gets) == 1, f"no retry when the id did not change: {h._http.urls}"


def test_404_new_value_but_404_again_stops_at_two_requests():
    prov = _Provider(OLD, fresh=NEW)
    h = _client(_by_id("never"), prov)      # old AND new both 404
    try:
        h.get_next("/x/-/members.json?group=x", "x")
    except SkoolHTTPError as e:
        assert e.status == 404, e.status
    else:
        raise AssertionError("the second 404 must propagate")
    assert prov.refreshes == 1, "never a second provider(True) in one call"
    assert len(h._http.gets) == 2, f"never a third request: {h._http.urls}"
    assert h._http.html_urls == []


def test_only_a_404_triggers_a_refresh():
    prov = _Provider(OLD, fresh=NEW)
    h = _client(lambda url: (500, "boom"), prov)
    try:
        h.get_next("/x/-/members.json?group=x", "x")
    except SkoolHTTPError as e:
        assert e.status == 500, e.status
    else:
        raise AssertionError("a 500 must propagate")
    assert prov.calls == [False], "a 500 is no reason to ask for a new buildId"
    assert len(h._http.gets) == 1


def test_empty_provider_value_is_an_error_not_an_html_fallback():
    for empty in ("", "   ", None):
        prov = _Provider(empty)
        h = _client(lambda url: (200, HTML_WITH_ID), prov)   # an HTML page WOULD have the id
        try:
            h.get_next("/x/-/members.json?group=x", "x")
        except SkoolHTTPError as e:
            msg = str(e).lower()
            assert "buildid" in msg and "provider" in msg, e
        else:
            raise AssertionError(f"{empty!r} from the provider must raise")
        assert h._http.gets == [], f"no request at all, least of all the HTML page ({empty!r})"
        try:
            h.build_id("x")
        except SkoolHTTPError:
            pass
        else:
            raise AssertionError("build_id() must raise on an empty value too")


def test_empty_provider_value_on_refresh_is_an_error_too():
    prov = _Provider(OLD, fresh="")
    h = _client(_by_id("never"), prov)
    try:
        h.get_next("/x/-/members.json?group=x", "x")
    except SkoolHTTPError as e:
        assert "buildid" in str(e).lower(), e
    else:
        raise AssertionError("an empty refreshed value must raise")
    assert prov.refreshes == 1
    assert len(h._http.gets) == 1, "the empty value must not trigger a retry"
    assert h._http.html_urls == []


def test_provider_exceptions_are_not_swallowed():
    class Boom(Exception):
        pass

    def broken(refresh):
        raise Boom("provider down")

    h = _client(_by_id(OLD), broken)
    try:
        h.get_next("/x/-/members.json?group=x", "x")
    except Boom:
        pass
    else:
        raise AssertionError("an exception from provider(False) must reach the caller")
    assert h._http.gets == [], "no fallback to the HTML page after a provider failure"

    def broken_on_refresh(refresh):
        if refresh:
            raise Boom("refresh failed")
        return OLD

    h = _client(_by_id("never"), broken_on_refresh)
    try:
        h.get_next("/x/-/members.json?group=x", "x")
    except Boom:
        pass
    else:
        raise AssertionError("an exception from provider(True) must reach the caller")
    assert len(h._http.gets) == 1 and h._http.html_urls == []


# -- without a provider: exactly as before ------------------------------------

def test_without_provider_the_html_page_is_loaded_once_as_before():
    def handler(url):
        if url == HTML_URL:
            return 200, HTML_WITH_ID
        return 200, PAGE_OK

    h = _client(handler)                    # provider stays None
    h.get_next("/x/-/members.json?group=x", "x")
    h.get_next("/x/-/members.json?group=x&p=2", "x")
    assert h._http.urls[0] == HTML_URL, h._http.urls
    assert h._http.urls[1:] == [f"https://www.skool.com{NEXT}{NEW}/x/-/members.json?group=x",
                                f"https://www.skool.com{NEXT}{NEW}/x/-/members.json?group=x&p=2"]
    assert h._build_id == NEW, "cached for the client's lifetime, as before"
    # The page load carries exactly these headers, as before.
    _, headers = h._http.gets[0]
    assert headers == {
        "Cookie": "c=1",
        "User-Agent": h._profile["ua"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }, headers


def test_without_provider_a_404_rediscovers_through_the_html_page_as_before():
    def handler(url):
        if url == HTML_URL:
            return 200, HTML_WITH_ID
        if f"{NEXT}{NEW}/" in url:
            return 200, PAGE_OK
        return 404, PAGE_404

    h = _client(handler)
    h._build_id = OLD
    data = h.get_next("/x/-/members.json?group=x", "x")
    assert data["pageProps"]["users"] == [{"id": "u1"}], data
    assert h._http.urls == [f"https://www.skool.com{NEXT}{OLD}/x/-/members.json?group=x",
                            HTML_URL,
                            f"https://www.skool.com{NEXT}{NEW}/x/-/members.json?group=x"], h._http.urls
    assert h._build_id == NEW


def test_without_provider_and_without_slug_a_404_still_raises_untouched():
    h = _client(lambda url: (404, PAGE_404))
    h._build_id = OLD
    try:
        h.get_next("/skoolers/-/games.json")
    except SkoolHTTPError as e:
        assert e.status == 404
    else:
        raise AssertionError("no slug and no provider: a 404 propagates as before")
    assert h._http.urls == [f"https://www.skool.com{NEXT}{OLD}/skoolers/-/games.json"]
    assert h._build_id == OLD, "the cached id stays when there is no way to re-discover"


def test_without_provider_a_missing_build_id_in_the_page_still_raises():
    h = _client(lambda url: (200, "<html>nothing here</html>"))
    try:
        h.build_id("x")
    except SkoolHTTPError as e:
        assert "buildId" in str(e) and e.status == 200, e
    else:
        raise AssertionError("no buildId in the page must raise, as before")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ok — buildId provider: no HTML page with a provider, one refresh and one retry per 404, "
          "empty value and provider errors raise, no provider means nothing changed")
