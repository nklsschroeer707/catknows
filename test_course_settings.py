"""Self-check: course settings are sent only on purpose and exactly as Skool's own client sends them; the share key never reaches a log; rules and level names are read raw.

Nothing here touches Skool. The client talks to a stand-in that records every
call, and the HTTP layer (for the wire and audit tests) to a stand-in transport.

The six calls (basis https://api2.skool.com). Source: Skool's own page code
(build 1794436674711), read from the plan notes `vertraege.md` (rows
updateCourseDrip, updateCourseParent, createCourseShareKey, importCourse,
getGroupRules, getGroupLevels), the catalogue (`skool-re-api-katalog-rest`,
`-groups`), the function map (`skool-re-funktionskarte-classroom` §C1.2,
`-community-settings` §1.8) and the page code itself (`_app`: the API client;
the classroom and course chunks: the dialogs). Never run live.

    updateCourseDrip      POST /courses/{id}/update-drip                   {enabled, days}
    updateCourseParent    POST /courses/{id}/update-parent?parent={pid}    no body
    createCourseShareKey  POST /courses/{id}/clone-code                    no body, answer {code}
    importCourse          POST /courses/{src}/import?group={gid}&clone_code={key}   no body
    getGroupRules         GET  /groups/{gid}/rules                         no query
    getGroupLevels        GET  /groups/{gid}/levels                        no query

What the page code shows, and the tests hold to:
  * Drip: body `{enabled, days}`, days a whole number, the field clamped to 1..999;
    with drip on and no days the Save button stays off; with drip off the form
    sends the days left in its field or 0. The call is in a PAGE's menu.
  * Parent: query `parent` = a folder's id, or the course's own id for "None"
    (`parent: folder ? folder.id : page.rootId`). No body.
  * Share key: no body; the page reads `data.code`. The import form refuses a key
    that is not exactly 64 characters, lowercases it, and sends
    `courseId = key.slice(0, 32)`, `groupId = currentGroup.id` (the group ID, not
    the slug), `code = key`; the query is `{group, clone_code}` in that order.
  * Rules: the page reads `data.rules`; levels: not read by any captured page
    chunk, answer shape not measured.

NOT built: duplicateCourse, updateCourseLockFreeTrial, unlockCourse, the rule and
level writers (addGroupRule, editGroupRule, deleteGroupRule, updateGroupLevel).

Deliberately NOT asserted, because the notes do not say: what Skool answers (the
stand-in answers are made up), the shape of the rules and levels answers, whether
the server enforces 1..999, whether an import arrives published or as a draft.

The share key is a credential: its value must be in no audit line, no error text
and no draft. The tests search for the whole key AND for its secret half (the
first 32 characters are the course id, which is no secret).

Run: python test_course_settings.py    (no network, no pytest)
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

os.environ["CATKNOWS_ALLOW_WRITE"] = "1"  # the write tools only exist with this
os.environ["CATKNOWS_WRITE_GAP_S"] = "0"  # not a test of the write gap (test_write_gap.py is)

from curl_cffi.requests.exceptions import RequestException

from catknows import audit
from catknows.client import SkoolClient
from catknows.http import SkoolHTTP, SkoolHTTPError, SkoolSecurityChallenge

SLUG = "some-group"
GID = "gid-1"

COURSE = "0123456789abcdef0123456789abcdef"      # 32 characters: the course id, no secret
SECRET = "feedfacecafebeef0011223344556677"      # 32 characters: the secret half
CODE = COURSE + SECRET                           # the 64-character share key
BASE = "https://api2.skool.com"

IMPORT_PATH = f"/courses/{COURSE}/import?group={GID}&clone_code={CODE}"


class _FakeHTTP:
    """Records every call of every kind, reads included: 'no call' means none."""

    def __init__(self, group=None, answer=None, read=None):
        self.calls = []
        self.group = {"id": GID} if group is None else group
        self.answer = {"id": "x-1"} if answer is None else answer
        self.read = {"rules": []} if read is None else read

    @property
    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]

    def get_api2(self, path):
        self.calls.append(("GET", path, None))
        if path == f"/groups/{SLUG}":
            return self.group
        return self.read

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
    """Skool answers every call with its security question (AUTH-SEC)."""

    def _ask(self, verb, path):
        self.calls.append((verb, path, None))
        raise SkoolSecurityChallenge("AUTH-SEC-501", verb, path)

    def get_api2(self, path):
        if path == f"/groups/{SLUG}":
            return super().get_api2(path)
        self._ask("GET", path)

    def post_api2(self, path, body):
        self._ask("POST", path)


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


# -- client: drip -----------------------------------------------------------------

def test_drip_posts_enabled_and_days_in_the_body():
    c = _client()
    c.set_course_drip("p1", True, 7)
    assert c.http.calls == [("POST", "/courses/p1/update-drip", {"enabled": True, "days": 7})], \
        c.http.calls


def test_drip_off_sends_enabled_false_and_zero_days_unless_told_otherwise():
    c = _client()
    c.set_course_drip("p1", False)
    c.set_course_drip("p1", False, 14)  # the page sends the days left in its field
    assert c.http.calls == [
        ("POST", "/courses/p1/update-drip", {"enabled": False, "days": 0}),
        ("POST", "/courses/p1/update-drip", {"enabled": False, "days": 14}),
    ], c.http.calls


def test_drip_days_follow_the_limits_of_the_page_form():
    ok = _client()
    ok.set_course_drip("p1", True, 1)
    ok.set_course_drip("p1", True, 999)
    ok.set_course_drip("p1", False, 0)
    ok.set_course_drip("p1", False, 999)
    assert [c[2]["days"] for c in ok.http.calls] == [1, 999, 0, 999], ok.http.calls

    bad = _client()
    for enabled, days in ((True, 0), (True, -1), (True, 1000), (False, 1000), (False, -1),
                          (True, True), (True, "7"), (True, 7.5), (True, None)):
        _refused(lambda: bad.set_course_drip("p1", enabled, days))
    assert bad.http.calls == [], bad.http.calls


def test_drip_needs_a_real_boolean():
    c = _client()
    for flag in ("false", "true", 1, 0, None):
        _refused(lambda: c.set_course_drip("p1", flag, 7))
    assert c.http.calls == [], c.http.calls


# -- client: folder ---------------------------------------------------------------

def test_move_to_folder_posts_the_parent_in_the_query_and_no_body():
    c = _client()
    c.move_course_to_folder("p1", "f1")
    assert c.http.calls == [("POST", "/courses/p1/update-parent?parent=f1", None)], c.http.calls


def test_ids_are_encoded_into_the_path_and_the_query():
    c = _client()
    c.move_course_to_folder("a/b?c#d", "x&parent=evil y")
    c.set_course_drip("a/b?c#d", True, 3)
    c.create_course_share_key("a b")
    (_, move, _), (_, drip, _), (_, key, _) = c.http.calls
    assert move == "/courses/a%2Fb%3Fc%23d/update-parent?parent=x%26parent%3Devil%20y", move
    assert drip == "/courses/a%2Fb%3Fc%23d/update-drip", drip
    assert key == "/courses/a%20b/clone-code", key


def test_an_empty_or_odd_id_is_refused_before_any_call():
    c = _client()
    for call in (
        lambda: c.move_course_to_folder("", "f1"),
        lambda: c.move_course_to_folder("p1", " "),
        lambda: c.move_course_to_folder("..", "f1"),
        lambda: c.move_course_to_folder("p1", "..."),
        lambda: c.move_course_to_folder(None, "f1"),
        lambda: c.set_course_drip("", True, 3),
        lambda: c.set_course_drip(".", True, 3),
        lambda: c.create_course_share_key(""),
        lambda: c.create_course_share_key(".."),
    ):
        _refused(call)
    assert c.http.calls == [], c.http.calls


def test_a_unit_cannot_be_its_own_parent():
    c = _client()
    _refused(lambda: c.move_course_to_folder("p1", "p1"))
    assert c.http.calls == [], c.http.calls


def test_the_id_refusal_names_the_argument_not_the_value():
    c = _client()
    msg = _refused(lambda: c.move_course_to_folder("..", "f1"))
    assert "course_id" in msg and ".." not in msg, msg
    msg = _refused(lambda: c.move_course_to_folder("p1", " "))
    assert "parent_id" in msg, msg


# -- client: share key ------------------------------------------------------------

def test_share_key_posts_no_body_and_returns_the_answer_as_it_is():
    c = _client(_FakeHTTP(answer={"code": CODE}))
    answer = c.create_course_share_key("c1")
    assert c.http.calls == [("POST", "/courses/c1/clone-code", None)], c.http.calls
    assert answer == {"code": CODE}, answer


# -- client: import ---------------------------------------------------------------

def test_import_sends_group_id_and_key_in_the_query_and_no_body():
    c = _client()
    c.import_course(COURSE, SLUG, CODE)
    assert c.http.writes == [("POST", IMPORT_PATH, None)], c.http.writes
    # the slug is turned into the group ID (the page sends currentGroup.id), by the
    # same read every other write uses
    assert c.http.calls[0] == ("GET", f"/groups/{SLUG}", None), c.http.calls


def test_import_lowercases_the_key_like_the_page_does():
    c = _client()
    c.import_course(COURSE, SLUG, CODE.upper())
    ((_, path, _),) = c.http.writes
    assert path == IMPORT_PATH, path


def test_import_refuses_what_the_page_form_refuses_and_never_repeats_the_key():
    c = _client()
    refused = [
        lambda: c.import_course(COURSE, SLUG, CODE[:-1]),
        lambda: c.import_course(COURSE, SLUG, CODE + "0"),
        lambda: c.import_course(COURSE, SLUG, ""),
        lambda: c.import_course(COURSE, SLUG, None),
        lambda: c.import_course(COURSE, SLUG, 12345),
        # the course id is the first 32 characters of the key, the only form the page sends
        lambda: c.import_course("f" * 32, SLUG, CODE),
        lambda: c.import_course("", SLUG, CODE),
        lambda: c.import_course("..", SLUG, CODE),
    ]
    for call in refused:
        msg = _refused(call)
        assert CODE not in msg and SECRET not in msg, f"a refusal repeated the key: {msg}"
    assert c.http.calls == [], c.http.calls


def test_import_names_the_64_characters_and_the_first_32():
    c = _client()
    assert "64" in _refused(lambda: c.import_course(COURSE, SLUG, "short"))
    assert "32" in _refused(lambda: c.import_course("f" * 32, SLUG, CODE))


def test_import_refuses_an_archived_community():
    c = _client(_FakeHTTP(group={"id": GID, "archived": True}))
    _refused(lambda: c.import_course(COURSE, SLUG, CODE))
    assert c.http.writes == [], c.http.writes


# -- client: reads ----------------------------------------------------------------

def test_rules_and_levels_are_plain_gets_without_query_or_body():
    c = _client(_FakeHTTP(read={"anything": [1, 2]}))
    assert c.group_rules(SLUG) == {"anything": [1, 2]}
    assert c.group_levels(SLUG) == {"anything": [1, 2]}
    assert c.http.calls == [
        ("GET", f"/groups/{SLUG}", None),
        ("GET", f"/groups/{GID}/rules", None),
        ("GET", f"/groups/{SLUG}", None),
        ("GET", f"/groups/{GID}/levels", None),
    ], c.http.calls
    assert c.http.writes == [], "a read must never be a write"


def test_rules_and_levels_stay_readable_in_an_archived_community():
    c = _client(_FakeHTTP(group={"id": GID, "archived": True}))
    c.group_rules(SLUG)
    c.group_levels(SLUG)
    assert [x[1] for x in c.http.calls if x[0] == "GET"][1::2] == [
        f"/groups/{GID}/rules", f"/groups/{GID}/levels"], c.http.calls


def test_a_group_id_that_would_fold_the_path_is_refused_on_the_reads():
    c = _client(_FakeHTTP(group={"id": ".."}))
    _refused(lambda: c.group_rules(SLUG))
    _refused(lambda: c.group_levels(SLUG))
    assert [x for x in c.http.calls if "rules" in x[1] or "levels" in x[1]] == [], c.http.calls


# -- a security question from Skool is a stop -------------------------------------

def test_a_security_question_goes_through_and_is_never_retried():
    for call in (
        lambda c: c.set_course_drip("p1", True, 3),
        lambda c: c.move_course_to_folder("p1", "f1"),
        lambda c: c.create_course_share_key("c1"),
        lambda c: c.import_course(COURSE, SLUG, CODE),
    ):
        c = _client(_ChallengedHTTP())
        try:
            call(c)
        except SkoolSecurityChallenge as e:
            assert e.code == "AUTH-SEC-501", e
            assert CODE not in str(e) and SECRET not in str(e), "the question repeated the key"
        else:
            raise AssertionError("the security question was swallowed")
        assert len(c.http.writes) == 1, f"a second try was sent: {c.http.writes}"
    for call in (lambda c: c.group_rules(SLUG), lambda c: c.group_levels(SLUG)):
        c = _client(_ChallengedHTTP())
        try:
            call(c)
        except SkoolSecurityChallenge:
            pass
        else:
            raise AssertionError("the security question was swallowed on a read")
        assert len([x for x in c.http.calls if x[1] != f"/groups/{SLUG}"]) == 1, c.http.calls


# -- the real HTTP layer: what goes on the wire -----------------------------------

class _Resp:
    def __init__(self, code, text):
        self.status_code, self.text = code, text


class _Transport:
    """Stands in for curl_cffi: the write path is ours, the wire is not."""

    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.calls = resp, exc, []

    def _record(self, verb):
        def send(url, **kw):
            self.calls.append((verb, url, kw))
            if self.exc is not None:
                raise self.exc
            return self.resp
        return send

    def __getattr__(self, name):
        if name in ("post", "put", "delete"):
            return self._record(name.upper())
        raise AttributeError(name)


def _real_http(resp=None, exc=None) -> SkoolHTTP:
    h = SkoolHTTP.__new__(SkoolHTTP)
    h.session = type("S", (), {"cookie_header": "c", "auth_token": "t", "waf_token": "w"})()
    h._cache = {}
    h._profile = {"ua": "ua", "lang": "en", "sec_ch_ua": "", "platform": ""}
    h._http = _Transport(resp, exc)
    return h


def _real_client(resp=None, exc=None):
    c = SkoolClient.__new__(SkoolClient)
    c.http = _real_http(resp or _Resp(200, json.dumps({})), exc)
    c.group_id_for = lambda slug, for_write=False: GID  # no read of Skool here
    return c


def test_body_less_calls_send_no_json_and_the_drip_sends_what_is_documented():
    c = _real_client()
    c.set_course_drip("p1", True, 7)
    c.move_course_to_folder("p1", "f1")
    c.create_course_share_key(COURSE)
    c.import_course(COURSE, SLUG, CODE)
    sent = [(v, u.removeprefix(BASE), kw.get("json", "NO-BODY"))
            for v, u, kw in c.http._http.calls]
    assert sent == [
        ("POST", "/courses/p1/update-drip", {"enabled": True, "days": 7}),
        ("POST", "/courses/p1/update-parent?parent=f1", "NO-BODY"),
        ("POST", f"/courses/{COURSE}/clone-code", "NO-BODY"),
        ("POST", IMPORT_PATH, "NO-BODY"),  # the real key goes on the wire, as it must
    ], sent


# -- tools: nothing leaves without confirm ----------------------------------------

WRITE_TOOLS = ("set_course_drip", "move_course_to_folder", "create_course_share_key",
               "import_course")
READ_TOOLS = ("get_community_rules", "get_community_levels")


def _drafts(c):
    """Every write tool once, confirm=false, with ordinary arguments."""
    calls = {
        "set_course_drip": lambda m: _fn(m.set_course_drip)("p1", True, 7),
        "move_course_to_folder": lambda m: _fn(m.move_course_to_folder)("p1", "f1"),
        "create_course_share_key": lambda m: _fn(m.create_course_share_key)("c1"),
        "import_course": lambda m: _fn(m.import_course)(COURSE, SLUG, CODE),
    }
    return {name: _with_client(c, call) for name, call in calls.items()}


def test_the_tools_exist_and_are_flagged():
    import catknows.mcp_server as m
    for name in WRITE_TOOLS:
        assert hasattr(m, name), f"{name} is not registered with CATKNOWS_ALLOW_WRITE=1"
        assert name in m._DESTRUCTIVE, f"{name} must be flagged destructive"
        assert name not in m._READ_ONLY, f"{name} writes"
    for name in READ_TOOLS:
        assert hasattr(m, name), f"{name} is not registered"
        assert name in m._READ_ONLY, f"{name} only reads"
        assert name not in m._DESTRUCTIVE, f"{name} only reads"


def test_the_audit_context_names_the_course_but_never_the_key():
    import catknows.mcp_server as m
    assert m._AUDIT_ARGS["course_id"] == "course_item_id", m._AUDIT_ARGS
    assert m._AUDIT_ARGS["community_slug"] == "community", m._AUDIT_ARGS
    assert "clone_code" not in m._AUDIT_ARGS, "the share key must never become an audit field"
    assert "code" not in m._AUDIT_ARGS, m._AUDIT_ARGS


def test_drafts_send_nothing_and_say_what_would_happen():
    c = _client()
    drafts = _drafts(c)
    assert c.http.calls == [], f"a draft must not touch Skool at all: {c.http.calls}"
    for name, out in drafts.items():
        assert out["status"].startswith("DRAFT"), (name, out)
        assert "confirm=true" in out["next_step"], (name, out)

    drip = drafts["set_course_drip"]["would_set_drip"]
    assert (drip["course_id"], drip["enabled"], drip["days"]) == ("p1", True, 7), drip
    move = drafts["move_course_to_folder"]["would_move"]
    assert (move["course_id"], move["parent_id"]) == ("p1", "f1"), move
    assert drafts["create_course_share_key"]["would_create_share_key"]["course_id"] == "c1"
    imp = drafts["import_course"]["would_import"]
    assert (imp["course_id"], imp["community"]) == (COURSE, SLUG), imp


def test_the_drip_draft_names_both_directions():
    c = _client()
    on = _with_client(c, lambda m: _fn(m.set_course_drip)("p1", True, 7))
    off = _with_client(c, lambda m: _fn(m.set_course_drip)("p1", False))
    assert "7 days" in on["would_set_drip"]["does"], on
    assert "off" in off["would_set_drip"]["does"], off
    assert off["would_set_drip"]["days"] == 0, off
    assert c.http.calls == [], c.http.calls


def test_the_import_draft_never_shows_the_key():
    c = _client()
    out = _with_client(c, lambda m: _fn(m.import_course)(COURSE, SLUG, CODE))
    blob = json.dumps(out)
    assert CODE not in blob and SECRET not in blob, f"the draft shows the share key: {blob}"
    assert "64" in blob, "the draft should say how long the key is, instead of showing it"
    assert c.http.calls == [], c.http.calls


def test_drafts_carry_a_fixed_note_on_what_is_not_verified():
    """A warning only in a docstring is not seen when the human confirms: it sits in the draft."""
    drafts = _drafts(_client())
    for name, out in drafts.items():
        note = out.get("not_verified")
        assert isinstance(note, str) and len(note) > 60, (name, out)
    assert "999" in drafts["set_course_drip"]["not_verified"]
    assert "page" in drafts["set_course_drip"]["not_verified"].lower()
    assert "folder" in drafts["move_course_to_folder"]["not_verified"].lower()
    key_note = drafts["create_course_share_key"]["not_verified"].lower()
    assert "access code" in key_note and "anybody" in key_note, key_note
    imp_note = drafts["import_course"]["not_verified"].lower()
    assert "published" in imp_note and "draft" in imp_note, imp_note
    for out in drafts.values():
        assert "not run" in out["not_verified"] or "not verified" in out["not_verified"].lower()


def test_no_course_setting_draft_asks_for_the_humanizing_pass():
    for name, out in _drafts(_client()).items():
        assert "before_you_show_this" not in out, f"{name} carries no prose for a human to read"


def test_a_draft_already_refuses_what_the_client_would_refuse():
    c = _client()
    refused = [
        lambda m: _fn(m.set_course_drip)("p1", True, 0),
        lambda m: _fn(m.set_course_drip)("p1", True, 1000),
        lambda m: _fn(m.set_course_drip)("", True, 3),
        lambda m: _fn(m.set_course_drip)("..", False),
        lambda m: _fn(m.move_course_to_folder)("p1", "p1"),
        lambda m: _fn(m.move_course_to_folder)("", "f1"),
        lambda m: _fn(m.move_course_to_folder)("p1", "..."),
        lambda m: _fn(m.create_course_share_key)(" "),
        lambda m: _fn(m.create_course_share_key)(".."),
        lambda m: _fn(m.import_course)(COURSE, SLUG, CODE[:-1]),
        lambda m: _fn(m.import_course)("f" * 32, SLUG, CODE),
        lambda m: _fn(m.import_course)("", SLUG, CODE),
    ]
    for call in refused:
        msg = _refused(lambda: _with_client(c, call))
        assert CODE not in msg and SECRET not in msg, msg
    assert c.http.calls == [], c.http.calls


# -- tools: confirm=true ----------------------------------------------------------

def test_confirmed_tools_send_exactly_the_documented_calls():
    cases = [
        (lambda m: _fn(m.set_course_drip)("p1", True, 7, confirm=True), "drip_set",
         [("POST", "/courses/p1/update-drip", {"enabled": True, "days": 7})]),
        (lambda m: _fn(m.set_course_drip)("p1", False, confirm=True), "drip_set",
         [("POST", "/courses/p1/update-drip", {"enabled": False, "days": 0})]),
        (lambda m: _fn(m.move_course_to_folder)("p1", "f1", confirm=True), "moved",
         [("POST", "/courses/p1/update-parent?parent=f1", None)]),
        (lambda m: _fn(m.create_course_share_key)("c1", confirm=True), "share_key_created",
         [("POST", "/courses/c1/clone-code", None)]),
        (lambda m: _fn(m.import_course)(COURSE, SLUG, CODE, confirm=True), "import_started",
         [("POST", IMPORT_PATH, None)]),
    ]
    for call, status, writes in cases:
        c = _client(_FakeHTTP(answer={"code": CODE}))
        out = _with_client(c, call)
        assert out["status"] == status, (status, out)
        assert c.http.writes == writes, (status, c.http.writes)


def test_the_share_key_comes_back_to_the_caller_and_only_the_key():
    answer = {"code": CODE, "apiKeys": ["SECRET-KEY"], "other": "not passed on"}
    c = _client(_FakeHTTP(answer=answer))
    out = _with_client(c, lambda m: _fn(m.create_course_share_key)("c1", confirm=True))
    assert out["code"] == CODE, out
    assert out["course_id"] == "c1", out
    blob = json.dumps(out)
    assert "SECRET-KEY" not in blob and "not passed on" not in blob, blob
    assert "warning" in out and "duplicate" in out["warning"], out


def test_an_answer_without_a_key_is_reported_without_being_shown():
    c = _client(_FakeHTTP(answer={"id": "x-1", "token": "SHOULD-NOT-BE-SHOWN"}))
    try:
        _with_client(c, lambda m: _fn(m.create_course_share_key)("c1", confirm=True))
    except SkoolHTTPError as e:
        assert "SHOULD-NOT-BE-SHOWN" not in str(e), str(e)
        assert "code" in str(e), str(e)
    else:
        raise AssertionError("an answer without a code must not pass for a key")


def test_the_answers_of_the_other_writes_go_through_the_scrub():
    answer = {"id": "x-1", "apiKeys": ["SECRET-KEY"]}
    for call in (
        lambda m: _fn(m.set_course_drip)("p1", True, 7, confirm=True),
        lambda m: _fn(m.move_course_to_folder)("p1", "f1", confirm=True),
        lambda m: _fn(m.import_course)(COURSE, SLUG, CODE, confirm=True),
    ):
        c = _client(_FakeHTTP(answer=answer))
        out = _with_client(c, call)
        assert "SECRET-KEY" not in json.dumps(out), "the answer must go through _safe_raw"


def test_the_import_tool_never_returns_the_key():
    c = _client(_FakeHTTP(answer={}))
    out = _with_client(c, lambda m: _fn(m.import_course)(COURSE, SLUG, CODE, confirm=True))
    blob = json.dumps(out)
    assert CODE not in blob and SECRET not in blob, blob


def test_an_import_into_an_archived_community_stops_at_the_tool():
    c = _client(_FakeHTTP(group={"id": GID, "archived": True}))
    _refused(lambda: _with_client(
        c, lambda m: _fn(m.import_course)(COURSE, SLUG, CODE, confirm=True)))
    assert c.http.writes == [], c.http.writes


def test_a_security_question_is_not_turned_into_a_result_by_the_tools():
    calls = (
        lambda m: _fn(m.set_course_drip)("p1", True, 7, confirm=True),
        lambda m: _fn(m.move_course_to_folder)("p1", "f1", confirm=True),
        lambda m: _fn(m.create_course_share_key)("c1", confirm=True),
        lambda m: _fn(m.import_course)(COURSE, SLUG, CODE, confirm=True),
        lambda m: _fn(m.get_community_rules)(SLUG),
        lambda m: _fn(m.get_community_levels)(SLUG),
    )
    for call in calls:
        c = _client(_ChallengedHTTP())
        try:
            _with_client(c, call)
        except SkoolSecurityChallenge:
            pass
        else:
            raise AssertionError("the tool swallowed Skool's security question")
        asked = [x for x in c.http.calls if x[1] != f"/groups/{SLUG}"]
        assert len(asked) == 1, f"a second try was sent: {asked}"


# -- tools: the two reads ---------------------------------------------------------

def test_the_read_tools_return_skools_answer_raw_through_the_scrub():
    raw = {"rules": [{"title": "Be kind", "content": "Always."}], "apiKeys": ["SECRET-KEY"],
           "unknown_field": {"kept": 1}}
    c = _client(_FakeHTTP(read=raw))
    rules = _with_client(c, lambda m: _fn(m.get_community_rules)(SLUG))
    assert "SECRET-KEY" not in json.dumps(rules), "raw answers go through _safe_raw"
    assert rules == {"rules": [{"title": "Be kind", "content": "Always."}],
                     "unknown_field": {"kept": 1}}, rules  # nothing invented, nothing reshaped
    c = _client(_FakeHTTP(read={"levels": [{"number": 1, "title": "Kitten"}]}))
    levels = _with_client(c, lambda m: _fn(m.get_community_levels)(SLUG))
    assert levels == {"levels": [{"number": 1, "title": "Kitten"}]}, levels


def test_the_read_tools_ask_skool_for_exactly_one_get_each():
    c = _client()
    _with_client(c, lambda m: _fn(m.get_community_rules)(SLUG))
    _with_client(c, lambda m: _fn(m.get_community_levels)(SLUG))
    asked = [x[1] for x in c.http.calls if x[1] != f"/groups/{SLUG}"]
    assert asked == [f"/groups/{GID}/rules", f"/groups/{GID}/levels"], c.http.calls
    assert c.http.writes == [], c.http.writes


def test_the_read_tools_work_in_an_archived_community():
    c = _client(_FakeHTTP(group={"id": GID, "archived": True}, read={"rules": []}))
    assert _with_client(c, lambda m: _fn(m.get_community_rules)(SLUG)) == {"rules": []}
    assert _with_client(c, lambda m: _fn(m.get_community_levels)(SLUG)) == {"rules": []}


# -- audit: the line names the target, never the key ------------------------------

def _lines():
    p = audit.log_path()
    return [json.loads(x) for x in p.read_text("utf-8").splitlines()] if p.exists() else []


@contextmanager
def _audit_log():
    """Points the audit log at a fresh file for one test, then puts it back."""
    prev = os.environ.get("CATKNOWS_AUDIT_LOG")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CATKNOWS_AUDIT_LOG"] = str(Path(tmp) / "w.jsonl")
        try:
            yield
        finally:
            if prev is None:
                os.environ.pop("CATKNOWS_AUDIT_LOG", None)
            else:
                os.environ["CATKNOWS_AUDIT_LOG"] = prev


def _without_key(blob: str, where: str):
    assert CODE not in blob, f"the share key reached {where}"
    assert SECRET not in blob, f"the secret half of the share key reached {where}"


def test_redact_hides_only_the_value_of_the_key_parameter():
    assert audit.redact(IMPORT_PATH) == (
        f"/courses/{COURSE}/import?group={GID}&clone_code=[redacted]")
    assert audit.redact(f"/x?clone_code={CODE}") == "/x?clone_code=[redacted]"
    assert audit.redact(f"/x?a=1&clone_code={CODE}&b=2") == "/x?a=1&clone_code=[redacted]&b=2"
    for plain in ("/courses/p1/update-parent?parent=f1", "/posts/p1/pin-comment?comment=c1",
                  "/courses/p1/state?state=published", "/courses/p1/clone-code", ""):
        assert audit.redact(plain) == plain, plain
    assert audit.redact(f"{BASE}{IMPORT_PATH}").endswith("clone_code=[redacted]")


def test_record_never_writes_the_key_even_when_given_a_path_with_it():
    with _audit_log():
        audit.record(method="post", path=IMPORT_PATH, status=200, response={})
        audit.record(method="post", path=f"/courses/{COURSE}/clone-code", status=200,
                     response={"code": CODE})
        blob = audit.log_path().read_text("utf-8")
        _without_key(blob, "the audit log")
        first, second = _lines()
        assert first["path"].endswith("clone_code=[redacted]"), first
        assert "group=" + GID in first["path"], "the other query values explain the write"
        assert len(second["response_hash"]) == 16 and "created_id" not in second, second


def test_every_course_setting_leaves_a_line_without_the_key():
    with _audit_log():
        c = _real_client(_Resp(200, json.dumps({"code": CODE})))
        _with_client(c, lambda m: _fn(m.set_course_drip)("p-1", True, 7, confirm=True))
        _with_client(c, lambda m: _fn(m.move_course_to_folder)("p-1", "f-1", confirm=True))
        shown = _with_client(c, lambda m: _fn(m.create_course_share_key)(
            COURSE, confirm=True))
        _with_client(c, lambda m: _fn(m.import_course)(COURSE, SLUG, CODE, confirm=True))

        assert shown["code"] == CODE, "the caller must get the key: that is the tool's job"
        blob = audit.log_path().read_text("utf-8")
        _without_key(blob, "the audit log")
        drip, parent, key, imp = _lines()
        assert (drip["tool"], drip["method"], drip["path"]) == (
            "set_course_drip", "POST", "/courses/p-1/update-drip"), drip
        assert drip["course_item_id"] == "p-1" and drip["status"] == 200, drip
        assert (parent["tool"], parent["path"]) == (
            "move_course_to_folder", "/courses/p-1/update-parent?parent=f-1"), parent
        assert parent["course_item_id"] == "p-1", parent
        assert (key["tool"], key["path"]) == (
            "create_course_share_key", f"/courses/{COURSE}/clone-code"), key
        assert key["course_item_id"] == COURSE, key
        assert len(key["response_hash"]) == 16, "only a fingerprint of the answer is kept"
        assert "created_id" not in key, key
        assert (imp["tool"], imp["method"]) == ("import_course", "POST"), imp
        assert imp["path"] == f"/courses/{COURSE}/import?group={GID}&clone_code=[redacted]", imp
        assert imp["community"] == SLUG and imp["course_item_id"] == COURSE, imp
        assert "clone_code" not in {k for k in imp if k not in ("path",)}, imp
        # the request itself did carry the real key (a redacted request would be no import)
        sent = [u for _, u, _ in c.http._http.calls if "/import" in u]
        assert sent == [f"{BASE}{IMPORT_PATH}"], sent


def _failing(resp=None, exc=None):
    """Run the import against a transport that fails; return (raised error, log text)."""
    with _audit_log():
        c = _real_client(resp, exc)
        try:
            with audit.context("import_course", community=SLUG, course_item_id=COURSE):
                c.import_course(COURSE, SLUG, CODE)
        except Exception as e:  # noqa: BLE001 - the test wants whatever comes
            err = e
        else:
            raise AssertionError("the call was expected to fail")
        lines = _lines()
        assert len(lines) == 1, lines
        return err, audit.log_path().read_text("utf-8"), lines[0]


def test_a_failed_import_leaves_no_key_in_the_log_or_the_error():
    cases = {
        "HTTP 500": _Resp(500, "server exploded"),
        "HTTP 500 echoing the query": _Resp(500, f"bad request ...?group={GID}&clone_code={CODE}"),
        "HTTP 400 fields": _Resp(400, json.dumps({"fields": [{"error": "Invalid code"}]})),
        "401 not permitted": _Resp(401, '{"error":"action not permitted"}'),
        "403 waf": _Resp(403, "blocked"),
        "bad json": _Resp(200, "this is <not> json"),
    }
    for label, resp in cases.items():
        err, log, line = _failing(resp)
        _without_key(str(err), f"the error text ({label})")
        _without_key(log, f"the audit log ({label})")
        assert isinstance(err, SkoolHTTPError), (label, err)
        assert line["path"].endswith("clone_code=[redacted]"), (label, line)


def test_a_network_error_leaves_no_key_in_the_log_or_the_error():
    # curl's own error text names the host, not the query; the second one is the
    # worst case, a library that repeats the whole URL: our copy of it is redacted
    for message in ("Could not resolve host: api2.skool.com",
                    f"failed: {BASE}{IMPORT_PATH}"):
        err, log, line = _failing(exc=RequestException(message))
        _without_key(str(err), "the error text")
        _without_key(log, "the audit log")
        assert line["status"] == "network_error", line
        assert isinstance(err, SkoolHTTPError), err


def test_a_security_question_on_the_import_leaves_no_key_anywhere():
    err, log, line = _failing(_Resp(200, json.dumps({"code": "AUTH-SEC-501"})))
    assert isinstance(err, SkoolSecurityChallenge), err
    _without_key(str(err), "the error text")
    _without_key(log, "the audit log")
    assert line["status"] == "security_challenge" and line["note"] == "AUTH-SEC-501", line
    assert line["path"].endswith("clone_code=[redacted]"), line


def test_a_failed_share_key_request_leaves_nothing_of_a_key():
    with _audit_log():
        c = _real_client(_Resp(500, json.dumps({"code": CODE})))
        try:
            c.create_course_share_key(COURSE)
        except SkoolHTTPError:
            pass
        (line,) = _lines()
        assert line["status"] == 500 and "response_hash" not in line, line
        _without_key(audit.log_path().read_text("utf-8"), "the audit log")


# -- the self-check of audit.py itself must still say the same --------------------

def test_the_audit_self_check_still_runs_clean():
    import subprocess
    import sys
    done = subprocess.run([sys.executable, "-m", "catknows.audit"], capture_output=True,
                          text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "audit self-check OK" in done.stdout, done.stdout


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all green")
