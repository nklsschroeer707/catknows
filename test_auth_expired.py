"""Self-check: confirming the email must not end on an error page.

Chris Fueger, 2026-10-08 19:50 UTC (Keycloak + dashboard logs): he clicked the
verification link, and two tabs finished the same login in the same second.
The link's tab got a code and was signed in; the original tab restarted,
Keycloak answered RESTART_AUTHENTICATION_ERROR already_logged_in and sent it
back with error=temporarily_unavailable&error_description=authentication_expired.
That tab showed "Keycloak said: temporarily_unavailable" — for an account that
was created, verified and signed in. Seen 3x since 13.09.

authentication_expired means "this login was already finished elsewhere": the
dashboard now continues instead — home if this browser is signed in, otherwise
one fresh login round (Keycloak's own session signs the user in silently). A
second expiry in a row gets a friendly page, never a redirect loop.

Run: python test_auth_expired.py    (no network, no pytest)
"""

import asyncio
import time

from starlette.requests import Request

import catknows.dashboard as d

EXPIRED = b"error=temporarily_unavailable&error_description=authentication_expired&state=s1"


def _call(query: bytes, cookie: str = ""):
    headers = [(b"cookie", cookie.encode())] if cookie else []
    scope = {"type": "http", "method": "GET", "path": "/auth/callback",
             "query_string": query, "headers": headers}
    return asyncio.run(d.callback(Request(scope)))


def test_expired_without_session_logs_in_again():
    resp = _call(EXPIRED)
    assert resp.status_code == 302, resp.status_code
    assert resp.headers["location"] == "/auth/login", resp.headers["location"]


def test_expired_with_session_goes_home():
    sid = d._new_session("sub", "a@b.c")
    resp = _call(EXPIRED, f"{d.COOKIE}={sid}")
    assert resp.status_code == 302 and resp.headers["location"] == "/", resp.headers


def test_second_expiry_is_a_friendly_page_not_a_loop():
    first = _call(EXPIRED)
    retry = first.headers["set-cookie"].split(";")[0]  # name=value
    resp = _call(EXPIRED, retry)
    body = resp.body.decode()
    assert resp.status_code != 302, "must not redirect again"
    assert "Keycloak said" not in body, body
    assert "/auth/login" in body, "offer the sign-in, don't strand the user"


def test_expiry_leaves_the_state_for_the_tab_that_won():
    d._pending["s1"] = {"verifier": "v", "exp": time.time() + 600}
    _call(EXPIRED)
    assert "s1" in d._pending, "the successful tab still needs this state"


def test_other_keycloak_errors_still_show():
    resp = _call(b"error=access_denied&state=s2")
    assert resp.status_code == 400 and "access_denied" in resp.body.decode()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok — a login finished in another tab continues instead of erroring")
