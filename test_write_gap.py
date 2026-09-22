"""Two writes in a row are at least WRITE_GAP_S apart (decision Niklas 2026-09-22).

A fixed pause, no jitter: tempo hygiene, not disguise. Offline — the transport
is faked, and so is the clock, so this runs in milliseconds.

    .venv/bin/python test_write_gap.py
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from catknows import http as http_mod
from catknows.http import SkoolHTTP, SkoolHTTPError


class _Resp:
    def __init__(self, code, text):
        self.status_code, self.text = code, text


class _Clock:
    """Fake monotonic clock; sleep() advances it and remembers what was asked."""

    def __init__(self):
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


class _FakeHTTP:
    def __init__(self, clock, code=200):
        self.clock, self.code = clock, code
        self.sent_at: list[float] = []

    def _any(self, url, **kw):
        self.sent_at.append(self.clock.now)
        return _Resp(self.code, '{"id": "x"}')

    post = put = delete = _any


def _session(token):
    return type("S", (), {"cookie_header": "c-" + token, "auth_token": token, "waf_token": "w"})()


def _setup(code=200):
    clock = _Clock()
    http_mod._clock, http_mod._sleep = clock.monotonic, clock.sleep
    http_mod._last_write_at.clear()
    h = SkoolHTTP.__new__(SkoolHTTP)
    h.session = _session("t")
    h._cache = {}
    h._profile = {"ua": "ua", "lang": "en", "sec_ch_ua": "", "platform": ""}
    h._http = _FakeHTTP(clock, code)
    return h, clock


def _quiet_log(tmp):
    os.environ["CATKNOWS_AUDIT_LOG"] = str(Path(tmp) / "w.jsonl")


def test_two_writes_back_to_back_are_15_seconds_apart():
    with tempfile.TemporaryDirectory() as tmp:
        _quiet_log(tmp)
        h, clock = _setup()
        h.post_api2("/posts", {})
        h.post_api2("/posts", {})
        a, b = h._http.sent_at
        assert b - a >= 15, f"second write only {b - a}s after the first"
        assert clock.slept == [15], "one fixed pause, no jitter"


def test_the_first_write_does_not_wait():
    with tempfile.TemporaryDirectory() as tmp:
        _quiet_log(tmp)
        h, clock = _setup()
        h.delete_api2("/posts/c1")
        assert clock.slept == [], "nothing to space from, nothing to wait for"


def test_time_already_passed_counts():
    with tempfile.TemporaryDirectory() as tmp:
        _quiet_log(tmp)
        h, clock = _setup()
        h.put_api2("/x", {})
        clock.now += 10  # the user took 10 s to approve the next draft
        h.put_api2("/x", {})
        assert clock.slept == [5], clock.slept


def test_a_failed_write_still_counts_as_sent():
    """Skool saw the request either way; the next one waits too."""
    with tempfile.TemporaryDirectory() as tmp:
        _quiet_log(tmp)
        h, clock = _setup(code=500)
        for _ in range(2):
            try:
                h.post_api2("/posts", {})
            except SkoolHTTPError:
                pass
        assert clock.slept == [15], clock.slept


def test_two_accounts_do_not_wait_for_each_other():
    """Hosted serves many Skool accounts in one process; each keeps its own pace."""
    with tempfile.TemporaryDirectory() as tmp:
        _quiet_log(tmp)
        h1, clock = _setup()
        h1.post_api2("/posts", {})
        h2 = SkoolHTTP.__new__(SkoolHTTP)
        h2.__dict__.update({**h1.__dict__, "_http": _FakeHTTP(clock), "session": _session("other")})
        h2.post_api2("/posts", {})
        assert clock.slept == [], "another account's write must not delay this one"


def test_the_gap_is_per_account_not_per_client():
    """A second client in the same process (re-login, a script) waits too."""
    with tempfile.TemporaryDirectory() as tmp:
        _quiet_log(tmp)
        h1, clock = _setup()
        h1.post_api2("/posts", {})
        h2 = SkoolHTTP.__new__(SkoolHTTP)
        h2.__dict__.update({**h1.__dict__, "_http": _FakeHTTP(clock)})
        h2.post_api2("/posts", {})
        assert clock.slept == [15], clock.slept


def test_reads_are_not_spaced():
    with tempfile.TemporaryDirectory() as tmp:
        _quiet_log(tmp)
        h, clock = _setup()
        h.post_api2("/posts", {})
        h._http.get = lambda url, **kw: _Resp(200, '{"ok": 1}')
        h.get_api2("/self")
        h.get_api2("/self/x")
        assert clock.slept == [], "a read must never wait on the write gap"


def test_env_overrides_the_gap():
    old = os.environ.get("CATKNOWS_WRITE_GAP_S")
    os.environ["CATKNOWS_WRITE_GAP_S"] = "30"
    try:
        assert http_mod._write_gap_s() == 30
        os.environ["CATKNOWS_WRITE_GAP_S"] = "kaputt"
        assert http_mod._write_gap_s() == 15, "a broken value falls back, never to 0"
    finally:
        if old is None:
            os.environ.pop("CATKNOWS_WRITE_GAP_S", None)
        else:
            os.environ["CATKNOWS_WRITE_GAP_S"] = old


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all green")
