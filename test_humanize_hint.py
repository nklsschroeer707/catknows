"""Self-check: every draft that carries prose asks for a humanizing pass.

The server has no model of its own, so it cannot rewrite anything. What it
CAN do is tell the client's model to do the pass before the user ever sees
the draft. That only makes sense where a human reads the text: a post, a
comment, a DM, a course page. A privacy flag or a delete confirmation has no
prose in it, and nagging there would just be noise.

Run: python test_humanize_hint.py    (no network, no pytest)
"""

import os

os.environ["CATKNOWS_ALLOW_WRITE"] = "1"  # the write tools only exist with this

import catknows.mcp_server as m

HINT = "before_you_show_this"


def _fn(tool):
    return tool.fn if hasattr(tool, "fn") else tool


def main() -> None:
    prose = {
        "create_post": lambda: _fn(m.create_post)("c", "Title", "Body"),
        "create_comment": lambda: _fn(m.create_comment)("c", "p1", "Body"),
        "send_dm": lambda: _fn(m.send_dm)("ch1", "Body"),
        "create_course": lambda: _fn(m.create_course)("c", "Title", "Desc"),
        "create_course_item": lambda: _fn(m.create_course_item)("c1", "Page", content="Body"),
        "update_course_item(title)": lambda: _fn(m.update_course_item)("i1", title="New"),
        "update_course_item(content)": lambda: _fn(m.update_course_item)("i1", content="New"),
    }
    for name, call in prose.items():
        out = call()
        assert HINT in out, f"{name}: prose draft must ask for the pass"
        assert "em dash" in out[HINT], f"{name}: the house dash rule must be in it"
        assert "Keep every fact" in out[HINT], \
            f"{name}: rewriting must never be licence to invent"

    # Settings-only edits carry no prose, so no nagging.
    out = _fn(m.update_course_item)("i1", privacy=1)
    assert HINT not in out, "a privacy flag has no prose to humanize"

    # The draft still has to be a draft: the hint must not replace the
    # nothing-was-written contract the whole write path rests on.
    d = _fn(m.create_post)("c", "T", "B")
    assert d["status"].startswith("DRAFT"), "the hint must not eat the DRAFT status"
    assert "confirm=true" in d["next_step"], "the confirm step must survive"
    assert d["would_post"]["content"] == "B", "the draft must show the ORIGINAL text"

    print("ok: prose drafts ask for a humanizing pass, settings drafts don't")


if __name__ == "__main__":
    main()
