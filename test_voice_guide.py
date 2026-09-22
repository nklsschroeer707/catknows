"""Self-check: with a voice guide set, every prose draft is written in the user's voice.

CATKNOWS_VOICE_FILE points at a Markdown file describing how the account
owner writes. Each draft that carries prose (post, comment, DM, edit, course
page) then hands that guide to the calling model, together with the order to
run a humanizer pass first. Settings-only drafts don't. A configured but
missing file must say so in the draft, never fall back silently.

Run: python test_voice_guide.py    (no network, no pytest)
"""

import os
import tempfile
from pathlib import Path

os.environ["CATKNOWS_ALLOW_WRITE"] = "1"  # the write tools only exist with this

import catknows.mcp_server as m

KEY = "write_as_the_user"
GUIDE = "---\nbereich: arbeit\n---\n\n# How Niklas writes\n\nShort. @name first. 👍🏽\n"


def _fn(tool):
    return tool.fn if hasattr(tool, "fn") else tool


def _drafts():
    return {
        "post": _fn(m.create_post)("c", "Title", "Body"),
        "comment": _fn(m.create_comment)("c", "p1", "Body"),
        "dm": _fn(m.send_dm)("ch1", "Body"),
        "edit_comment": None,  # needs a live read; covered by _draft below
        "course_page": _fn(m.create_course_item)("c1", "Page", content="Body"),
    }


def test_every_prose_draft_carries_the_voice():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "voice.md"
        p.write_text(GUIDE, encoding="utf-8")
        os.environ["CATKNOWS_VOICE_FILE"] = str(p)
        try:
            for name, d in _drafts().items():
                if d is None:
                    continue
                assert KEY in d, f"{name}: no voice guide in the draft"
                v = d[KEY]
                assert "Short. @name first. 👍🏽" in v["voice_guide"], name
                assert "bereich:" not in v["voice_guide"], "frontmatter is not voice"
                assert "humanizer" in v["instruction"].lower(), "run the humanizer skill"
                assert "before_you_show_this" in d, "the built-in pass stays too"
                assert d["status"].startswith("DRAFT"), "still a draft"
            wrapped = m._draft("DRAFT", "would_edit", {"content": "x"})
            assert KEY in wrapped, "edits go through _draft and get it as well"
        finally:
            os.environ.pop("CATKNOWS_VOICE_FILE", None)


def test_settings_only_drafts_stay_without_it():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "voice.md"
        p.write_text(GUIDE, encoding="utf-8")
        os.environ["CATKNOWS_VOICE_FILE"] = str(p)
        try:
            d = _fn(m.update_course_item)("i1", privacy=1)
            assert KEY not in d, "a privacy flag has no voice"
        finally:
            os.environ.pop("CATKNOWS_VOICE_FILE", None)


def test_a_missing_file_is_loud():
    os.environ["CATKNOWS_VOICE_FILE"] = "/nonexistent/voice.md"
    try:
        d = _fn(m.create_post)("c", "Title", "Body")
        assert KEY in d, "configured but missing must still show up"
        assert "could not be read" in d[KEY]["instruction"], d[KEY]
        assert "stop" in d[KEY]["instruction"].lower(), "tell the model not to go on blind"
    finally:
        os.environ.pop("CATKNOWS_VOICE_FILE", None)


def test_without_the_setting_nothing_changes():
    os.environ.pop("CATKNOWS_VOICE_FILE", None)
    d = _fn(m.create_post)("c", "Title", "Body")
    assert KEY not in d, "other installs must not see a voice they never set"
    assert "before_you_show_this" in d


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all green")
