"""Self-check: a members-only page bounced to /about still says "no access".

Next.js answers a members-only route for a non-member with 200 and a redirect
stub in pageProps. Until 2026-10 that stub held only __N_REDIRECT and
__N_REDIRECT_STATUS; measured 2026-10-11 it also carries Sentry's tracing
keys (_sentryBaggage, _sentryTraceData). The gate demanded the bare two-key
shape, so every members-only tool returned an empty result instead of the
"not a member" error: get_leaderboard on a private community gave 0 rows.

Run: python test_member_gate.py    (no network, no pytest)
"""

from catknows.http import SkoolHTTP, SkoolHTTPError

SENTRY = {"_sentryBaggage": "sentry-environment=production", "_sentryTraceData": "abc-def-0"}


def _http(page_props):
    h = SkoolHTTP.__new__(SkoolHTTP)
    h._build_id = "b1"
    h._profile = {"ua": "UA", "lang": "en"}
    h._cookie_header = lambda: "auth_token=x"
    h._add_sec_ch_ua = lambda headers: None
    h.build_id = lambda slug: "b1"
    h._get_with_retry = lambda url, headers: {"pageProps": page_props, "__N_SSP": True}
    return h


def _gate(page_props):
    try:
        _http(page_props).get_next("/x/-/leaderboards.json?group=x", "x")
    except SkoolHTTPError as e:
        return e
    return None


def test_bare_stub_to_about_is_no_access():
    e = _gate({"__N_REDIRECT": "/x/about?rdref=y", "__N_REDIRECT_STATUS": 307})
    assert e is not None and e.status == 307, e


def test_stub_with_sentry_keys_is_no_access():
    e = _gate({"__N_REDIRECT": "/x/about?rdref=y", "__N_REDIRECT_STATUS": 307, **SENTRY})
    assert e is not None and "no access" in str(e), "Sentry keys hid the membership gate"


def test_navigation_redirect_passes_through():
    assert _gate({"__N_REDIRECT": "/x/classroom/abc?md=def", "__N_REDIRECT_STATUS": 307,
                  **SENTRY}) is None


def test_real_page_with_a_redirect_field_is_not_a_stub():
    assert _gate({"__N_REDIRECT": "/x/about", "allTime": {"users": []}, **SENTRY}) is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all green")
