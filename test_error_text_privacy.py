"""Self-check: an error text of the HTTP layer carries no address and no credential.

On the hosted server an error text ends up in the journal: the MCP SDK logs every
tool exception that is not a ToolError, with its whole chain. Two things could
ride along on the read paths and on the upload:

1. **Skool's answer.** Every error shows the start of the answer (that is what
   makes it readable). An answer can name a person, so the excerpt comes from one
   place, `http._answer_excerpt`, which masks e-mail addresses
   (`normalize.mask_email`) and runs `audit.redact` over it.
2. **The URL.** A network error repeats the library's own message, and that can
   repeat the URL: the one-shot token of the dashboard numbers (`?token=`) or a
   presigned upload link, whose query IS the access. Such an error names the URL
   without its query and has no exception chain. The write path already did this
   (`test_course_settings.py`).

Run: python test_error_text_privacy.py    (no network, no pytest)
"""

import json
import os
import tempfile
import time
import traceback

os.environ["CATKNOWS_WRITE_GAP_S"] = "0"  # not a test of the write gap
# The write path logs. Keep this file out of the real ~/.catknows/writes.jsonl.
os.environ["CATKNOWS_AUDIT_LOG"] = os.path.join(tempfile.mkdtemp(), "writes.jsonl")

from curl_cffi.requests.exceptions import RequestException

from catknows import audit, http, normalize
from catknows.client import SkoolClient
from catknows.http import SkoolHTTP, SkoolHTTPError

BASE = "https://api2.skool.com"
ADDRESS = "anna@example.com"
MASKED = "a***@example.com"
TOKEN = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"   # invented: a one-shot wait token
UPLOAD_HOST = "uploads.example-bucket.test"
SIGNATURE = "5a4b3c2d1e0f"                    # invented: the signature of an upload link
CREDENTIAL = "AKIDEXAMPLE-20260101"           # invented
UPLOAD_URL = (f"https://{UPLOAD_HOST}/group-1/files/secret-name.png"
              f"?X-Amz-Credential={CREDENTIAL}&X-Amz-Signature={SIGNATURE}")


class _Resp:
    def __init__(self, code, text):
        self.status_code, self.text = code, text


class _Transport:
    """Stands in for curl_cffi: answers every verb with one response, or raises."""

    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.calls = resp, exc, []

    def _send(self, verb):
        def send(url, **kw):
            self.calls.append((verb, url))
            if self.exc is not None:
                raise self.exc
            return self.resp
        return send

    def __getattr__(self, name):
        if name in ("get", "post", "put", "delete"):
            return self._send(name.upper())
        raise AttributeError(name)


def _http(resp=None, exc=None) -> SkoolHTTP:
    h = SkoolHTTP.__new__(SkoolHTTP)
    h.session = type("S", (), {"cookie_header": "c", "auth_token": "t", "waf_token": "w"})()
    h._cache = {}
    h._build_id = "build-1"
    h._profile = {"ua": "ua", "lang": "en", "sec_ch_ua": "", "platform": ""}
    h._http = _Transport(resp, exc)
    return h


def _raised(call, resp=None, exc=None) -> SkoolHTTPError:
    try:
        call(_http(resp, exc))
    except SkoolHTTPError as e:
        return e
    raise AssertionError("the call was expected to fail")


def _everything(err: BaseException) -> str:
    """What a logger prints for an exception: its text AND the whole chain."""
    return str(err) + "\n" + "".join(traceback.format_exception(err))


# One call per door of the HTTP layer. The first four are the read paths.
READS = {
    "get_api2": lambda h: h.get_api2("/groups/g1/rules"),
    "get_next": lambda h: h.get_next("/some-group/-/members.json?p=2"),
    "get_api2_text": lambda h: h.get_api2_text("/wait?token=" + TOKEN),
    "post_api2_read": lambda h: h.post_api2_read("/search/users", {"q": "an"}),
}
WRITES = {
    "post_api2": lambda h: h.post_api2("/posts?follow=true", {"x": 1}),
    "put_api2": lambda h: h.put_api2("/posts/p1", {"x": 1}),
    "delete_api2": lambda h: h.delete_api2("/posts/p1", {}),
    "put_bytes": lambda h: h.put_bytes(UPLOAD_URL, b"bytes", {}),
}


# -- B: the answer excerpt ------------------------------------------------------

def test_an_address_in_an_error_body_is_masked_on_every_read_and_write_path():
    body = json.dumps({"error": f"user {ADDRESS} not found", "detail": "try again"})
    for label, call in {**READS, **WRITES}.items():
        if label == "put_bytes":
            continue    # a foreign storage answers there, see the upload tests below
        err = _raised(call, _Resp(500, body))
        assert ADDRESS not in _everything(err), f"{label}: the address is in the error"
        assert MASKED in str(err), f"{label}: the masked address is missing: {err}"
        assert "not found" in str(err) and "HTTP 500" in str(err), \
            f"{label}: the excerpt is what makes the error readable: {err}"
        assert err.status == 500, (label, err.status)


def test_an_address_in_a_body_that_is_not_json_is_masked_too():
    body = f"<html>{ADDRESS} is not allowed</html>"
    for label, call in (("get_api2", READS["get_api2"]), ("get_next", READS["get_next"]),
                        ("post_api2", WRITES["post_api2"])):
        err = _raised(call, _Resp(200, body))
        assert "Bad JSON" in str(err), (label, str(err))
        assert ADDRESS not in _everything(err), f"{label}: the address is in the error"
        assert MASKED in str(err), (label, str(err))


def test_the_excerpt_is_masked_before_it_is_cut():
    """An address that straddles the cut would otherwise lose its domain and stay as it is."""
    body = "x" * 290 + " " + ADDRESS + " and more"
    for label, call in (("get_api2", READS["get_api2"]), ("post_api2", WRITES["post_api2"])):
        err = _raised(call, _Resp(500, body))
        assert "anna" not in str(err), f"{label}: a cut address kept its local part: {err}"


def test_the_excerpt_keeps_its_length():
    long = "word " * 200
    assert http._answer_excerpt("/posts", long, 300) == long[:300]
    assert http._answer_excerpt("/posts", long, 200) == long[:200]
    assert http._answer_excerpt("/posts", "short", 300) == "short"
    assert len(http._answer_excerpt("/posts", (ADDRESS + " ") * 100, 300)) == 300


def test_the_excerpt_goes_through_the_redaction_of_secret_query_values():
    body = f"no such thing: /courses/c1/import?group=g1&clone_code={TOKEN}"
    assert TOKEN not in http._answer_excerpt("/posts", body, 300)
    err = _raised(READS["get_api2"], _Resp(500, body))
    assert TOKEN not in str(err) and "clone_code=[redacted]" in str(err), str(err)


def test_the_share_key_answer_is_still_not_shown_at_all():
    assert "not shown" in http._answer_excerpt("/courses/c1/clone-code", ADDRESS, 300)


def test_a_link_in_the_error_body_of_a_read_post_is_still_removed():
    err = _raised(READS["post_api2_read"],
                  _Resp(500, f"see https://files.example.test/f?sig=abc or mail {ADDRESS}"))
    assert "[link removed]" in str(err) and "files.example.test" not in str(err), str(err)
    assert MASKED in str(err) and ADDRESS not in str(err), str(err)


# -- C: the URL in a network error ----------------------------------------------

def _no_chain(err: BaseException, label: str):
    assert err.__cause__ is None and err.__context__ is None, \
        f"{label}: the library's error hangs on the chain"
    assert err.__suppress_context__ is True, label


def test_redact_knows_the_one_shot_token():
    assert audit.redact(f"/wait?token={TOKEN}") == "/wait?token=[redacted]"
    assert audit.redact(f"/groups/g1/analytics-v2?chart=mrr&token={TOKEN}") == (
        "/groups/g1/analytics-v2?chart=mrr&token=[redacted]")
    assert audit.carries_secret(f"/wait?token={TOKEN}")
    # a name that only ends in "token" is another parameter
    assert audit.redact("/x?playbackToken=abc") == "/x?playbackToken=abc"
    assert not audit.carries_secret("/groups/g1/rules")


def test_a_network_error_on_the_wait_path_names_no_token_and_has_no_chain():
    url = f"{BASE}/wait?token={TOKEN}"
    for message in ("Could not resolve host: api2.skool.com", f"Failed to perform: {url}"):
        err = _raised(READS["get_api2_text"], exc=RequestException(message))
        assert TOKEN not in _everything(err), f"the token is in the error ({message[:20]})"
        assert f"Network error on {BASE}/wait:" in str(err), str(err)
        assert "?" not in str(err).split(": ")[0], f"the URL is named with a query: {err}"
        assert err.status == 0, err.status
        _no_chain(err, "get_api2_text")
    err = _raised(READS["get_api2_text"], exc=RequestException("Could not resolve host"))
    assert "Could not resolve host" in str(err), "the reason is kept in the text"


def test_a_network_error_on_a_get_with_a_token_names_no_token_and_has_no_chain():
    path = f"/groups/g1/analytics-v2?chart=mrr&token={TOKEN}"
    err = _raised(lambda h: h.get_api2(path),
                  exc=RequestException(f"Failed to perform: {BASE}{path}"))
    assert TOKEN not in _everything(err), "the token is in the error or its chain"
    assert f"Network error on {BASE}/groups/g1/analytics-v2:" in str(err), str(err)
    _no_chain(err, "_get_with_retry")


def test_a_network_error_on_a_read_post_is_redacted_and_then_has_no_chain():
    """The two read-POSTs carry no credential in their own URL. A library message
    that names one anyway is redacted, and then it must not travel on as the cause."""
    path = "/files/f1/download-url?expire=300"
    message = f"Failed to perform: {BASE}{path} via {BASE}/wait?token={TOKEN}"
    err = _raised(lambda h: h.post_api2_read(path), exc=RequestException(message))
    assert TOKEN not in _everything(err), "the token is in the error or its chain"
    assert f"Network error on {BASE}{path}:" in str(err), str(err)
    assert "token=[redacted]" in str(err), str(err)
    _no_chain(err, "post_api2_read")


def test_a_network_error_on_the_upload_names_the_host_only_and_has_no_chain():
    for message in ("Connection timed out",
                    f"Failed to perform: {UPLOAD_URL}",
                    f"Failed to perform: {UPLOAD_URL.replace('https://', '')}"):
        err = _raised(WRITES["put_bytes"], exc=RequestException(message))
        blob = _everything(err)
        for piece, what in ((SIGNATURE, "signature"), (CREDENTIAL, "credential"),
                            ("secret-name.png", "path"), ("X-Amz", "query")):
            assert piece not in blob, f"the {what} of the upload link is in the error: {err}"
        assert UPLOAD_HOST in str(err), f"the host says where it failed: {err}"
        assert err.status == 0, err.status
        _no_chain(err, "put_bytes")
    err = _raised(WRITES["put_bytes"], exc=RequestException("Connection timed out"))
    assert "Connection timed out" in str(err), "the reason is kept in the text"


def test_an_http_error_on_a_path_with_a_token_names_no_token():
    """Not only the network error repeats the URL: every error text of a read does."""
    wait = READS["get_api2_text"]
    data = lambda h: h.get_api2(f"/groups/g1/analytics-v2?chart=mrr&token={TOKEN}")  # noqa: E731
    cases = {
        "HTTP 500": _Resp(500, "server exploded"),
        "401 not permitted": _Resp(401, '{"error":"action not permitted"}'),
        "403 waf": _Resp(403, "blocked"),
        "404 not found": _Resp(404, '{"notFound":true}'),
    }
    for label, resp in cases.items():
        for name, call in (("get_api2_text", wait), ("get_api2", data)):
            err = _raised(call, resp)
            assert TOKEN not in _everything(err), f"{name}, {label}: the token is in the error"
            assert err.status == resp.status_code, (name, label, err.status)
    err = _raised(data, _Resp(200, "this is <not> json"))
    assert TOKEN not in _everything(err), "bad JSON: the token is in the error or its chain"
    old = http.time.sleep
    http.time.sleep = lambda s: None  # the 202 retry waits; not here
    try:
        err = _raised(data, _Resp(202, ""))
    finally:
        http.time.sleep = old
    assert TOKEN not in _everything(err) and "after" in str(err), str(err)


def test_an_ordinary_read_keeps_its_url_its_reason_and_its_cause():
    """Only a URL with a credential is cut: the rest of the error texts stay as they are."""
    for label, call, url in (
        ("get_api2", READS["get_api2"], f"{BASE}/groups/g1/rules"),
        ("get_next", READS["get_next"],
         "https://www.skool.com/_next/data/build-1/some-group/-/members.json?p=2"),
        ("post_api2_read", READS["post_api2_read"], f"{BASE}/search/users"),
    ):
        boom = RequestException("Could not resolve host")
        err = _raised(call, exc=boom)
        assert str(err) == f"Network error on {url}: Could not resolve host", (label, str(err))
        assert err.__cause__ is boom, f"{label}: the cause of an ordinary error is gone"
        err = _raised(call, _Resp(500, "server exploded"))
        assert str(err) == f"HTTP 500 on {url}: server exploded", (label, str(err))


# -- after the review: the size of the body, the foreign storage, the other doors --

def test_a_huge_error_body_is_not_masked_as_a_whole():
    """Only the start is shown, so only the start is worked on (the error path of a
    multi-megabyte answer took seconds)."""
    for body in ('\\"' * 2_000_000, "a.b-c " * 700_000, '{"k": "v"}, ' * 350_000):
        start = time.perf_counter()
        http._answer_excerpt("/posts", body, 300)
        took = time.perf_counter() - start
        assert took < 0.2, f"{took:.2f} s for the excerpt of {len(body)} characters"


def test_the_excerpt_of_a_long_body_is_the_same_as_masking_all_of_it():
    long_local = "anna." + "x" * 59            # 64 characters, masked down to 4
    bodies = (
        "x" * 290 + " " + ADDRESS + " and more " * 200,
        "x" * 690 + " " + ADDRESS + " and more " * 200,     # an address on the window's edge
        (f"{long_local}@example.com ") * 200,               # masking shrinks it a lot
        ("word " * 139) + f"{long_local}@mail.example.com " + "tail " * 500,
        f"see /x?clone_code={TOKEN} " * 100,
    )
    for body in bodies:
        for limit in (200, 300):
            whole = audit.redact(normalize.mask_email(body))[:limit]
            got = http._answer_excerpt("/posts", body, limit)
            assert got == whole, (limit, body[:40], got[-60:], whole[-60:])
            assert "anna" not in got, got


def test_an_http_error_of_the_upload_shows_the_storage_code_and_message_only():
    """The storage's error answer repeats the request: the access key id, the text
    that was signed, the signature. Its <Code> and <Message> say why, the rest stays out."""
    body = ('<?xml version="1.0" encoding="UTF-8"?>\n<Error><Code>SignatureDoesNotMatch</Code>'
            "<Message>The request signature we calculated does not match the signature "
            f"you provided, {ADDRESS}.</Message><AWSAccessKeyId>{CREDENTIAL}</AWSAccessKeyId>"
            f"<StringToSign>AWS4-HMAC-SHA256 group-1/files/secret-name.png</StringToSign>"
            f"<SignatureProvided>{SIGNATURE}</SignatureProvided></Error>")
    err = _raised(WRITES["put_bytes"], _Resp(403, body))
    blob = _everything(err)
    for piece, what in ((CREDENTIAL, "access key id"), (SIGNATURE, "signature"),
                        ("secret-name.png", "signed text"), ("AWSAccessKeyId", "other fields"),
                        (ADDRESS, "address")):
        assert piece not in blob, f"the {what} of the storage's answer is in the error: {err}"
    assert str(err).startswith("HTTP 403 uploading bytes: SignatureDoesNotMatch: "
                               "The request signature we calculated"), str(err)
    assert MASKED in str(err) and err.status == 403, (str(err), err.status)


def test_an_http_error_of_the_upload_that_is_not_the_storage_form_shows_no_body():
    for body in (json.dumps({"error": f"user {ADDRESS}", "key": CREDENTIAL}),
                 f"<html>denied for {CREDENTIAL}</html>", ""):
        err = _raised(WRITES["put_bytes"], _Resp(500, body))
        assert CREDENTIAL not in _everything(err) and "user" not in str(err), str(err)
        assert str(err).startswith("HTTP 500 uploading bytes") and err.status == 500, str(err)
        assert "not shown" in str(err), str(err)


MUX_URL = f"https://stream.video-host.test/playback-1.m3u8?token={TOKEN}"


def test_a_network_error_on_the_video_host_names_no_token_and_has_no_chain():
    for message in ("Connection timed out", f"Failed to perform: {MUX_URL}",
                    f"Failed to perform: {MUX_URL.replace('https://', '')}"):
        err = _raised(lambda h: h.get_mux(MUX_URL), exc=RequestException(message))
        assert TOKEN not in _everything(err), f"the playback token is in the error: {err}"
        assert "Network error on https://stream.video-host.test/playback-1.m3u8:" in str(err), \
            str(err)
        assert err.status == 0, err.status
        _no_chain(err, "get_mux")
    err = _raised(lambda h: h.get_mux(MUX_URL), exc=RequestException("Connection timed out"))
    assert "Connection timed out" in str(err), "the reason is kept in the text"


def test_the_request_to_the_video_host_is_sent_as_before():
    h = _http(_Resp(200, "#EXTM3U"))
    assert h.get_mux(MUX_URL) == "#EXTM3U"
    assert h._http.calls == [("GET", MUX_URL)], h._http.calls
    err = _raised(lambda h: h.get_mux(MUX_URL), _Resp(403, "E184-1"))
    assert err.status == 403 and TOKEN not in str(err), str(err)


def test_a_network_error_while_looking_for_the_build_id_is_a_skool_error():
    def call(h):
        h._build_id = ""
        return h.build_id("some-group")
    boom = RequestException("Could not resolve host")
    err = _raised(call, exc=boom)
    assert str(err) == ("Network error on https://www.skool.com/some-group: "
                        "Could not resolve host"), str(err)
    assert err.status == 0 and err.__cause__ is boom, "an ordinary read keeps its cause"
    # a library message that names a credential is redacted, and then has no chain
    err = _raised(call, exc=RequestException(f"redirected to {BASE}/wait?token={TOKEN}"))
    assert TOKEN not in _everything(err), "the token is in the error or its chain"
    _no_chain(err, "build_id")


def test_an_unknown_wait_answer_is_shown_short_and_masked():
    """`/wait` says `in-progress` or `completed`. Anything else ends the wait, and
    the error shows the start of it, from the same place as every other excerpt."""
    class _H:
        def __init__(self, state):
            self.state = state

        def get_api2(self, path):
            return {"token": TOKEN}

        def get_api2_text(self, path):
            return self.state

    def ended(state) -> str:
        c = SkoolClient.__new__(SkoolClient)
        c.http = _H(state)
        try:
            c._analytics("/groups/g1/analytics-v2?chart=mrr")
        except RuntimeError as e:
            assert TOKEN not in _everything(e), "the token is in the error"
            return str(e)
        raise AssertionError("an unknown wait answer must end the wait")

    assert ended("failed") == ("Skool's wait for /groups/g1/analytics-v2?chart=mrr "
                               "ended in 'failed'"), ended("failed")
    text = ended(f"<html>no session for {ADDRESS}</html>" + "x" * 5000)
    assert ADDRESS not in text and MASKED in text, text
    assert len(text) < 200, f"the whole answer is in the error ({len(text)} characters)"
    text = ended("x" * 70 + " " + ADDRESS)
    assert "anna" not in text, f"a cut address kept its local part: {text}"


def test_a_rejected_read_post_names_the_url_the_same_way_as_the_other_reads():
    err = _raised(READS["post_api2_read"], _Resp(403, "blocked"))
    assert f"HTTP 403 on {BASE}/search/users " in str(err) and err.status == 403, str(err)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all green")
