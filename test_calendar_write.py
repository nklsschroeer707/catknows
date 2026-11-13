"""Self-check: calendar events are created, changed and deleted only on purpose.

Nothing here touches Skool. The client talks to a stand-in that records every
call, and the HTTP layer (for the audit test) to a stand-in transport.

The three calls (basis https://api2.skool.com). Source: Skool's own page code
(build 1794436674711), read from the plan notes `vertraege.md` (rows
createCalendarEventV2, updateCalendarEventV2, deleteCalendarEventV2) and
`quellen/skool-re-funktionskarte-kalender-2026-11-12.md` (§1 endpoints, §2 fields
and limits, §3 location types, §5 recurrence, §6 series scope). Never run live.

    createCalendarEventV2   POST   /calendar-events
        query: none
        body:  the whole event object (Karte §2)
               {group_id, start_time, end_time,
                metadata: {title, description, timezone, reminder_disabled,
                           cover_image, location, privacy},
                recurrence: {interval, frequency, days, month_day, position,
                             until, count}}
               `location` and `privacy` are JSON TEXT, not objects.
    updateCalendarEventV2   PUT    /calendar-events/{id}
        query: from_occurrence_id (Karte §6: "this and following events";
               left out = all events)
        body:  the whole event object, same fields as create
    deleteCalendarEventV2   DELETE /calendar-events/{id}
        query: from_occurrence_id (same meaning)
        body:  none (vertraege.md: "–")

Limits (Karte §2): title at most 30 characters, description at most 300.
Location types (Karte §3): 1 Zoom, 2 Meet, 3 Address, 4 Link, 5 Skool call,
6 Skool webinar; 5 and 6 have no input field, the server hands out the call
code, so they carry no `location_info`.

NOT built: updateCalendarEventOccurrenceV2 (one occurrence of a series).

Deliberately NOT asserted, because the notes do not say: which fields Skool
requires, whether an update with fewer fields keeps or resets the rest, the
wire format of start_time/end_time/timezone/until (the values below are opaque
markers that must come out unchanged).

Run: python test_calendar_write.py    (no network, no pytest)
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

os.environ["CATKNOWS_ALLOW_WRITE"] = "1"  # the write tools only exist with this
os.environ["CATKNOWS_WRITE_GAP_S"] = "0"  # not a test of the write gap (test_write_gap.py is)

from catknows import audit
from catknows.client import SkoolClient
from catknows.http import SkoolHTTP, SkoolSecurityChallenge

SLUG = "some-group"
GID = "gid-1"
START = "START-VALUE-AS-GIVEN"
END = "END-VALUE-AS-GIVEN"
TZ = "TZ-VALUE-AS-GIVEN"
LINK = '{"location_type":4,"location_info":"https://example.com/meet"}'
ACCESS = '{"privacy_type":0}'
SERIES = {"frequency": "weekly", "interval": 1, "days": [2], "count": 10}

EVENT = {
    "start_time": START,
    "end_time": END,
    "metadata": {
        "title": "Coffee hour",
        "description": "A short chat.",
        "timezone": TZ,
        "reminder_disabled": 0,
        "location": LINK,
        "privacy": ACCESS,
    },
    "recurrence": SERIES,
}


class _FakeHTTP:
    """Records every call of every kind, reads included: 'no call' means none."""

    def __init__(self, group=None, answer=None):
        self.calls = []
        self.group = {"id": GID} if group is None else group
        self.answer = {"id": "ev-1"} if answer is None else answer

    @property
    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]

    def get_api2(self, path):
        self.calls.append(("GET", path, None))
        return self.group

    def post_api2(self, path, body):
        self.calls.append(("POST", path, body))
        return self.answer

    def put_api2(self, path, body):
        self.calls.append(("PUT", path, body))
        return self.answer

    def delete_api2(self, path, body=None):
        self.calls.append(("DELETE", path, body))
        return {}


class _ChallengedHTTP(_FakeHTTP):
    """Skool answers every write with its security question (AUTH-SEC)."""

    def _ask(self, verb, path):
        self.calls.append((verb, path, None))
        raise SkoolSecurityChallenge("AUTH-SEC-501", verb, path)

    def post_api2(self, path, body):
        self._ask("POST", path)

    def put_api2(self, path, body):
        self._ask("PUT", path)

    def delete_api2(self, path, body=None):
        self._ask("DELETE", path)


def _client(http=None):
    c = SkoolClient.__new__(SkoolClient)
    c.http = http or _FakeHTTP()
    return c


def _refused(call):
    try:
        call()
    except ValueError as e:
        return str(e)
    raise AssertionError("expected a ValueError")


def _fn(tool):
    return tool.fn if hasattr(tool, "fn") else tool


def _with_client(c, call):
    import catknows.mcp_server as m
    old = m._get_client
    m._get_client = lambda: c
    try:
        return call(m)
    finally:
        m._get_client = old


# -- client: the three calls ----------------------------------------------------

def test_create_posts_the_whole_object_with_the_group_id():
    c = _client()
    before = json.dumps(EVENT, sort_keys=True)
    out = c.create_calendar_event(SLUG, EVENT)
    assert out == {"id": "ev-1"}, "the client hands back Skool's answer as it is"
    assert c.http.calls == [
        ("GET", f"/groups/{SLUG}", None),  # group_id_for: slug -> uuid
        ("POST", "/calendar-events", {"group_id": GID, **EVENT}),
    ], c.http.calls
    assert json.dumps(EVENT, sort_keys=True) == before, "the caller's object was changed"


def test_create_keeps_times_and_texts_exactly_as_given():
    c = _client()
    c.create_calendar_event(SLUG, EVENT)
    (_, _, body), = c.http.writes
    assert body["start_time"] == START and body["end_time"] == END, body
    assert body["metadata"]["timezone"] == TZ, body
    assert body["metadata"]["location"] == LINK, "location stays JSON text, untouched"
    assert body["metadata"]["privacy"] == ACCESS, "privacy stays JSON text, untouched"
    assert body["recurrence"] == SERIES, body


def test_create_refuses_an_archived_community():
    c = _client(_FakeHTTP(group={"id": GID, "archived": True}))
    _refused(lambda: c.create_calendar_event(SLUG, EVENT))
    assert c.http.writes == [], c.http.writes


def test_create_never_takes_a_group_id_from_the_caller():
    """The slug is what the archived guard checked; a second id would sidestep it."""
    c = _client()
    _refused(lambda: c.create_calendar_event(SLUG, {**EVENT, "group_id": "another"}))
    assert c.http.calls == [], c.http.calls


def test_update_puts_the_object_untouched():
    c = _client()
    out = c.update_calendar_event("ev1", EVENT)
    assert out == {"id": "ev-1"}
    assert c.http.calls == [("PUT", "/calendar-events/ev1", EVENT)], c.http.calls
    assert c.http.calls[0][2] is EVENT, "the object goes out unchanged, not rebuilt"


def test_update_from_occurrence_goes_in_the_path_text_encoded():
    c = _client()
    c.update_calendar_event("ev1", EVENT, from_occurrence_id="occ-9")
    c.update_calendar_event("ev1", EVENT, from_occurrence_id="a b/c&d=e")
    assert [x[1] for x in c.http.calls] == [
        "/calendar-events/ev1?from_occurrence_id=occ-9",
        "/calendar-events/ev1?from_occurrence_id=a%20b%2Fc%26d%3De",
    ], c.http.calls


def test_delete_sends_no_body():
    c = _client()
    out = c.delete_calendar_event("ev1")
    assert out == {}
    assert c.http.calls == [("DELETE", "/calendar-events/ev1", None)], c.http.calls


def test_delete_from_occurrence_goes_in_the_path_text_encoded():
    c = _client()
    c.delete_calendar_event("ev1", from_occurrence_id="occ-9")
    c.delete_calendar_event("ev1", from_occurrence_id="a b/c&d=e")
    assert c.http.calls == [
        ("DELETE", "/calendar-events/ev1?from_occurrence_id=occ-9", None),
        ("DELETE", "/calendar-events/ev1?from_occurrence_id=a%20b%2Fc%26d%3De", None),
    ], c.http.calls


def test_an_id_cannot_leave_its_path_segment():
    c = _client()
    c.delete_calendar_event("../x?y=1")
    assert c.http.calls == [("DELETE", "/calendar-events/..%2Fx%3Fy%3D1", None)], c.http.calls


def test_an_empty_or_odd_id_is_refused_before_any_call():
    for bad in ("", "  ", None, 7):
        c = _client()
        _refused(lambda: c.update_calendar_event(bad, EVENT))
        _refused(lambda: c.delete_calendar_event(bad))
        assert c.http.calls == [], (bad, c.http.calls)
    c = _client()
    _refused(lambda: c.delete_calendar_event("ev1", from_occurrence_id=5))
    assert c.http.calls == [], c.http.calls


def _bad_id_calls(bad):
    """Every way an event id (or occurrence id) can reach a request: client and tools."""
    ev = lambda m: _fn(m.update_calendar_event)  # noqa: E731
    de = lambda m: _fn(m.delete_calendar_event)  # noqa: E731
    return [
        lambda c: c.update_calendar_event(bad, EVENT),
        lambda c: c.delete_calendar_event(bad),
        lambda c: _with_client(c, lambda m: ev(m)(SLUG, bad, title="New")),
        lambda c: _with_client(c, lambda m: ev(m)(SLUG, bad, title="New", confirm=True)),
        lambda c: _with_client(c, lambda m: de(m)(SLUG, bad)),
        lambda c: _with_client(c, lambda m: de(m)(SLUG, bad, confirm=True)),
    ]


def test_ids_made_only_of_dots_are_refused_before_any_call():
    """Dots stay dots when percent-encoded, and libcurl folds `/calendar-events/..`
    away: a DELETE would then hit the collection or the root, not one event."""
    for bad in (".", "..", "...", "....."):
        for call in _bad_id_calls(bad):
            c = _client()
            _refused(lambda: call(c))
            assert c.http.calls == [], (bad, c.http.calls)
    c = _client()
    c.delete_calendar_event("a.b")  # a dot between other characters is an ordinary id
    c.delete_calendar_event(".a")
    c.update_calendar_event("ev1", EVENT)
    assert [x[1] for x in c.http.calls] == [
        "/calendar-events/a.b", "/calendar-events/.a", "/calendar-events/ev1"], c.http.calls


def test_blank_and_non_text_ids_are_refused_by_client_and_tools():
    for bad in ("", "   ", "\t", None, 7, ["ev1"]):
        for call in _bad_id_calls(bad):
            c = _client()
            _refused(lambda: call(c))
            assert c.http.calls == [], (bad, c.http.calls)


def test_the_id_refusal_names_the_argument_not_the_value():
    msg = _refused(lambda: _client().delete_calendar_event(".."))
    assert "event_id" in msg and ".." not in msg, msg
    msg = _refused(lambda: _client().update_calendar_event("....", EVENT))
    assert "event_id" in msg and "...." not in msg, msg
    msg = _refused(lambda: _client().delete_calendar_event(["secret-event-id"]))
    assert "event_id" in msg and "secret-event-id" not in msg, msg
    msg = _refused(lambda: _client().update_calendar_event("ev1", EVENT, from_occurrence_id="  "))
    assert "from_occurrence_id" in msg, msg


def test_a_given_occurrence_id_must_be_real_text():
    """Empty means "not given" (the whole event). Given, it is non-empty text."""
    for bad in ("  ", "\n", 5, ["occ-1"], None):
        c = _client()
        _refused(lambda: c.update_calendar_event("ev1", EVENT, from_occurrence_id=bad))
        _refused(lambda: c.delete_calendar_event("ev1", from_occurrence_id=bad))
        _refused(lambda: _with_client(c, lambda m: _fn(m.delete_calendar_event)(
            SLUG, "ev1", from_occurrence_id=bad, confirm=True)))
        assert c.http.calls == [], (bad, c.http.calls)
    c = _client()
    c.delete_calendar_event("ev1", from_occurrence_id="..")  # a query value, not a path piece
    c.delete_calendar_event("ev1", from_occurrence_id="")
    assert [x[1] for x in c.http.calls] == [
        "/calendar-events/ev1?from_occurrence_id=..", "/calendar-events/ev1"], c.http.calls


# -- client: the limits bite before anything is sent ---------------------------

def _with_meta(**fields):
    return {"start_time": START, "end_time": END, "metadata": fields}


def test_title_limit_is_30_characters():
    ok = _with_meta(title="x" * 30)
    c = _client()
    c.create_calendar_event(SLUG, ok)
    c.update_calendar_event("ev1", ok)
    assert len(c.http.writes) == 2

    too_long = _with_meta(title="x" * 31)
    c = _client()
    assert "30" in _refused(lambda: c.create_calendar_event(SLUG, too_long))
    _refused(lambda: c.update_calendar_event("ev1", too_long))
    assert c.http.calls == [], f"a refused event must cause no call at all: {c.http.calls}"


def test_description_limit_is_300_characters():
    c = _client()
    c.create_calendar_event(SLUG, _with_meta(description="y" * 300))
    assert len(c.http.writes) == 1

    too_long = _with_meta(description="y" * 301)
    c = _client()
    assert "300" in _refused(lambda: c.create_calendar_event(SLUG, too_long))
    _refused(lambda: c.update_calendar_event("ev1", too_long))
    assert c.http.calls == [], c.http.calls


def test_the_refusal_names_the_limit_not_the_text():
    secret = "Private launch party " * 3
    msg = _refused(lambda: _client().create_calendar_event(SLUG, _with_meta(title=secret)))
    assert secret not in msg, "an error message must not echo the title"


def test_title_and_description_must_be_text():
    c = _client()
    _refused(lambda: c.create_calendar_event(SLUG, _with_meta(title=12)))
    _refused(lambda: c.update_calendar_event("ev1", _with_meta(description=["a"])))
    assert c.http.calls == [], c.http.calls


def test_the_object_itself_must_be_an_object():
    c = _client()
    _refused(lambda: c.create_calendar_event(SLUG, "not a dict"))
    _refused(lambda: c.update_calendar_event("ev1", ["not", "a", "dict"]))
    _refused(lambda: c.create_calendar_event(SLUG, {"metadata": "not a dict"}))
    assert c.http.calls == [], c.http.calls


def test_update_checks_only_the_fields_it_was_given():
    """No required set is invented: an update with one field is checked as that."""
    c = _client()
    c.update_calendar_event("ev1", {"start_time": START})
    c.update_calendar_event("ev1", {"metadata": {"title": "New"}})
    assert len(c.http.writes) == 2
    _refused(lambda: c.update_calendar_event("ev1", {"metadata": {"title": "z" * 31}}))
    assert len(c.http.writes) == 2, "the refused one must not have been sent"


def test_location_must_be_json_text_with_a_type():
    bad = [
        {"location_type": 4, "location_info": "https://example.com"},  # an object, not text
        "https://example.com/meet",                                    # text, but not JSON
        "[1, 2]",                                                      # JSON, not an object
        '{"location_info": "https://example.com"}',                    # no location_type
        None,
        7,
    ]
    for loc in bad:
        c = _client()
        _refused(lambda: c.create_calendar_event(SLUG, _with_meta(location=loc)))
        _refused(lambda: c.update_calendar_event("ev1", _with_meta(location=loc)))
        assert c.http.calls == [], (loc, c.http.calls)


def test_skool_call_and_webinar_take_no_location_info():
    for kind in (5, 6):
        for extra in ({"location_info": "abc123"}, {"location_info": ""}):
            loc = json.dumps({"location_type": kind, **extra})
            c = _client()
            msg = _refused(lambda: c.create_calendar_event(SLUG, _with_meta(location=loc)))
            assert "location_info" in msg, msg
            _refused(lambda: c.update_calendar_event("ev1", _with_meta(location=loc)))
            assert c.http.calls == [], (kind, extra, c.http.calls)
        fine = json.dumps({"location_type": kind})
        c = _client()
        c.create_calendar_event(SLUG, _with_meta(location=fine))
        c.update_calendar_event("ev1", _with_meta(location=fine))
        assert [x[2]["metadata"]["location"] for x in c.http.writes] == [fine, fine]


def test_the_other_location_types_carry_their_info():
    for kind in (1, 2, 3, 4):
        loc = json.dumps({"location_type": kind, "location_info": "somewhere"})
        c = _client()
        c.create_calendar_event(SLUG, _with_meta(location=loc))
        assert c.http.writes[0][2]["metadata"]["location"] == loc


# -- a security question from Skool is a stop ----------------------------------

def test_a_security_question_goes_through_and_is_never_retried():
    for call in (
        lambda c: c.create_calendar_event(SLUG, EVENT),
        lambda c: c.update_calendar_event("ev1", EVENT),
        lambda c: c.delete_calendar_event("ev1"),
    ):
        c = _client(_ChallengedHTTP())
        try:
            call(c)
        except SkoolSecurityChallenge as e:
            assert e.code == "AUTH-SEC-501", e
        else:
            raise AssertionError("the security question was swallowed")
        assert len(c.http.writes) == 1, f"a second try was sent: {c.http.writes}"


# -- tools: nothing leaves without confirm -------------------------------------

def _tool_args(**over):
    args = dict(community_slug=SLUG, title="Coffee hour", start_time=START, end_time=END,
                description="A short chat.", timezone=TZ, reminder_disabled=0,
                location_type=4, location_info="https://example.com/meet",
                privacy=ACCESS, recurrence=json.dumps(SERIES))
    args.update(over)
    return args


def test_the_tools_exist_and_are_flagged():
    import catknows.mcp_server as m
    for name in ("create_calendar_event", "update_calendar_event", "delete_calendar_event"):
        assert hasattr(m, name), f"{name} is not registered with CATKNOWS_ALLOW_WRITE=1"
        assert name in m._DESTRUCTIVE, f"{name} must be flagged destructive"
        assert name not in m._READ_ONLY, f"{name} writes"
    assert m._AUDIT_ARGS["event_id"] == "calendar_event_id", m._AUDIT_ARGS


def test_drafts_send_nothing_and_say_what_would_happen():
    c = _client()
    created = _with_client(c, lambda m: _fn(m.create_calendar_event)(**_tool_args()))
    changed = _with_client(c, lambda m: _fn(m.update_calendar_event)(
        SLUG, "ev1", title="New title", from_occurrence_id="occ-9"))
    deleted = _with_client(c, lambda m: _fn(m.delete_calendar_event)(
        SLUG, "ev1", from_occurrence_id="occ-9"))
    assert c.http.calls == [], f"a draft must not touch Skool at all: {c.http.calls}"

    for out in (created, changed, deleted):
        assert out["status"].startswith("DRAFT"), out
        assert "confirm=true" in out["next_step"], out

    assert created["would_create"]["community"] == SLUG, created
    assert created["would_create"]["event"]["metadata"]["title"] == "Coffee hour", created
    assert changed["would_update"]["event_id"] == "ev1", changed
    assert changed["would_update"]["from_occurrence_id"] == "occ-9", changed
    assert changed["would_update"]["event"] == {"metadata": {"title": "New title"}}, changed
    assert deleted["would_delete"]["event_id"] == "ev1", deleted
    assert deleted["would_delete"]["from_occurrence_id"] == "occ-9", deleted
    assert "following" in deleted["would_delete"]["applies_to"], deleted


def test_a_delete_draft_says_when_it_takes_the_whole_event():
    c = _client()
    out = _with_client(c, lambda m: _fn(m.delete_calendar_event)(SLUG, "ev1"))
    assert "every occurrence" in out["would_delete"]["applies_to"], out
    assert out["would_delete"]["from_occurrence_id"] == "", out
    assert c.http.calls == []


def test_prose_drafts_ask_for_the_humanizing_pass():
    c = _client()
    created = _with_client(c, lambda m: _fn(m.create_calendar_event)(**_tool_args()))
    titled = _with_client(c, lambda m: _fn(m.update_calendar_event)(SLUG, "ev1", title="New"))
    moved = _with_client(c, lambda m: _fn(m.update_calendar_event)(
        SLUG, "ev1", start_time=START))
    deleted = _with_client(c, lambda m: _fn(m.delete_calendar_event)(SLUG, "ev1"))
    assert "before_you_show_this" in created, "title and description are prose"
    assert "before_you_show_this" in titled
    assert "before_you_show_this" not in moved, "a time change has no prose"
    assert "before_you_show_this" not in deleted


def test_every_draft_carries_a_fixed_not_verified_text():
    """A warning that lives only in a docstring never reaches the human who confirms."""
    c = _client()
    created = _with_client(c, lambda m: _fn(m.create_calendar_event)(**_tool_args()))
    changed = _with_client(c, lambda m: _fn(m.update_calendar_event)(
        SLUG, "ev1", title="New title"))
    deleted = _with_client(c, lambda m: _fn(m.delete_calendar_event)(SLUG, "ev1"))
    assert c.http.calls == [], f"a draft must not touch Skool at all: {c.http.calls}"

    nv_create, nv_update, nv_delete = (created["not_verified"], changed["not_verified"],
                                       deleted["not_verified"])
    # create: what only a live run can settle
    assert "requires" in nv_create and "start_time" in nv_create \
        and "timezone" in nv_create, nv_create
    # update: whether Skool keeps a field the update leaves out
    assert "keeps or resets" in nv_update and "leaves out" in nv_update, nv_update
    # delete: the draft cannot show the title, so the human must know the event
    assert "cannot show" in nv_delete and "title" in nv_delete \
        and "which event" in nv_delete, nv_delete
    # the same text every time: it is a constant, not built from the call
    again = _with_client(c, lambda m: _fn(m.update_calendar_event)(
        SLUG, "ev2", start_time=START, from_occurrence_id="occ-1"))
    assert again["not_verified"] == nv_update, again


def test_a_draft_already_refuses_what_the_client_would_refuse():
    c = _client()
    for over in ({"title": "x" * 31}, {"description": "y" * 301},
                 {"location_type": 5, "location_info": "abc"}):
        _refused(lambda: _with_client(c, lambda m: _fn(m.create_calendar_event)(
            **_tool_args(**over))))
    _refused(lambda: _with_client(c, lambda m: _fn(m.update_calendar_event)(
        SLUG, "ev1", title="x" * 31)))
    _refused(lambda: _with_client(c, lambda m: _fn(m.delete_calendar_event)(SLUG, "")))
    assert c.http.calls == [], c.http.calls


def test_location_info_alone_and_bad_json_are_refused():
    c = _client()
    _refused(lambda: _with_client(c, lambda m: _fn(m.create_calendar_event)(
        **_tool_args(location_type=0, location_info="https://example.com"))))
    for bad in ("not json", "[1]", '"x"'):
        _refused(lambda: _with_client(c, lambda m: _fn(m.create_calendar_event)(
            **_tool_args(recurrence=bad))))
    assert c.http.calls == [], c.http.calls


def test_update_without_any_change_is_refused():
    c = _client()
    _refused(lambda: _with_client(c, lambda m: _fn(m.update_calendar_event)(SLUG, "ev1")))
    assert c.http.calls == [], c.http.calls


# -- tools: confirm=true -------------------------------------------------------

def test_confirmed_create_sends_the_object_and_scrubs_the_answer():
    answer = {"id": "ev-1", "metadata": {"title": "Coffee hour"}, "apiKeys": ["SECRET-KEY"]}
    c = _client(_FakeHTTP(answer=answer))
    out = _with_client(c, lambda m: _fn(m.create_calendar_event)(**_tool_args(), confirm=True))
    assert out["status"] == "created", out
    assert out["event"]["id"] == "ev-1", out
    assert "SECRET-KEY" not in json.dumps(out), "the answer must go through _safe_raw"
    assert c.http.calls == [
        ("GET", f"/groups/{SLUG}", None),
        ("POST", "/calendar-events", {
            "group_id": GID, "start_time": START, "end_time": END,
            "metadata": {"title": "Coffee hour", "description": "A short chat.",
                         "timezone": TZ, "reminder_disabled": 0,
                         "location": LINK, "privacy": ACCESS},
            "recurrence": SERIES}),
    ], c.http.calls


def test_the_tool_leaves_out_what_it_was_not_given():
    c = _client()
    _with_client(c, lambda m: _fn(m.create_calendar_event)(
        SLUG, title="Quick call", start_time=START, end_time=END,
        location_type=5, confirm=True))
    (_, _, body), = c.http.writes
    assert body == {"group_id": GID, "start_time": START, "end_time": END,
                    "metadata": {"title": "Quick call",
                                 "location": '{"location_type":5}'}}, body


def test_confirmed_update_sends_only_the_given_fields_plus_the_group():
    c = _client()
    out = _with_client(c, lambda m: _fn(m.update_calendar_event)(
        SLUG, "ev1", title="New title", reminder_disabled=1,
        from_occurrence_id="occ-9", confirm=True))
    assert out["status"] == "updated", out
    assert c.http.calls == [
        ("GET", f"/groups/{SLUG}", None),
        ("PUT", "/calendar-events/ev1?from_occurrence_id=occ-9",
         {"group_id": GID, "metadata": {"title": "New title", "reminder_disabled": 1}}),
    ], c.http.calls


def test_confirmed_delete_sends_the_delete_and_no_body():
    c = _client()
    out = _with_client(c, lambda m: _fn(m.delete_calendar_event)(SLUG, "ev1", confirm=True))
    assert out["status"] == "deleted" and out["event_id"] == "ev1", out
    assert c.http.calls == [
        ("GET", f"/groups/{SLUG}", None),
        ("DELETE", "/calendar-events/ev1", None),
    ], c.http.calls


def test_writes_into_an_archived_community_stop_at_the_tool():
    c = _client(_FakeHTTP(group={"id": GID, "archived": True}))
    _refused(lambda: _with_client(c, lambda m: _fn(m.update_calendar_event)(
        SLUG, "ev1", title="New", confirm=True)))
    _refused(lambda: _with_client(c, lambda m: _fn(m.delete_calendar_event)(
        SLUG, "ev1", confirm=True)))
    assert c.http.writes == [], c.http.writes


def test_a_security_question_is_not_turned_into_a_result_by_the_tools():
    for call in (
        lambda m: _fn(m.create_calendar_event)(**_tool_args(), confirm=True),
        lambda m: _fn(m.update_calendar_event)(SLUG, "ev1", title="New", confirm=True),
        lambda m: _fn(m.delete_calendar_event)(SLUG, "ev1", confirm=True),
    ):
        c = _client(_ChallengedHTTP())
        try:
            _with_client(c, call)
        except SkoolSecurityChallenge:
            pass
        else:
            raise AssertionError("the tool swallowed Skool's security question")
        assert len(c.http.writes) == 1, f"a second try was sent: {c.http.writes}"


# -- audit: the line names the event, never its content ------------------------

class _Resp:
    def __init__(self, code, text):
        self.status_code, self.text = code, text


class _Transport:
    """Stands in for curl_cffi: the write path is ours, the wire is not."""

    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def _any(self, url, **kw):
        self.calls.append(url)
        return self.resp

    post = put = delete = _any


def _real_http(resp) -> SkoolHTTP:
    h = SkoolHTTP.__new__(SkoolHTTP)
    h.session = type("S", (), {"cookie_header": "c", "auth_token": "t", "waf_token": "w"})()
    h._cache = {}
    h._profile = {"ua": "ua", "lang": "en", "sec_ch_ua": "", "platform": ""}
    h._http = _Transport(resp)
    return h


def test_every_calendar_write_leaves_a_line_without_content():
    secret_title = "Private launch party"
    secret_desc = "Doors open for the inner circle only."
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CATKNOWS_AUDIT_LOG"] = str(Path(tmp) / "w.jsonl")
        c = SkoolClient.__new__(SkoolClient)
        c.http = _real_http(_Resp(200, json.dumps({"id": "ev-77"})))
        c.group_id_for = lambda slug, for_write=False: GID  # no read of Skool here

        _with_client(c, lambda m: _fn(m.create_calendar_event)(
            SLUG, title=secret_title, description=secret_desc,
            start_time=START, end_time=END, confirm=True))
        _with_client(c, lambda m: _fn(m.update_calendar_event)(
            SLUG, "ev-77", title=secret_title, from_occurrence_id="occ-9", confirm=True))
        _with_client(c, lambda m: _fn(m.delete_calendar_event)(SLUG, "ev-77", confirm=True))

        lines = [json.loads(x) for x in audit.log_path().read_text("utf-8").splitlines()]
        made, changed, gone = lines
        assert (made["tool"], made["method"], made["path"]) == (
            "create_calendar_event", "POST", "/calendar-events"), made
        assert made["community"] == SLUG and made["created_id"] == "ev-77", made
        assert (changed["tool"], changed["method"]) == ("update_calendar_event", "PUT"), changed
        assert changed["calendar_event_id"] == "ev-77", changed
        assert changed["path"] == "/calendar-events/ev-77?from_occurrence_id=occ-9", changed
        assert (gone["tool"], gone["method"]) == ("delete_calendar_event", "DELETE"), gone
        assert gone["calendar_event_id"] == "ev-77" and gone["status"] == 200, gone
        blob = "\n".join(json.dumps(x) for x in lines)
        assert secret_title not in blob and secret_desc not in blob, \
            "event text reached the audit log (no content in the log, ever)"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all green")
