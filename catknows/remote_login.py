"""Streamed remote login: a server-side browser, driven from the user's screen.

Skool's ``auth_token`` is httpOnly and its WAF challenge only solves in a real
browser, so there is no OAuth redirect to borrow (plan §2a). Instead the server
opens Skool's *own* login page in a real Chromium here, streams it to the user,
and takes the cookie out of the jar afterwards. The user types their password
into Skool's page — Google/Apple SSO works for the same reason — and never into
anything of ours.

The transport is CDP screencast over a WebSocket: Chromium already encodes JPEG
frames for us (``Page.startScreencast``) and already accepts synthetic input
(``Input.dispatchMouseEvent``). That is the whole protocol. No WebRTC stack, no
TURN server, no second service to run — Playwright and Chromium are already
dependencies, and Xvfb is already installed.

ponytail: ~15 fps JPEG, not WebRTC/H.264. Right for a 30-second login; swap in
Neko or a real WebRTC pipeline if this ever has to carry a long session.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .auth import SKOOL_HOME_URL, SKOOL_LOGIN_URL, _cookies_to_session

# The box runs 4 GB with no swap and Keycloak already holds ~600 MB, so a burst
# of logins must be refused rather than OOM-killed halfway through someone's
# password entry. Chromium measures ~300-400 MB per instance here.
MAX_SESSIONS = int(os.environ.get("CATKNOWS_LOGIN_MAX_SESSIONS", "2"))

# A login is seconds of work. Anything still open after this is an abandoned tab.
SESSION_TTL = float(os.environ.get("CATKNOWS_LOGIN_TTL", "300"))

VIEWPORT = {"width": 420, "height": 760}  # phone-shaped: §2a wants mobile to work


class LoginError(RuntimeError):
    """Something the user should be told, verbatim."""


@dataclass
class LoginSession:
    """One user's in-flight browser login."""

    subject: str
    playwright: Any
    context: Any
    page: Any
    cdp: Any
    started: float = field(default_factory=time.monotonic)
    frames: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=2))
    done: bool = False

    @property
    def age(self) -> float:
        return time.monotonic() - self.started

    async def close(self) -> None:
        # Each step is independent: a dead browser must not leave Playwright
        # running, so failures here are swallowed one at a time.
        for shut in (
            lambda: self.cdp.detach(),
            lambda: self.context.close(),
            lambda: self.playwright.stop(),
        ):
            with contextlib.suppress(Exception):
                await shut()


class LoginManager:
    """Owns the live browser sessions, one per OAuth subject."""

    def __init__(self) -> None:
        self._sessions: dict[str, LoginSession] = {}
        self._lock = asyncio.Lock()

    def profile_dir(self, subject: str) -> Path:
        """Per-user Chromium profile, so a refresh later runs headless (§2a).

        Named after a hash of the subject for the same reason the session store
        is: no user id in a path, and no way to escape the base directory.
        """
        import hashlib

        base = Path(
            os.environ.get("CATKNOWS_PROFILE_DIR", "/var/lib/catknows/skool-profile")
        )
        name = hashlib.sha256(subject.encode()).hexdigest()[:32]
        return base / name

    async def _reap(self) -> None:
        """Drop sessions past their TTL. Called before every admission check.

        Skips ``None`` entries: those are slots reserved by a start() that is
        still booting its browser, and reaping one would let a third login in
        past the cap.
        """
        for subject, s in list(self._sessions.items()):
            if s is None:
                continue
            if s.done or s.age > SESSION_TTL:
                await s.close()
                self._sessions.pop(subject, None)

    async def start(self, subject: str) -> LoginSession:
        """Open Skool's login page for this user and begin streaming it."""
        if not subject:
            raise LoginError("No verified user on this request.")

        async with self._lock:
            await self._reap()

            # Restarting replaces the old session rather than stacking a second
            # browser: a user who reloads the page must not consume two slots.
            if old := self._sessions.pop(subject, None):
                await old.close()

            if len(self._sessions) >= MAX_SESSIONS:
                raise LoginError(
                    "Too many logins in progress right now. Try again in a minute — "
                    "this server keeps only a couple of browsers open at once."
                )
            # Reserve the slot while we boot, so two parallel starts can't both
            # pass the check above.
            self._sessions[subject] = None  # type: ignore[assignment]

        try:
            session = await self._launch(subject)
        except BaseException:
            async with self._lock:
                self._sessions.pop(subject, None)
            raise

        async with self._lock:
            self._sessions[subject] = session
        return session

    async def _launch(self, subject: str) -> LoginSession:
        from playwright.async_api import async_playwright

        pw = await async_playwright().start()
        try:
            profile = self.profile_dir(subject)
            profile.mkdir(parents=True, exist_ok=True)
            ctx = await pw.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                headless=True,  # Xvfb is there, but headless screencasts fine
                viewport=VIEWPORT,
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()

            # A persisted profile may still be logged in — then there is nothing
            # to stream and we can hand back the cookie immediately.
            await page.goto(SKOOL_HOME_URL, wait_until="domcontentloaded")
            if not _cookies_to_session(await ctx.cookies()).is_valid:
                await page.goto(SKOOL_LOGIN_URL, wait_until="domcontentloaded")

            cdp = await ctx.new_cdp_session(page)
            session = LoginSession(
                subject=subject, playwright=pw, context=ctx, page=page, cdp=cdp
            )

            def on_frame(event: dict) -> None:
                # Ack first: Chromium throttles the stream until we do, and an
                # un-acked frame stalls the whole screencast.
                sid = event.get("sessionId")
                if sid is not None:
                    asyncio.ensure_future(
                        _suppress(cdp.send("Page.screencastFrameAck", {"sessionId": sid}))
                    )
                # Drop frames instead of queueing them: a slow phone should see
                # the newest picture, not a backlog of stale ones.
                if session.frames.full():
                    with contextlib.suppress(asyncio.QueueEmpty):
                        session.frames.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    session.frames.put_nowait(event.get("data", ""))

            cdp.on("Page.screencastFrame", on_frame)
            await cdp.send(
                "Page.startScreencast",
                {"format": "jpeg", "quality": 60, "maxWidth": VIEWPORT["width"],
                 "maxHeight": VIEWPORT["height"], "everyNthFrame": 1},
            )
            return session
        except BaseException:
            with contextlib.suppress(Exception):
                await pw.stop()
            raise

    def get(self, subject: str) -> LoginSession | None:
        s = self._sessions.get(subject)
        return s if s and not s.done else None

    async def input(self, subject: str, msg: dict) -> None:
        """Forward one user gesture into the browser."""
        s = self.get(subject)
        if s is None:
            raise LoginError("No login in progress.")
        kind = msg.get("type")

        if kind in ("mousePressed", "mouseReleased", "mouseMoved"):
            await s.cdp.send("Input.dispatchMouseEvent", {
                "type": kind,
                "x": float(msg.get("x", 0)), "y": float(msg.get("y", 0)),
                "button": msg.get("button", "left"),
                "clickCount": 1 if kind == "mousePressed" else 0,
            })
        elif kind == "insertText":
            # What a touchscreen keyboard produces. It reports finished text via
            # `input` events, not per-key codes, so there is no keyCode to
            # forward — and autocorrect or an IME can deliver several characters
            # at once. insertText takes them verbatim.
            await s.cdp.send("Input.insertText", {"text": str(msg.get("text", ""))})
        elif kind in ("keyDown", "keyUp", "char"):
            # `text` is what actually types a character; `key`/`code` drive
            # Enter, Tab and friends. Passing both covers either case.
            await s.cdp.send("Input.dispatchKeyEvent", {
                "type": kind,
                "text": msg.get("text", ""),
                "key": msg.get("key", ""),
                "code": msg.get("code", ""),
                "windowsVirtualKeyCode": int(msg.get("keyCode", 0) or 0),
            })
        elif kind == "scroll":
            await s.cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseWheel",
                "x": float(msg.get("x", 0)), "y": float(msg.get("y", 0)),
                "deltaX": float(msg.get("deltaX", 0)),
                "deltaY": float(msg.get("deltaY", 0)),
            })
        else:
            raise LoginError(f"Unknown input type: {kind!r}")

    async def check_login(self, subject: str) -> str | None:
        """Return the finished cookie header, or None while still logging in.

        Mirrors auth.py's completion test: the cookie is there *and* we have
        left the login page. Then a revisit lets the WAF challenge write a
        fresh aws-waf-token, without which the paginated endpoints 403.
        """
        s = self.get(subject)
        if s is None:
            return None
        cookies = await s.context.cookies()
        names = {c["name"] for c in cookies}
        if "auth_token" not in names or "/login" in s.page.url:
            return None

        await s.page.goto(SKOOL_HOME_URL, wait_until="domcontentloaded")
        await s.page.wait_for_timeout(2500)  # let the WAF JS run
        session = _cookies_to_session(await s.page.context.cookies())
        return session.cookie_header if session.is_valid else None

    async def whoami(self, subject: str) -> dict:
        """Who just logged in, read out of Skool's own page state.

        Plan §2a wants the connected Skool account checked against the catknows
        account, so nobody parks a foreign session in their own tenant. Skool's
        auth_token carries only an opaque user_id — no email — so the identity
        has to come from the logged-in page while the browser is still open.

        Returns whatever it can find; callers must treat every field as
        optional. Skool's Next.js payload is not an API contract, and a layout
        change must degrade the check, never break the login.
        """
        s = self.get(subject)
        if s is None:
            return {}
        with contextlib.suppress(Exception):
            found = await s.page.evaluate(
                """() => {
                    const walk = (o, d) => {
                      if (!o || d > 6) return null;
                      if (typeof o === 'object') {
                        if (o.email && (o.name || o.id)) return o;
                        for (const k of Object.keys(o)) {
                          const hit = walk(o[k], d + 1);
                          if (hit) return hit;
                        }
                      }
                      return null;
                    };
                    const d = window.__NEXT_DATA__;
                    const u = d ? walk(d.props, 0) : null;
                    return u ? {email: u.email, name: u.name || '', id: u.id || ''} : {};
                }"""
            )
            if isinstance(found, dict):
                return {k: v for k, v in found.items() if v}
        return {}

    async def finish(self, subject: str) -> None:
        """Tear down after a completed or abandoned login."""
        async with self._lock:
            if s := self._sessions.pop(subject, None):
                s.done = True
                await s.close()

    async def shutdown(self) -> None:
        async with self._lock:
            for s in self._sessions.values():
                if s:
                    await s.close()
            self._sessions.clear()

    @property
    def active(self) -> int:
        return sum(1 for s in self._sessions.values() if s)


async def _suppress(awaitable: Any) -> None:
    with contextlib.suppress(Exception):
        await awaitable


manager = LoginManager()


async def refresh_cookie(subject: str) -> str | None:
    """Headless re-visit of this user's persisted Chromium profile, so Skool's
    WAF JS writes a fresh ``aws-waf-token`` into the jar.

    The stored session's WAF token ages while the year-long login stays valid,
    and a stale token degrades exactly the paginated/filtered endpoints first
    (measured 2026-08-15: members.json turns into 202-challenges/403s). The
    profile that the streamed login left behind is still logged in, so a
    silent revisit is all it takes — no streaming, no user input.

    Returns the fresh cookie header, or None when this user has no usable
    profile: sessions stored via ``catknows-session store`` never had a
    browser here, and a profile whose Skool login expired can't heal itself.
    """
    profile = manager.profile_dir(subject)
    if not profile.is_dir() or not any(profile.iterdir()):
        return None  # no browser profile on this box — nothing to refresh from

    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    try:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=True,
            viewport=VIEWPORT,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(SKOOL_HOME_URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(2500)  # let the WAF JS run
            session = _cookies_to_session(await ctx.cookies())
            return session.cookie_header if session.is_valid else None
        finally:
            await ctx.close()
    finally:
        with contextlib.suppress(Exception):
            await pw.stop()


def _self_check() -> None:
    """python -m catknows.remote_login — the parts that don't need a browser."""
    import hashlib

    m = LoginManager()

    # Profiles are per-subject, hashed, and cannot escape the base directory.
    os.environ["CATKNOWS_PROFILE_DIR"] = "/tmp/ck-test"
    a, b = m.profile_dir("alice"), m.profile_dir("bob")
    assert a != b, "each subject needs its own profile"
    assert "alice" not in str(a), "the subject must not appear in the path"
    assert a.name == hashlib.sha256(b"alice").hexdigest()[:32]
    assert m.profile_dir("../../etc").parent == Path("/tmp/ck-test"), "no traversal"

    async def checks() -> None:
        # An empty subject is refused before anything is launched.
        try:
            await m.start("")
            raise AssertionError("empty subject must be refused")
        except LoginError as e:
            assert "No verified user" in str(e)

        # Input and completion checks on an unknown subject don't explode.
        assert m.get("nobody") is None
        assert await m.check_login("nobody") is None

        # A refresh without a browser profile answers None fast — before any
        # Playwright import. Manual-cookie users must not cost a Chromium.
        assert await refresh_cookie("nobody-has-this-profile") is None
        try:
            await m.input("nobody", {"type": "mousePressed"})
            raise AssertionError("input without a session must be refused")
        except LoginError as e:
            assert "No login in progress" in str(e)

        # The admission gate: a full manager refuses rather than over-committing
        # RAM on a box with no swap. Slots are the reserved-but-still-booting
        # kind (None), which is what a burst of parallel starts actually looks
        # like — and what _reap must not quietly free.
        m._sessions = {f"u{i}": None for i in range(MAX_SESSIONS)}  # type: ignore
        try:
            await m.start("one-too-many")
            raise AssertionError("must refuse past MAX_SESSIONS")
        except LoginError as e:
            assert "Too many logins" in str(e), e
        assert "one-too-many" not in m._sessions, "a refused start must not hold a slot"
        assert len(m._sessions) == MAX_SESSIONS, "reaping must not free a booting slot"

        # An expired real session IS reaped, so the cap can't wedge shut.
        stale = LoginSession(
            subject="stale", playwright=None, context=None, page=None, cdp=None,
            started=time.monotonic() - SESSION_TTL - 1,
        )
        m._sessions = {"stale": stale}
        await m._reap()
        assert not m._sessions, "a session past its TTL must be dropped"

        # Frame queue drops the oldest rather than growing without bound.
        q: asyncio.Queue = asyncio.Queue(maxsize=2)
        for x in ("a", "b"):
            q.put_nowait(x)
        assert q.full()
        q.get_nowait(); q.put_nowait("c")
        assert q.qsize() == 2

    asyncio.run(checks())
    print(f"remote_login self-check OK (max {MAX_SESSIONS} sessions, TTL {SESSION_TTL}s)")


if __name__ == "__main__":
    _self_check()
