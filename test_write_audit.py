"""Every write leaves a line, and no line carries content (board D8).

The log is the thing that turns "Skool un-listed three comments" into a rate.
If it silently stops recording, or starts recording post text, we find out
here rather than after the next incident.

    .venv/Scripts/python -m pytest test_write_audit.py     # or run directly
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from catknows import audit
from catknows.http import SkoolHTTP, SkoolHTTPError


class _Resp:
    def __init__(self, code, text):
        self.status_code, self.text = code, text


class _FakeHTTP:
    """Stands in for curl_cffi. The write path is ours, the transport is not."""

    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def _any(self, url, **kw):
        self.calls.append(url)
        return self.resp

    post = put = delete = _any


def _http(resp) -> SkoolHTTP:
    h = SkoolHTTP.__new__(SkoolHTTP)
    h.session = type("S", (), {"cookie_header": "c", "auth_token": "t", "waf_token": "w"})()
    h._cache = {}
    h._profile = {"ua": "ua", "lang": "en", "sec_ch_ua": "", "platform": ""}
    h._http = _FakeHTTP(resp)
    return h


def _lines() -> list[dict]:
    p = audit.log_path()
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text("utf-8").splitlines() if x.strip()]


def _fresh_log(tmp: str, name: str) -> None:
    os.environ["CATKNOWS_AUDIT_LOG"] = str(Path(tmp) / f"{name}.jsonl")


SECRET = "sk-live-must-never-be-logged"
BODY = "The actual post text nobody may log."


def test_a_successful_write_is_logged_without_content():
    with tempfile.TemporaryDirectory() as tmp:
        _fresh_log(tmp, "ok")
        created = {"id": "post-42", "metadata": {"content": BODY}, "apiKeys": [SECRET]}
        h = _http(_Resp(200, json.dumps(created)))

        with audit.context("create_post", community="hoomans-9944"):
            h.post_api2("/posts?follow=true", {"metadata": {"content": BODY}})

        (line,) = _lines()
        assert line["tool"] == "create_post", line
        assert line["community"] == "hoomans-9944", line
        assert line["method"] == "POST" and line["status"] == 200, line
        assert line["path"] == "/posts?follow=true", "the flags explain the write"
        assert line["created_id"] == "post-42", "no id, no way to trace an un-listing"
        assert len(line["response_hash"]) == 16, line

        blob = json.dumps(line)
        assert BODY not in blob, "post content reached the log (E4)"
        assert SECRET not in blob, "a credential field reached the log"


def test_a_failed_write_is_logged_too():
    """A write we could not send is exactly what we are otherwise guessing about."""
    with tempfile.TemporaryDirectory() as tmp:
        _fresh_log(tmp, "fail")
        h = _http(_Resp(500, f"server exploded: {BODY}"))

        with audit.context("create_comment", community="catnose", post_id="p7"):
            try:
                h.post_api2("/posts?follow=false", {"metadata": {"content": BODY}})
            except SkoolHTTPError:
                pass

        (line,) = _lines()
        assert line["status"] == 500 and line["tool"] == "create_comment", line
        assert line["post_id"] == "p7", line
        assert "response_hash" not in line, "an error body must not be hashed in"
        assert BODY not in json.dumps(line), "the error body echoed our content"


def test_an_empty_200_delete_still_leaves_a_line():
    """Skool answers a delete with an empty 200 — the quietest write there is."""
    with tempfile.TemporaryDirectory() as tmp:
        _fresh_log(tmp, "del")
        h = _http(_Resp(200, ""))

        with audit.context("delete_comment", community="hoomans-9944", comment_id="c3"):
            h.delete_api2("/posts/c3")

        (line,) = _lines()
        assert line["method"] == "DELETE" and line["status"] == 200, line
        assert line["comment_id"] == "c3", line


def test_a_blocked_post_is_not_logged_as_a_write():
    """The hosted copy-paste block writes nothing, and must never count as a post."""
    with tempfile.TemporaryDirectory() as tmp:
        _fresh_log(tmp, "blocked")
        os.environ["CATKNOWS_WRITE_MODE"] = "draft_only"
        try:
            with audit.context("create_post", community="catnose"):
                audit.record_blocked("create_post", "catnose", "BLOCKED_draft_only")
        finally:
            os.environ.pop("CATKNOWS_WRITE_MODE", None)

        (line,) = _lines()
        assert line["status"] == "BLOCKED_draft_only", "a block must have its own status"
        assert line["status"] != 200, "a block is not a success"
        assert line["method"] == "-", line
        assert line["write_mode"] == "draft_only", "the mode is why it was blocked"


def test_the_log_never_breaks_the_write():
    """Mitschrift, keine Steuerung: an unwritable log must not fail a post."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CATKNOWS_AUDIT_LOG"] = str(Path(tmp) / "file.jsonl" / "deeper.jsonl")
        Path(tmp, "file.jsonl").write_text("not a directory", encoding="utf-8")
        h = _http(_Resp(200, '{"id": "p1"}'))

        with audit.context("create_post", community="x"):
            out = h.post_api2("/posts", {})  # must not raise

        assert out == {"id": "p1"}, "the write result changed because logging failed"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all green")
