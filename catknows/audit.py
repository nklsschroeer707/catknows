"""Write-audit log — one line per write catknows sends to Skool (board D8).

Why this exists: Skool has un-listed individual API-written comments three
times. ``GET /posts/{id}`` still answers 200 afterwards, the comment is just
gone from the tree (a real delete would 404). We could not say how often that
happens, because nobody wrote down what we wrote. Every statement about the
write path was anecdote. This turns "three cases out of maybe a hundred
writes" into a real rate, and it is what the control experiment gets evaluated
against.

It writes, it does not steer: no queue, no retry, nothing hangs off the log.
A failure to log must never fail a write, so `record` swallows its own errors.
The old rule D5 "no throttle" is replaced (owner's decision 2026-09-22): there
is now a fixed minimum gap between writes, but it lives in
`http._write_api2`, not here, so the log stays a record and never a control.

What must NEVER be in here (E4): post text, DM text, member data, and a
credential in a query string (the course share key and the delete's client id,
`redact`). Only
technical metadata about our own writes. The response is stored as a hash, not
as text, because Skool payloads carry credential-class fields — logging them
raw would make the log the leak. `normalize.scrub` runs before hashing anyway,
so a future field name added there is covered here too.

    CATKNOWS_AUDIT_LOG=/var/lib/catknows/writes.jsonl   # off when unset locally
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

from . import normalize

# Who asked for the write. The HTTP layer knows the URL and the status but not
# which tool ran or which community it was aimed at, and threading two extra
# arguments through 13 client methods would mean every future write method has
# to remember to pass them. A contextvar set once at the tool boundary covers
# callers that don't know they're being audited (the CLI, doc examples), which
# is the whole point of logging at the shared path.
_CONTEXT: ContextVar[dict] = ContextVar("catknows_audit_context", default={})

# Fallback path so a local install that never sets the env var still logs
# somewhere findable, rather than silently not logging at all.
_DEFAULT_PATH = Path.home() / ".catknows" / "writes.jsonl"


# Query parameters whose VALUE is a credential: the course share key
# (`importCourse` carries it as `clone_code`, docs/API.md §7.8), the verified
# client id that lets a whole course be deleted (`deleteCourse`, §7.6: an id that
# passed the e-mail code check once and is reused for good) and the one-shot token
# of the dashboard numbers (`/wait?token=` and the read that follows it, §1.9: no
# write, so it never reaches the log, but the read paths name their URL in error
# texts and take the rule from here). The log line keeps
# the path with its query because the flags in it explain the write
# (`notify=members`, `state=published`), so the value is replaced and the name
# stays: the line still says that a key was sent, never which. A new secret
# query name goes in this tuple, not into a call site.
_SECRET_QUERY_KEYS = ("clone_code", "client_id", "token")
_SECRET_QUERY = re.compile(
    r"([?&](?:" + "|".join(map(re.escape, _SECRET_QUERY_KEYS)) + r"))=[^&#\s]*")

# Paths whose ANSWER is a credential: the course share key comes back in the body
# of `POST /courses/{id}/clone-code` (docs/API.md §7.8). No error text may repeat
# a piece of such an answer (see `SkoolHTTP._write_api2`), and the audit line
# keeps only its hash (`record`). A new path goes here, not into a call site.
_SECRET_ANSWER_PATH = re.compile(r"/courses/[^/?#]+/clone-code")


def redact(text: str) -> str:
    """``text`` with the value of every secret query parameter replaced by ``[redacted]``.

    For anything that may repeat a request path or URL and is allowed to leave
    this process: an audit line, an error message. A text without such a
    parameter comes back unchanged, byte for byte.
    """
    return _SECRET_QUERY.sub(r"\1=[redacted]", text)


def redact_value(text: str, value: str) -> str:
    """``text`` with every occurrence of ``value`` replaced by ``[redacted]``, in any case.

    `redact` finds a credential behind its parameter name. This is for the caller
    that knows the value it sent (the course share key of an import) and must not
    see it come back bare in an answer that an error text repeats. An empty
    ``value`` hides nothing.
    """
    if not value:
        return text
    return re.sub(re.escape(value), "[redacted]", text, flags=re.IGNORECASE)


def secret_answer(path_and_query: str) -> bool:
    """True if Skool's ANSWER to a request on this path is a credential."""
    return _SECRET_ANSWER_PATH.fullmatch(path_and_query.split("?")[0].split("#")[0]) is not None


def carries_secret(path_and_query: str) -> bool:
    """True if the request or the answer on this path is a credential.

    Then nothing about the call may travel on in an exception chain either: a
    logger prints the whole chain, and the library's own message can repeat the URL.
    """
    return secret_answer(path_and_query) or redact(path_and_query) != path_and_query


def log_path() -> Path:
    p = os.environ.get("CATKNOWS_AUDIT_LOG", "").strip()
    return Path(p) if p else _DEFAULT_PATH


def mode() -> str:
    """``hosted`` when the server speaks HTTP for remote users, else ``local``."""
    return "hosted" if os.environ.get("CATKNOWS_HTTP", "") == "1" else "local"


def write_mode() -> str:
    """The active CATKNOWS_WRITE_MODE, ``normal`` when unset."""
    return os.environ.get("CATKNOWS_WRITE_MODE", "").strip() or "normal"


@contextmanager
def context(tool: str, **fields):
    """Name the tool (and its target ids) for every write inside this block.

    Nests: an inner block inherits the outer fields, so the file upload inside
    ``create_post`` is still logged as create_post, with the upload's own path
    telling the two apart.
    """
    merged = {**_CONTEXT.get(), "tool": tool,
              **{k: v for k, v in fields.items() if v}}
    token = _CONTEXT.set(merged)
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def current_tool() -> str:
    """The tool the current call is running under, "" outside a context."""
    return _CONTEXT.get().get("tool", "")


def response_hash(payload) -> str:
    """A stable short hash of a response, never the response itself.

    Scrubbed first: hashing does not leak, but scrub is the repo's single
    guard for credential-class fields and running it here keeps the invariant
    "nothing reaches a log un-scrubbed" true at this path too. Sorted keys, so
    the same response hashes the same across runs.
    """
    try:
        clean = normalize.scrub(json.loads(json.dumps(payload, default=str)))
        blob = json.dumps(clean, sort_keys=True, default=str)
    except (TypeError, ValueError):
        blob = repr(payload)
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()[:16]


def record(*, method: str, path: str, status, response=None, **extra) -> None:
    """Append one line. Never raises — a broken log must not break a write."""
    try:
        ctx = _CONTEXT.get()
        line = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "tool": ctx.get("tool") or f"{method.upper()} {path.split('?')[0]}",
            "community": ctx.get("community", ""),
            "method": method.upper(),
            # Query strings carry only flags (notify=members, state=published),
            # no content — keep them, they explain what the write did. The one
            # exception is a credential in the query (`redact`: the course share
            # key `clone_code`, the delete's `client_id`).
            "path": redact(path),
            "status": status,
            "mode": mode(),
            "write_mode": write_mode(),
        }
        for key in ("post_id", "comment_id", "course_item_id", "dm_channel_id", "member_id",
                    "calendar_event_id", "category_id"):
            if ctx.get(key):
                line[key] = ctx[key]
        line.update({k: v for k, v in extra.items() if v not in ("", None)})
        if response is not None:
            line["response_hash"] = response_hash(response)
            # The id Skool assigned is metadata, not content, and without it a
            # later un-listing can't be traced back to the write that made it.
            if isinstance(response, dict) and response.get("id"):
                line["created_id"] = response["id"]

        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001 - see docstring
        print(f"catknows: write-audit line dropped: {e}", file=sys.stderr)


def record_blocked(tool: str, community: str, reason: str, **extra) -> None:
    """A write the user asked for that we did NOT send (hosted copy-paste block).

    Not a write and not an error, so it carries its own status: nobody should
    ever count these as posts. It answers a question the write lines can't —
    how often people want to post while posting is paused.

    ``context`` merges, so the ids the tool wrapper already put in place (the
    post a blocked comment was aimed at) stay on the line.
    """
    with context(tool or "(unknown)", community=community, **extra):
        record(method="-", path="(not sent)", status=reason)


if __name__ == "__main__":
    # ponytail: self-check for the parts that can silently rot — the context
    # nesting, the no-content promise, and record() surviving a bad path.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CATKNOWS_AUDIT_LOG"] = str(Path(tmp) / "w.jsonl")
        os.environ["CATKNOWS_WRITE_MODE"] = "draft_only"

        secret = "sk-live-do-not-log-me"
        with context("create_comment", community="hoomans-9944", post_id="p1"):
            record(method="post", path="/posts?follow=false", status=200,
                   response={"id": "c9", "metadata": {"content": secret},
                             "apiKeys": [secret]})
        record_blocked("create_post", "catnose", "BLOCKED_draft_only")

        lines = [json.loads(x) for x in log_path().read_text("utf-8").splitlines()]
        assert len(lines) == 2, lines
        a, b = lines

        assert a["tool"] == "create_comment" and a["post_id"] == "p1", a
        assert a["community"] == "hoomans-9944" and a["status"] == 200, a
        assert a["created_id"] == "c9", "the created id must be traceable"
        assert len(a["response_hash"]) == 16, a
        assert a["write_mode"] == "draft_only" and a["mode"] == "local", a
        assert secret not in json.dumps(a), "content or secret reached the log"

        assert b["tool"] == "create_post" and b["status"] == "BLOCKED_draft_only", b
        assert "response_hash" not in b, "a block is not a response"

        assert _CONTEXT.get() == {}, "context leaked out of its block"

        # Same payload, same hash; a changed payload, a different one.
        h = response_hash({"a": 1, "b": 2})
        assert h == response_hash({"b": 2, "a": 1}), "hash must not depend on key order"
        assert h != response_hash({"a": 1, "b": 3}), "different payload, same hash"

        # A credential in the query never reaches the line; the other flags stay.
        key = "0123456789abcdef0123456789abcdef" * 2
        record(method="post", path=f"/courses/c1/import?group=g1&clone_code={key}", status=200)
        last = json.loads(log_path().read_text("utf-8").splitlines()[-1])
        assert key not in json.dumps(last), "a share key reached the log"
        assert last["path"] == "/courses/c1/import?group=g1&clone_code=[redacted]", last
        assert redact("/posts?follow=true") == "/posts?follow=true", "a plain path must not change"
        assert redact("/courses/c1?client_id=abc") == "/courses/c1?client_id=[redacted]"
        assert redact("/wait?token=abc") == "/wait?token=[redacted]"
        assert redact_value(f"Invalid code {key.upper()}", key) == "Invalid code [redacted]"
        assert secret_answer("/courses/c1/clone-code") and not secret_answer("/courses/c1/import")
        assert carries_secret("/courses/c1?client_id=abc") and not carries_secret("/posts/p1/pin")

        # A log that cannot be written must not raise into the write path.
        os.environ["CATKNOWS_AUDIT_LOG"] = str(Path(tmp) / "w.jsonl" / "nope.jsonl")
        record(method="post", path="/posts", status=200)  # must not raise

    print("audit self-check OK")
