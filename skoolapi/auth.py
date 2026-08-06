"""Skool authentication via a real browser (Playwright).

Skool's ``auth_token`` cookie is httpOnly, so page-context JavaScript (and
browser extensions) cannot read it. And Skool's private API sits behind an
AWS-WAF that fingerprints requests — a raw HTTP client gets 403'd on the
paginated endpoints. Both problems vanish if we drive a *real* browser: the
user logs in normally (email/password or Google/Apple SSO), and we read the
session cookies straight out of the browser's cookie jar.

This module hands the rest of the library two things:
  1. a cookie string ("auth_token=...; aws-waf-token=...") for the HTTP client
  2. the persisted browser profile, so the next run skips the login entirely
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# The cookies the API layer needs. auth_token is the session JWT; the
# aws-waf-token is Skool's WAF challenge token, set by JS after the WAF
# challenge solves in a real browser.
TARGET_COOKIES = ("auth_token", "aws-waf-token")

SKOOL_LOGIN_URL = "https://www.skool.com/login"
# Any signed-in Skool URL works to confirm login + let the WAF challenge run.
SKOOL_HOME_URL = "https://www.skool.com/"


@dataclass
class Session:
    """A resolved Skool session, ready for the API client."""

    cookie_header: str
    """Full "name=value; name=value" string for the Cookie request header."""

    auth_token: str
    """Bare JWT, also sent as `Authorization: Bearer` on api2.skool.com calls."""

    waf_token: str
    """Bare aws-waf-token value, sent as the `x-aws-waf-token` header."""

    @property
    def is_valid(self) -> bool:
        return bool(self.auth_token)


def _cookies_to_session(cookies: list[dict]) -> Session:
    """Turn Playwright's cookie list into a Session."""
    by_name = {c["name"]: c["value"] for c in cookies}
    auth_token = by_name.get("auth_token", "")
    waf_token = by_name.get("aws-waf-token", "")

    # The Cookie header carries the full jar — Skool's Next.js data endpoints
    # want more than just auth_token (e.g. the WAF token as a cookie too).
    cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    return Session(cookie_header=cookie_header, auth_token=auth_token, waf_token=waf_token)


def login(
    profile_dir: str | Path = ".skool-profile",
    *,
    headless: bool = False,
    timeout_ms: int = 300_000,
) -> Session:
    """Open a browser, let the user log in, and return the session cookies.

    The browser profile is persisted to ``profile_dir`` so subsequent runs
    reuse the session and skip the manual login. Delete that directory to log
    out / switch accounts.

    Args:
        profile_dir: Where to persist the browser profile between runs.
        headless: If True, don't show the browser window. Only works once a
            session is already persisted — the first login needs a visible
            window for the user to type credentials.
        timeout_ms: How long to wait for the user to finish logging in.

    Raises:
        RuntimeError: if login didn't complete (no auth_token cookie found).
    """
    # Imported lazily so `import skoolapi` works even before `playwright
    # install` has been run — only login() actually needs the browser.
    from playwright.sync_api import sync_playwright

    profile_dir = Path(profile_dir).expanduser().resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=headless,
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()

            # If the persisted profile is still logged in, skool.com/ loads
            # straight into the app and we can skip the wait entirely.
            page.goto(SKOOL_HOME_URL, wait_until="domcontentloaded")
            session = _cookies_to_session(ctx.cookies())

            if not session.is_valid:
                # Not logged in yet — send the user to the login page and wait
                # until the auth_token cookie appears (login complete).
                page.goto(SKOOL_LOGIN_URL, wait_until="domcontentloaded")
                print(
                    "\n  Log in to Skool in the browser window that opened.\n"
                    "  (Email/password or Google/Apple both work.)\n"
                    "  This window closes automatically once you're signed in.\n"
                )
                _wait_for_login(page, ctx, timeout_ms)

            # Re-visit home so the WAF challenge JS runs and writes a fresh
            # aws-waf-token before we snapshot the cookies for real.
            page.goto(SKOOL_HOME_URL, wait_until="networkidle")
            session = _cookies_to_session(ctx.cookies())

            if not session.is_valid:
                raise RuntimeError(
                    "Login did not complete — no auth_token cookie found. "
                    "Did you finish signing in?"
                )
            return session
        finally:
            ctx.close()


def _wait_for_login(page, ctx, timeout_ms: int) -> None:
    """Poll the cookie jar until auth_token shows up (or we time out)."""
    import time

    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        cookies = {c["name"] for c in ctx.cookies()}
        # auth_token present AND we've navigated away from the login page =
        # login finished.
        if "auth_token" in cookies and "/login" not in page.url:
            return
        page.wait_for_timeout(500)
    raise RuntimeError(f"Timed out after {timeout_ms}ms waiting for login.")


def session_from_cookie(cookie_header: str) -> Session:
    """Build a Session from a raw Cookie header copied out of DevTools.

    Escape hatch for users who don't want Playwright: open skool.com logged in,
    DevTools -> Application -> Cookies, copy the whole cookie string, pass it
    here. No browser dependency, but you re-do it whenever the session expires.
    """
    by_name: dict[str, str] = {}
    for part in cookie_header.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            by_name[k.strip()] = v.strip()
    return Session(
        cookie_header=cookie_header.strip(),
        auth_token=by_name.get("auth_token", ""),
        waf_token=by_name.get("aws-waf-token", ""),
    )
