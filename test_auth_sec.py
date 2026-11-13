"""Self-check: Skool's security question (AUTH-SEC-5xx) is recognised, never mistaken for data.

Skool can answer ANY api2 call with HTTP 200 and a body like
``{"code": "AUTH-SEC-501"}`` instead of the data: "I want a security nonce for
this path". It is no HTTP error, so before this check catknows read it as an
ordinary (empty) answer, and a write counted as sent. Now it raises
``SkoolSecurityChallenge`` and stops. It does not answer the question and does
not retry.

Only a 200 whose top-level ``code`` is TEXT that matches ``AUTH-SEC-5\\d\\d``
in full counts. Anything else is an ordinary answer.

Run: python test_auth_sec.py    (no network, no pytest)
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

os.environ["CATKNOWS_WRITE_GAP_S"] = "0"      # not a test of the write gap
os.environ["CATKNOWS_ALLOW_WRITE"] = "1"      # send_dm only registers in WRITE mode

import catknows
from catknows import audit, mcp_server
from catknows import http as http_mod
from catknows.client import SkoolClient
from catknows.http import SkoolHTTP, SkoolHTTPError, SkoolSecurityChallenge

http_mod.CACHE_TTL_S = 600.0                   # the cache checks below need it on

# Invented values. None of them may show up in a message or a log line.
NONCE = "n0nce-invented-for-this-test"
EMAIL = "anna@example.com"
PERSON = "Test Person"
QUERY_TOKEN = "tok-invented-query-value"

CHALLENGE = json.dumps({"code": "AUTH-SEC-501", "nonce": NONCE, "email": EMAIL,
                        "message": f"{PERSON} must verify this request"})
GOOD = {"id": "p1", "items": [1, 2]}
SECRETS = (NONCE, EMAIL, PERSON, QUERY_TOKEN, "x=1")


class _Resp:
    def __init__(self, code, text):
        self.status_code, self.text = code, text


class _Net:
    """Stands in for curl_cffi. Answers from a queue; the last answer repeats."""

    def __init__(self, *responses):
        self.queue = list(responses)
        self.calls: list[tuple[str, str, dict]] = []

    def _go(self, method, url, **kw):
        self.calls.append((method, url, kw))
        code, text = self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]
        return _Resp(code, text)

    def get(self, url, **kw):
        return self._go("get", url, **kw)

    def post(self, url, **kw):
        return self._go("post", url, **kw)

    def put(self, url, **kw):
        return self._go("put", url, **kw)

    def delete(self, url, **kw):
        return self._go("delete", url, **kw)


def _http(*responses) -> SkoolHTTP:
    h = SkoolHTTP.__new__(SkoolHTTP)           # no login, no real session
    h.session = type("S", (), {"cookie_header": "c", "auth_token": "t", "waf_token": "w"})()
    h._cache = {}
    h._profile = {"ua": "ua", "lang": "en", "sec_ch_ua": "", "platform": ""}
    h._http = _Net(*responses)
    return h


# One call per api2 way. The query strings carry values that must stay out of messages.
WAYS = {
    "get_api2": lambda h: h.get_api2("/groups/g1/labels?x=1"),
    "get_api2_text": lambda h: h.get_api2_text(f"/wait?token={QUERY_TOKEN}"),
    "post_api2": lambda h: h.post_api2("/posts?follow=true", {"metadata": {"content": "hello"}}),
    "put_api2": lambda h: h.put_api2("/courses/c1", {"title": "t"}),
    "delete_api2": lambda h: h.delete_api2("/posts/p1"),
    "delete_api2 with body": lambda h: h.delete_api2("/posts/p1", {}),
}
WRITES = ("post_api2", "put_api2", "delete_api2", "delete_api2 with body")
JSON_WAYS = ("get_api2",) + WRITES        # the ways that return parsed JSON


def _log(tmp: str) -> None:
    """Point the write-audit log at a fresh file."""
    os.environ["CATKNOWS_AUDIT_LOG"] = str(Path(tmp) / "w.jsonl")


def _lines() -> list[dict]:
    p = audit.log_path()
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text("utf-8").splitlines() if x.strip()]


def _raises_challenge(call, h) -> SkoolSecurityChallenge:
    try:
        call(h)
    except SkoolSecurityChallenge as e:
        return e
    raise AssertionError("expected SkoolSecurityChallenge")


# -- recognised ---------------------------------------------------------------

def test_every_api2_way_raises_on_a_200_with_the_code():
    with tempfile.TemporaryDirectory() as tmp:
        _log(tmp)
        for name, call in WAYS.items():
            h = _http((200, CHALLENGE))
            e = _raises_challenge(call, h)
            assert isinstance(e, SkoolHTTPError), "callers catch SkoolHTTPError; the new one must be one"
            assert e.code == "AUTH-SEC-501" and e.status == 200, (name, e.code, e.status)
            msg = str(e)
            assert "AUTH-SEC-501" in msg, (name, msg)
            for secret in SECRETS:
                assert secret not in msg, f"{name}: the message leaked {secret!r}: {msg}"
            assert len(h._http.calls) == 1, f"{name}: no automatic second attempt: {h._http.calls}"


def test_the_message_says_stop_and_never_invites_a_retry():
    """The text is what a calling model reads: it must say stop, not "try again"."""
    for code in ("AUTH-SEC-501", "AUTH-SEC-502"):
        msg = str(SkoolSecurityChallenge(code, "GET", "/groups/g1/labels?x=1")).lower()
        assert "do not retry automatically" in msg, msg
        assert "stop and tell the user" in msg, msg
        assert "try again" not in msg and "later" not in msg, msg
        # 501 asks for a nonce, 502 is a Turnstile refusal: the class text names neither.
        assert "nonce" not in msg, msg


def test_the_whole_5xx_family_counts():
    with tempfile.TemporaryDirectory() as tmp:
        _log(tmp)
        for code in ("AUTH-SEC-500", "AUTH-SEC-502", "AUTH-SEC-599"):
            e = _raises_challenge(WAYS["get_api2"], _http((200, json.dumps({"code": code}))))
            assert e.code == code and code in str(e)


def test_a_challenge_is_never_cached():
    h = _http((200, CHALLENGE), (200, json.dumps(GOOD)))
    _raises_challenge(WAYS["get_api2"], h)
    assert h._cache == {}, "a challenge answer must not be cached"
    assert WAYS["get_api2"](h) == GOOD, "the next call must reach Skool again and get the data"
    assert len(h._http.calls) == 2
    assert len(h._cache) == 1 and next(iter(h._cache.values()))[1] == GOOD


def test_a_write_answered_with_a_challenge_does_not_count_as_sent():
    with tempfile.TemporaryDirectory() as tmp:
        _log(tmp)
        for name in WRITES:
            h = _http((200, CHALLENGE))
            earlier = (time.time(), {"read": "before the write"})
            h._cache["https://api2.skool.com/earlier"] = earlier
            _raises_challenge(WAYS[name], h)
            assert h._cache == {"https://api2.skool.com/earlier": earlier}, \
                f"{name}: nothing was written, so what was read stays valid"

        lines = _lines()
        assert len(lines) == len(WRITES), lines
        for line in lines:
            assert line["status"] != 200, f"an 'ok' entry for a write Skool did not take: {line}"
            assert line["status"] == "security_challenge", line
            assert line["note"] == "AUTH-SEC-501", line
            assert "response_hash" not in line and "created_id" not in line, line
        blob = Path(audit.log_path()).read_text("utf-8")
        for secret in SECRETS + ("hello",):
            assert secret not in blob, f"the log leaked {secret!r}"


# -- NOT a challenge ----------------------------------------------------------

ORDINARY = [
    {"code": 7},
    {"code": "OK"},
    {"code": "AUTH-SEC-401"},               # right family name, wrong class
    {"code": "xAUTH-SEC-501"},              # must match in full, front ...
    {"code": "AUTH-SEC-501x"},              # ... and back
    {"code": " AUTH-SEC-501"},
    {"code": "auth-sec-501"},               # case counts
    {"code": "AUTH-SEC-5012"},
    {"code": "AUTH-SEC-50"},
    {"code": "AUTH-SEC-501\n"},             # Python's `$` would let this through; Skool's `$` does not
    {"code": "AUTH-SEC-5０１"},     # full-width digits: JavaScript's \d is ASCII only
    {"code": None},
    {"code": True},
    {"code": ["AUTH-SEC-501"]},
    {"code": {"inner": "AUTH-SEC-501"}},
    {"data": {"code": "AUTH-SEC-501"}},     # only the TOP level counts
    [{"code": "AUTH-SEC-501"}],             # a list on top
    "AUTH-SEC-501",                         # a bare JSON string
    {},
    GOOD,
]


def test_ordinary_answers_pass_through_unchanged():
    with tempfile.TemporaryDirectory() as tmp:
        _log(tmp)
        for body in ORDINARY:
            for name in JSON_WAYS:
                h = _http((200, json.dumps(body)))
                assert WAYS[name](h) == body, (name, body)
        # The writes among them left ordinary 200 lines, as before.
        lines = _lines()
        assert len(lines) == len(ORDINARY) * len(WRITES)
        assert all(line["status"] == 200 and "response_hash" in line for line in lines), lines[:2]


def test_not_json_is_no_challenge():
    with tempfile.TemporaryDirectory() as tmp:
        _log(tmp)
        for text in ("AUTH-SEC-501", "<html>AUTH-SEC-501</html>"):
            for name in JSON_WAYS:
                try:
                    WAYS[name](_http((200, text)))
                except SkoolSecurityChallenge:
                    raise AssertionError(f"{name}: text that is not JSON must not trigger ({text!r})")
                except SkoolHTTPError as e:
                    assert "Bad JSON" in str(e), e       # the old, ordinary complaint
                else:
                    raise AssertionError(f"{name}: not JSON must still fail as before")
        # The one plain-text endpoint just returns its text.
        assert WAYS["get_api2_text"](_http((200, "AUTH-SEC-501"))) == "AUTH-SEC-501"


def test_only_http_200_counts():
    with tempfile.TemporaryDirectory() as tmp:
        _log(tmp)
        # 201/204-style successes carry no challenge, whatever they say.
        assert WAYS["get_api2"](_http((201, CHALLENGE))) == json.loads(CHALLENGE)
        assert WAYS["post_api2"](_http((201, CHALLENGE))) == json.loads(CHALLENGE)
        assert _lines()[-1]["status"] == 201
        # Errors stay the errors they always were.
        for code in (400, 404, 500):
            for name in ("get_api2", "post_api2"):
                try:
                    WAYS[name](_http((code, CHALLENGE)))
                except SkoolSecurityChallenge:
                    raise AssertionError(f"{name}: HTTP {code} is an error, not a challenge")
                except SkoolHTTPError as e:
                    assert e.status == code, (name, code, e.status)
                else:
                    raise AssertionError(f"{name}: HTTP {code} must raise")


def test_the_plain_text_endpoint_is_unchanged():
    for text, want in (('"completed"', "completed"), ("in-progress", "in-progress"),
                       ("completed", "completed")):
        assert WAYS["get_api2_text"](_http((200, text))) == want


def test_normal_get_and_write_behave_as_before():
    with tempfile.TemporaryDirectory() as tmp:
        _log(tmp)
        h = _http((200, json.dumps(GOOD)))
        assert WAYS["get_api2"](h) == GOOD
        assert WAYS["get_api2"](h) == GOOD
        assert len(h._http.calls) == 1, "a normal GET is still cached"
        assert len(h._cache) == 1

        created = {"id": "post-42", "metadata": {"content": "x"}}
        h._http.queue = [(200, json.dumps(created))]
        assert h.post_api2("/posts", {"a": 1}) == created
        assert h._cache == {}, "a write that went through still clears the cache"

        h._http.queue = [(200, "")]
        assert h.delete_api2("/posts/p1") == {}, "an empty 200 is still success"

        lines = _lines()
        assert [ln["status"] for ln in lines] == [200, 200], lines
        assert lines[0]["created_id"] == "post-42" and "response_hash" in lines[0], lines[0]


# -- importable from outside --------------------------------------------------

def test_the_error_is_exported():
    from catknows.http import SkoolSecurityChallenge as imported
    assert imported is SkoolSecurityChallenge
    assert issubclass(imported, SkoolHTTPError)
    assert "SkoolSecurityChallenge" in catknows.__all__
    assert catknows.SkoolSecurityChallenge is imported


# -- not swallowed on the way up ----------------------------------------------
# Three places catch SkoolHTTPError broadly. A security question there would
# turn into "no archive flag", "not a root unit" or "unknown channel", and the
# stop would be lost. It must reach the caller as itself.

class _Raises:
    def __init__(self):
        self.calls: list[str] = []

    def get_api2(self, path):
        self.calls.append(f"GET {path}")
        raise SkoolSecurityChallenge("AUTH-SEC-501", "GET", path)

    def post_api2(self, path, body):
        self.calls.append(f"POST {path}")
        return {"id": "should-not-happen"}

    def put_api2(self, path, body):
        self.calls.append(f"PUT {path}")
        return {"id": "should-not-happen"}


def _client() -> tuple[SkoolClient, _Raises]:
    c = SkoolClient.__new__(SkoolClient)
    c.http = _Raises()
    return c, c.http


def test_group_id_for_does_not_swallow_a_challenge():
    for for_write in (False, True):
        c, http = _client()
        try:
            c.group_id_for("some-community", for_write=for_write)
        except SkoolSecurityChallenge:
            pass
        else:
            raise AssertionError("a challenge on the group lookup must stop the call")
        assert http.calls == ["GET /groups/some-community"], \
            f"no fallback to the posts bootstrap after a challenge: {http.calls}"


def test_update_course_item_does_not_turn_a_challenge_into_a_blind_put():
    """Skool resets omitted course fields on a PUT, so the read before it matters."""
    c, http = _client()
    try:
        c.update_course_item("course-1", {"title": "new"})
    except SkoolSecurityChallenge:
        pass
    else:
        raise AssertionError("a challenge on the read must stop the update")
    assert not any(call.startswith("PUT") for call in http.calls), http.calls


def test_send_dm_attachment_lookup_does_not_report_a_challenge_as_unknown_channel():
    _tool = mcp_server.send_dm
    send_dm = _tool if callable(_tool) else (getattr(_tool, "fn", None) or _tool.func)
    c, http = _client()
    c.upload_file = lambda gid, path: (_ for _ in ()).throw(AssertionError("must not upload"))
    mcp_server._client = c
    try:
        send_dm(channel_id="ch1", content="hi", attachments="x.gif", confirm=True)
    except SkoolSecurityChallenge:
        pass
    except ValueError as e:
        raise AssertionError(f"the challenge was reported as: {e}")
    else:
        raise AssertionError("a challenge on the channel lookup must stop the send")
    assert not any(call.startswith("POST") for call in http.calls), http.calls


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ok — AUTH-SEC-5xx on a 200 raises on all api2 ways, is never cached, "
          "counts no write as sent, and ordinary answers pass untouched")
