"""Self-check: the small post actions are sent only on purpose and exactly as Skool's own client sends them.

Nothing here touches Skool. The client talks to a stand-in that records every
call, and the HTTP layer (for the body and audit tests) to a stand-in transport.

The thirteen calls (basis https://api2.skool.com). Source: Skool's own page code
(build 1794436674711), read from the plan notes `vertraege.md` (rows
updatePostVote, pinPost, unpinPost, createLabel, updateLabel, deleteLabel,
updateLandingPageDescription, disablePostComments, enablePostComments,
pinComment, unpinComment, followPost, unfollowPost), the catalogue
(`skool-re-api-katalog-rest`, `-groups`), the function maps
(`skool-re-funktionskarte-inhalte` §3, §5-§8, `-community-settings` §1.8) and
the page code itself (`updateLabel`, `deleteLabel`, `pinComment`,
`updateLandingPageDescription`).
Never run live.

    updatePostVote                 PUT   /posts/{id}/vote                       {old, new}
    pinPost                        POST  /posts/{id}/pin                        {}  (Skool's client: none)
    unpinPost                      POST  /posts/{id}/unpin                      {}  (same)
    createLabel                    POST  /labels                                {group_id, metadata:
                                                          {display_name, default_sort, post_write_role}}
    updateLabel                    PUT   /labels/{id}                           {display_name}
    deleteLabel                    DELETE /labels/{id}[?migrate={other id}]     no body
    updateLandingPageDescription   POST  /groups/{gid}/update-landing-page-description   {description}
    disablePostComments            POST  /posts/{id}/disable-comments           no body
    enablePostComments             POST  /posts/{id}/enable-comments            no body
    pinComment                     POST  /posts/{id}/pin-comment?comment={cid}  no body
    unpinComment                   POST  /posts/{id}/unpin-comment?comment={cid} no body
    followPost                     POST  /posts/{id}/follow                     no body
    unfollowPost                   POST  /posts/{id}/unfollow                   no body

Votes (Karte Inhalte §6): only "" and "up", no downvote. Categories (Karte
Inhalte §3, community-settings §1.8): name at most 30 characters,
`default_sort` newest-cm | newest | best-1w | best-1m (best-1y is shown but not
selectable in the form), `post_write_role` 3 (admins) | 4 (members).

NOT built: updateGroup, updateLabel's other two fields,
pinPostToModule / unpinPostFromModule, movePinnedPost.

Deliberately NOT asserted, because the notes do not say: what Skool answers
(the stand-in answers are made up), what it does with a vote whose `old` does
not match the stored vote, the format Skool wants for the About `description`
(plain text or "[v2]" TipTap), why pinPost once answered 400.

Run: python test_post_actions.py    (no network, no pytest)
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

ABOUT = "We build small things.\nSecond line, with an emoji \U0001F431 and a quote \"q\"."


class _FakeHTTP:
    """Records every call of every kind, reads included: 'no call' means none."""

    def __init__(self, group=None, answer=None):
        self.calls = []
        self.group = {"id": GID} if group is None else group
        self.answer = {"id": "x-1"} if answer is None else answer

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


# -- client: votes ----------------------------------------------------------------

def test_a_like_puts_old_and_new():
    c = _client()
    out = c.vote_post("p1", "", "up")
    assert out == {"id": "x-1"}, "the client hands back Skool's answer as it is"
    assert c.http.calls == [("PUT", "/posts/p1/vote", {"old": "", "new": "up"})], c.http.calls


def test_taking_a_like_back_puts_up_to_empty():
    c = _client()
    c.vote_post("p1", "up", "")
    assert c.http.calls == [("PUT", "/posts/p1/vote", {"old": "up", "new": ""})], c.http.calls


def test_only_the_two_documented_vote_values_are_sent():
    bad = [("", "down"), ("down", ""), ("up", "down"), ("UP", ""), ("", "UP"), ("", "1"),
           (None, "up"), ("", None), (1, "up"), ("", 1), ("up", True)]
    for old, new in bad:
        c = _client()
        _refused(lambda: c.vote_post("p1", old, new))
        assert c.http.calls == [], (old, new, c.http.calls)


def test_a_vote_that_changes_nothing_is_not_sent():
    for same in ("", "up"):
        c = _client()
        _refused(lambda: c.vote_post("p1", same, same))
        assert c.http.calls == [], c.http.calls


# -- client: pin / unpin a post ---------------------------------------------------

def test_pin_and_unpin_post_send_an_empty_object():
    c = _client()
    c.pin_post("p1")
    c.pin_post("p1", pinned=False)
    assert c.http.calls == [
        ("POST", "/posts/p1/pin", {}),
        ("POST", "/posts/p1/unpin", {}),
    ], c.http.calls


def test_pin_needs_a_real_boolean():
    """A text "false" is truthy: it must not turn an unpin into a pin."""
    for bad in ("false", "", 0, 1, None):
        c = _client()
        _refused(lambda: c.pin_post("p1", pinned=bad))
        assert c.http.calls == [], (bad, c.http.calls)


# -- client: comments on/off, pin a comment, follow ------------------------------

def test_comments_off_and_on_send_no_body():
    c = _client()
    c.set_post_comments("p1", enabled=False)
    c.set_post_comments("p1", enabled=True)
    assert c.http.calls == [
        ("POST", "/posts/p1/disable-comments", None),
        ("POST", "/posts/p1/enable-comments", None),
    ], c.http.calls


def test_pin_comment_and_unpin_comment_carry_the_comment_in_the_query():
    c = _client()
    c.pin_comment("p1", "c1")
    c.pin_comment("p1", "c1", pinned=False)
    assert c.http.calls == [
        ("POST", "/posts/p1/pin-comment?comment=c1", None),
        ("POST", "/posts/p1/unpin-comment?comment=c1", None),
    ], c.http.calls


def test_follow_and_unfollow_send_no_body():
    c = _client()
    c.follow_post("p1")
    c.follow_post("p1", follow=False)
    assert c.http.calls == [
        ("POST", "/posts/p1/follow", None),
        ("POST", "/posts/p1/unfollow", None),
    ], c.http.calls


def test_the_switches_need_real_booleans():
    for call in (lambda c, v: c.set_post_comments("p1", enabled=v),
                 lambda c, v: c.pin_comment("p1", "c1", pinned=v),
                 lambda c, v: c.follow_post("p1", follow=v)):
        for bad in ("false", "", 0, 1, None):
            c = _client()
            _refused(lambda: call(c, bad))
            assert c.http.calls == [], (bad, c.http.calls)


def test_ids_and_the_comment_value_are_encoded_into_the_path_text():
    c = _client()
    c.pin_comment("a b/c", "x&y=z?w")
    c.follow_post("../p?q=1")
    c.vote_post("p/1", "", "up")
    assert [x[1] for x in c.http.calls] == [
        "/posts/a%20b%2Fc/pin-comment?comment=x%26y%3Dz%3Fw",
        "/posts/..%2Fp%3Fq%3D1/follow",
        "/posts/p%2F1/vote",
    ], c.http.calls


def _every_id_call(bad):
    """Each client call that builds a path or query from an id, with `bad` as that id."""
    return [
        lambda c: c.vote_post(bad, "", "up"),
        lambda c: c.pin_post(bad),
        lambda c: c.pin_post(bad, pinned=False),
        lambda c: c.set_post_comments(bad, enabled=False),
        lambda c: c.set_post_comments(bad, enabled=True),
        lambda c: c.pin_comment(bad, "c1"),
        lambda c: c.pin_comment("p1", bad),
        lambda c: c.pin_comment("p1", bad, pinned=False),
        lambda c: c.follow_post(bad),
        lambda c: c.follow_post(bad, follow=False),
        lambda c: c.rename_category(bad, "New"),
    ]


def test_an_empty_or_odd_id_is_refused_before_any_call():
    for bad in ("", "  ", None, 7):
        for call in _every_id_call(bad):
            c = _client()
            _refused(lambda: call(c))
            assert c.http.calls == [], (bad, c.http.calls)


def test_ids_made_only_of_dots_are_refused_before_any_call():
    """Dots stay dots when percent-encoded, and libcurl folds `/posts/../pin` to `/pin`."""
    for bad in (".", "..", "...", "....."):
        for call in _every_id_call(bad):
            c = _client()
            _refused(lambda: call(c))
            assert c.http.calls == [], (bad, c.http.calls)
    c = _client()
    c.pin_post("a.b")   # a dot between other characters is an ordinary id
    c.pin_post(".a")
    assert [x[1] for x in c.http.calls] == ["/posts/a.b/pin", "/posts/.a/pin"], c.http.calls


def test_the_id_refusal_names_the_argument_not_the_value():
    msg = _refused(lambda: _client().pin_comment("p1", ".."))
    assert "comment_id" in msg and ".." not in msg, msg
    msg = _refused(lambda: _client().pin_post(""))
    assert "post_id" in msg, msg
    msg = _refused(lambda: _client().rename_category(" ", "x"))
    assert "category_id" in msg, msg
    msg = _refused(lambda: _client().vote_post(["secret-id-value"], "", "up"))
    assert "secret-id-value" not in msg and "post_id" in msg, msg


# -- client: categories (Skool's labels) ------------------------------------------

def test_create_category_posts_group_id_and_the_three_metadata_fields():
    c = _client()
    out = c.create_category(SLUG, "Questions", default_sort="newest-cm", post_write_role=4)
    assert out == {"id": "x-1"}
    assert c.http.calls == [
        ("GET", f"/groups/{SLUG}", None),  # group_id_for: slug -> uuid
        ("POST", "/labels", {"group_id": GID, "metadata": {
            "display_name": "Questions", "default_sort": "newest-cm", "post_write_role": 4}}),
    ], c.http.calls


def test_create_category_takes_every_documented_sort_and_role():
    for sort in ("newest-cm", "newest", "best-1w", "best-1m"):
        for role in (3, 4):
            c = _client()
            c.create_category(SLUG, "x", default_sort=sort, post_write_role=role)
            (_, _, body), = c.http.writes
            assert body["metadata"]["default_sort"] == sort, body
            assert body["metadata"]["post_write_role"] == role, body


def test_create_category_has_no_defaults_of_its_own():
    c = _client()
    for call in (lambda: c.create_category(SLUG, "x"),
                 lambda: c.create_category(SLUG, "x", default_sort="newest"),
                 lambda: c.create_category(SLUG, "x", post_write_role=4)):
        try:
            call()
        except TypeError:
            pass
        else:
            raise AssertionError("a missing sort or role must be an error, not a default")
    assert c.http.calls == [], c.http.calls


def test_create_category_refuses_what_the_form_would_not_send():
    bad = [
        dict(display_name="", default_sort="newest", post_write_role=4),
        dict(display_name="   ", default_sort="newest", post_write_role=4),
        dict(display_name="x" * 31, default_sort="newest", post_write_role=4),
        dict(display_name=12, default_sort="newest", post_write_role=4),
        dict(display_name="x", default_sort="best-1y", post_write_role=4),  # shown, not selectable
        dict(display_name="x", default_sort="top", post_write_role=4),
        dict(display_name="x", default_sort=None, post_write_role=4),
        dict(display_name="x", default_sort="newest", post_write_role=2),
        dict(display_name="x", default_sort="newest", post_write_role="4"),
        dict(display_name="x", default_sort="newest", post_write_role=True),
    ]
    for kw in bad:
        c = _client()
        _refused(lambda: c.create_category(SLUG, **kw))
        assert c.http.calls == [], f"a refused category must cause no call at all: {kw}"
    ok = _client()
    ok.create_category(SLUG, "x" * 30, default_sort="newest", post_write_role=3)
    assert len(ok.http.writes) == 1


def test_the_category_refusal_names_the_limit_not_the_text():
    secret = "Private inner circle category " * 2
    msg = _refused(lambda: _client().create_category(
        SLUG, secret, default_sort="newest", post_write_role=4))
    assert "30" in msg and secret not in msg, msg


def test_create_category_refuses_an_archived_community():
    c = _client(_FakeHTTP(group={"id": GID, "archived": True}))
    _refused(lambda: c.create_category(SLUG, "x", default_sort="newest", post_write_role=4))
    assert c.http.writes == [], c.http.writes


def test_rename_category_puts_only_the_display_name():
    c = _client()
    out = c.rename_category("lb1", "New name")
    assert out == {"id": "x-1"}
    assert c.http.calls == [("PUT", "/labels/lb1", {"display_name": "New name"})], c.http.calls


def test_rename_category_encodes_the_id_and_checks_the_name():
    c = _client()
    c.rename_category("a b/c", "x" * 30)
    assert c.http.calls[0][1] == "/labels/a%20b%2Fc", c.http.calls
    for bad in ("", "  ", "x" * 31, None, 5):
        c = _client()
        _refused(lambda: c.rename_category("lb1", bad))
        assert c.http.calls == [], (bad, c.http.calls)


def test_delete_category_sends_a_bare_delete_and_the_migrate_query_only_when_given():
    c = _client()
    assert c.delete_category("lb1") == {}
    c.delete_category("lb1", migrate_to="lb2")
    assert c.http.calls == [("DELETE", "/labels/lb1", None),
                            ("DELETE", "/labels/lb1?migrate=lb2", None)], c.http.calls


def test_delete_category_encodes_both_ids_and_refuses_bad_ones():
    c = _client()
    c.delete_category("a b/c", migrate_to="x&y=1")
    assert c.http.calls[0][1] == "/labels/a%20b%2Fc?migrate=x%26y%3D1", c.http.calls
    for cid, target in (("", ""), ("..", ""), (" ", ""), ("lb1", " "), ("lb1", 5),
                        ("lb1", None), ("lb1", "lb1")):
        c = _client()
        _refused(lambda: c.delete_category(cid, migrate_to=target))
        assert c.http.calls == [], (cid, target, c.http.calls)


# -- client: the About text --------------------------------------------------------

def test_about_description_posts_the_text_exactly_as_given():
    c = _client()
    before = ABOUT
    out = c.update_about_description(SLUG, ABOUT)
    assert out == {"id": "x-1"}
    assert c.http.calls == [
        ("GET", f"/groups/{SLUG}", None),
        ("POST", f"/groups/{GID}/update-landing-page-description", {"description": ABOUT}),
    ], c.http.calls
    assert ABOUT == before


def test_about_description_encodes_the_group_id():
    c = _client(_FakeHTTP(group={"id": "g/1?x"}))
    c.update_about_description(SLUG, ABOUT)
    assert c.http.writes[0][1] == "/groups/g%2F1%3Fx/update-landing-page-description", c.http.calls


def test_about_description_refuses_a_group_id_that_would_fold_the_path():
    for bad in (".", "..", "...", "", " ", None):
        c = _client()
        c.group_id_for = lambda slug, for_write=False, _b=bad: _b  # the id Skool "answered"
        _refused(lambda: c.update_about_description(SLUG, ABOUT))
        assert c.http.calls == [], (bad, c.http.calls)


def test_about_description_refuses_an_archived_community_and_odd_text():
    c = _client(_FakeHTTP(group={"id": GID, "archived": True}))
    _refused(lambda: c.update_about_description(SLUG, ABOUT))
    assert c.http.writes == [], c.http.writes
    for bad in ("", "   \n ", None, 5, ["a"]):
        c = _client()
        _refused(lambda: c.update_about_description(SLUG, bad))
        assert c.http.calls == [], (bad, c.http.calls)


# -- a security question from Skool is a stop ---------------------------------------

def test_a_security_question_goes_through_and_is_never_retried():
    for call in (
        lambda c: c.vote_post("p1", "", "up"),
        lambda c: c.pin_post("p1"),
        lambda c: c.pin_post("p1", pinned=False),
        lambda c: c.create_category(SLUG, "x", default_sort="newest", post_write_role=4),
        lambda c: c.rename_category("lb1", "x"),
        lambda c: c.update_about_description(SLUG, ABOUT),
        lambda c: c.set_post_comments("p1", enabled=False),
        lambda c: c.pin_comment("p1", "c1"),
        lambda c: c.follow_post("p1"),
    ):
        c = _client(_ChallengedHTTP())
        try:
            call(c)
        except SkoolSecurityChallenge as e:
            assert e.code == "AUTH-SEC-501", e
        else:
            raise AssertionError("the security question was swallowed")
        assert len(c.http.writes) == 1, f"a second try was sent: {c.http.writes}"


# -- the real HTTP layer: what goes on the wire for "no body" -----------------------

class _Resp:
    def __init__(self, code, text):
        self.status_code, self.text = code, text


class _Transport:
    """Stands in for curl_cffi: the write path is ours, the wire is not."""

    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def _record(self, verb):
        def send(url, **kw):
            self.calls.append((verb, url, kw))
            return self.resp
        return send

    def __getattr__(self, name):
        if name in ("post", "put", "delete"):
            return self._record(name.upper())
        raise AttributeError(name)


def _real_http(resp) -> SkoolHTTP:
    h = SkoolHTTP.__new__(SkoolHTTP)
    h.session = type("S", (), {"cookie_header": "c", "auth_token": "t", "waf_token": "w"})()
    h._cache = {}
    h._profile = {"ua": "ua", "lang": "en", "sec_ch_ua": "", "platform": ""}
    h._http = _Transport(resp)
    return h


def _real_client(resp=None):
    c = SkoolClient.__new__(SkoolClient)
    c.http = _real_http(resp or _Resp(200, json.dumps({"id": "lb-9"})))
    c.group_id_for = lambda slug, for_write=False: GID  # no read of Skool here
    return c


def test_body_less_calls_send_no_json_and_the_others_send_what_is_documented():
    c = _real_client()
    c.set_post_comments("p1", enabled=False)
    c.pin_comment("p1", "c1")
    c.follow_post("p1", follow=False)
    c.pin_post("p1")
    c.vote_post("p1", "", "up")
    base = "https://api2.skool.com"
    sent = [(v, u.removeprefix(base), kw.get("json", "NO-BODY"))
            for v, u, kw in c.http._http.calls]
    assert sent == [
        ("POST", "/posts/p1/disable-comments", "NO-BODY"),
        ("POST", "/posts/p1/pin-comment?comment=c1", "NO-BODY"),
        ("POST", "/posts/p1/unfollow", "NO-BODY"),
        ("POST", "/posts/p1/pin", {}),
        ("PUT", "/posts/p1/vote", {"old": "", "new": "up"}),
    ], sent


# -- tools: nothing leaves without confirm ------------------------------------------

TOOLS = ("vote_post", "pin_post", "create_category", "rename_category", "delete_category",
         "update_about_description", "set_post_comments", "pin_comment", "follow_post")

PROSE_TOOLS = ("create_category", "rename_category", "update_about_description")


def _drafts(c):
    """Every tool once, confirm=false, with ordinary arguments."""
    calls = {
        "vote_post": lambda m: _fn(m.vote_post)(SLUG, "p1"),
        "pin_post": lambda m: _fn(m.pin_post)(SLUG, "p1"),
        "create_category": lambda m: _fn(m.create_category)(SLUG, "Questions", "newest-cm", 4),
        "rename_category": lambda m: _fn(m.rename_category)(SLUG, "lb1", "New name"),
        "delete_category": lambda m: _fn(m.delete_category)(SLUG, "lb1", "lb2"),
        "update_about_description": lambda m: _fn(m.update_about_description)(SLUG, ABOUT),
        "set_post_comments": lambda m: _fn(m.set_post_comments)(SLUG, "p1", False),
        "pin_comment": lambda m: _fn(m.pin_comment)(SLUG, "p1", "c1"),
        "follow_post": lambda m: _fn(m.follow_post)(SLUG, "p1"),
    }
    return {name: _with_client(c, call) for name, call in calls.items()}


def test_the_tools_exist_and_are_flagged():
    import catknows.mcp_server as m
    for name in TOOLS:
        assert hasattr(m, name), f"{name} is not registered with CATKNOWS_ALLOW_WRITE=1"
        assert name in m._DESTRUCTIVE, f"{name} must be flagged destructive"
        assert name not in m._READ_ONLY, f"{name} writes"
    assert m._AUDIT_ARGS["category_id"] == "category_id", m._AUDIT_ARGS


def test_drafts_send_nothing_and_say_what_would_happen():
    c = _client()
    drafts = _drafts(c)
    assert c.http.calls == [], f"a draft must not touch Skool at all: {c.http.calls}"
    for name, out in drafts.items():
        assert out["status"].startswith("DRAFT"), (name, out)
        assert "confirm=true" in out["next_step"], (name, out)

    v = drafts["vote_post"]["would_vote"]
    assert (v["post_id"], v["old"], v["new"], v["community"]) == ("p1", "", "up", SLUG), v
    assert drafts["pin_post"]["would_pin"]["post_id"] == "p1", drafts["pin_post"]
    made = drafts["create_category"]["would_create"]
    assert (made["display_name"], made["default_sort"], made["post_write_role"]) == (
        "Questions", "newest-cm", 4), made
    renamed = drafts["rename_category"]["would_rename"]
    assert (renamed["category_id"], renamed["display_name"]) == ("lb1", "New name"), renamed
    gone = drafts["delete_category"]["would_delete"]
    assert (gone["category_id"], gone["migrate_to"]) == ("lb1", "lb2"), gone
    assert "cannot be undone" in drafts["delete_category"]["not_verified"]
    assert drafts["update_about_description"]["would_update"]["description"] == ABOUT
    assert "would_disable_comments" in drafts["set_post_comments"], drafts["set_post_comments"]
    pc = drafts["pin_comment"]["would_pin_comment"]
    assert (pc["post_id"], pc["comment_id"]) == ("p1", "c1"), pc
    assert drafts["follow_post"]["would_follow"]["post_id"] == "p1", drafts["follow_post"]


def test_drafts_name_the_opposite_direction_too():
    c = _client()
    calls = [
        ("would_unvote", lambda m: _fn(m.vote_post)(SLUG, "p1", "up", "")),
        ("would_unpin", lambda m: _fn(m.pin_post)(SLUG, "p1", False)),
        ("would_enable_comments", lambda m: _fn(m.set_post_comments)(SLUG, "p1", True)),
        ("would_unpin_comment", lambda m: _fn(m.pin_comment)(SLUG, "p1", "c1", False)),
        ("would_unfollow", lambda m: _fn(m.follow_post)(SLUG, "p1", False)),
    ]
    for key, call in calls:
        out = _with_client(c, call)
        assert key in out, (key, out)
    assert c.http.calls == [], c.http.calls


def test_drafts_carry_a_fixed_note_on_what_is_not_verified():
    """A warning only in a docstring is not seen when the human confirms: it sits in the draft."""
    drafts = _drafts(_client())
    for name in ("vote_post", "pin_post", "create_category", "rename_category",
                 "update_about_description", "set_post_comments", "pin_comment"):
        note = drafts[name].get("not_verified")
        assert isinstance(note, str) and len(note) > 40, (name, drafts[name])
    assert "display_name" in drafts["rename_category"]["not_verified"]
    assert "400" in drafts["pin_post"]["not_verified"]
    assert "old" in drafts["vote_post"]["not_verified"]
    assert "[v2]" in drafts["update_about_description"]["not_verified"]
    assert "10" in drafts["create_category"]["not_verified"]
    # 10 is the category form's limit and the 409 is read from the page code:
    # neither is stated as what Skool's server does.
    assert "form" in drafts["create_category"]["not_verified"]
    assert "Skool allows" not in drafts["create_category"]["not_verified"]
    assert "read from the code" in drafts["set_post_comments"]["not_verified"]
    assert "one" in drafts["pin_comment"]["not_verified"]


def test_the_rename_note_says_what_skools_own_form_really_sends():
    """Page code: the one category form calls updateLabel with name, sort AND write
    role every time. A name-only update is not what the web client does, so the
    draft must not claim Skool's client sends only the fields it changes."""
    c = _client()
    out = _with_client(c, lambda m: _fn(m.rename_category)(SLUG, "lb1", "New name"))
    note = out["not_verified"]
    for wrong in ("also sends only", "only the fields", "only the changed", "only what it changes"):
        assert wrong not in note, f"the draft still says: {wrong!r}: {note}"
    assert "always sends name, sort and write role together" in note, note
    assert "name-only update is not something the web client does" in note, note
    assert "not checked live" in note and "not read back" in note, note
    assert out["would_rename"]["display_name"] == "New name", out  # the draft shows the new name
    assert c.http.calls == [], c.http.calls


def test_the_note_is_there_in_both_directions():
    c = _client()
    calls = [
        lambda m: _fn(m.vote_post)(SLUG, "p1", "up", ""),
        lambda m: _fn(m.pin_post)(SLUG, "p1", False),
        lambda m: _fn(m.set_post_comments)(SLUG, "p1", True),
        lambda m: _fn(m.pin_comment)(SLUG, "p1", "c1", False),
    ]
    for call in calls:
        out = _with_client(c, call)
        assert isinstance(out.get("not_verified"), str) and out["not_verified"], out
    assert c.http.calls == [], c.http.calls


def test_only_the_drafts_with_public_text_ask_for_the_humanizing_pass():
    drafts = _drafts(_client())
    for name, out in drafts.items():
        assert ("before_you_show_this" in out) == (name in PROSE_TOOLS), name


def test_a_draft_already_refuses_what_the_client_would_refuse():
    c = _client()
    refused = [
        lambda m: _fn(m.vote_post)(SLUG, "p1", "", "down"),
        lambda m: _fn(m.vote_post)(SLUG, "p1", "up", "up"),
        lambda m: _fn(m.vote_post)(SLUG, "", "", "up"),
        lambda m: _fn(m.pin_post)(SLUG, " "),
        lambda m: _fn(m.vote_post)(SLUG, "..", "", "up"),
        lambda m: _fn(m.pin_post)(SLUG, ".", False),
        lambda m: _fn(m.set_post_comments)(SLUG, "...", True),
        lambda m: _fn(m.pin_comment)(SLUG, "p1", "..", True),
        lambda m: _fn(m.follow_post)(SLUG, "..", False),
        lambda m: _fn(m.rename_category)(SLUG, "...", "New"),
        lambda m: _fn(m.create_category)(SLUG, "x" * 31, "newest", 4),
        lambda m: _fn(m.create_category)(SLUG, "x", "best-1y", 4),
        lambda m: _fn(m.create_category)(SLUG, "x", "newest", 2),
        lambda m: _fn(m.create_category)(SLUG, " ", "newest", 4),
        lambda m: _fn(m.rename_category)(SLUG, "", "New"),
        lambda m: _fn(m.rename_category)(SLUG, "lb1", "x" * 31),
        lambda m: _fn(m.update_about_description)(SLUG, "  "),
        lambda m: _fn(m.set_post_comments)(SLUG, "", True),
        lambda m: _fn(m.pin_comment)(SLUG, "p1", ""),
        lambda m: _fn(m.follow_post)(SLUG, ""),
    ]
    for call in refused:
        _refused(lambda: _with_client(c, call))
    assert c.http.calls == [], c.http.calls


# -- tools: confirm=true ------------------------------------------------------------

def test_confirmed_tools_send_exactly_the_documented_calls():
    cases = [
        (lambda m: _fn(m.vote_post)(SLUG, "p1", confirm=True), "voted",
         [("PUT", "/posts/p1/vote", {"old": "", "new": "up"})]),
        (lambda m: _fn(m.vote_post)(SLUG, "p1", "up", "", confirm=True), "vote_taken_back",
         [("PUT", "/posts/p1/vote", {"old": "up", "new": ""})]),
        (lambda m: _fn(m.pin_post)(SLUG, "p1", confirm=True), "pinned",
         [("POST", "/posts/p1/pin", {})]),
        (lambda m: _fn(m.pin_post)(SLUG, "p1", False, confirm=True), "unpinned",
         [("POST", "/posts/p1/unpin", {})]),
        (lambda m: _fn(m.create_category)(SLUG, "Questions", "newest-cm", 4, confirm=True),
         "created",
         [("POST", "/labels", {"group_id": GID, "metadata": {
             "display_name": "Questions", "default_sort": "newest-cm", "post_write_role": 4}})]),
        (lambda m: _fn(m.rename_category)(SLUG, "lb1", "New name", confirm=True), "renamed",
         [("PUT", "/labels/lb1", {"display_name": "New name"})]),
        (lambda m: _fn(m.delete_category)(SLUG, "lb1", confirm=True), "deleted",
         [("DELETE", "/labels/lb1", None)]),
        (lambda m: _fn(m.delete_category)(SLUG, "lb1", "lb2", confirm=True), "deleted",
         [("DELETE", "/labels/lb1?migrate=lb2", None)]),
        (lambda m: _fn(m.update_about_description)(SLUG, ABOUT, confirm=True), "updated",
         [("POST", f"/groups/{GID}/update-landing-page-description", {"description": ABOUT})]),
        (lambda m: _fn(m.set_post_comments)(SLUG, "p1", False, confirm=True), "comments_disabled",
         [("POST", "/posts/p1/disable-comments", None)]),
        (lambda m: _fn(m.set_post_comments)(SLUG, "p1", True, confirm=True), "comments_enabled",
         [("POST", "/posts/p1/enable-comments", None)]),
        (lambda m: _fn(m.pin_comment)(SLUG, "p1", "c1", confirm=True), "comment_pinned",
         [("POST", "/posts/p1/pin-comment?comment=c1", None)]),
        (lambda m: _fn(m.pin_comment)(SLUG, "p1", "c1", False, confirm=True), "comment_unpinned",
         [("POST", "/posts/p1/unpin-comment?comment=c1", None)]),
        (lambda m: _fn(m.follow_post)(SLUG, "p1", confirm=True), "followed",
         [("POST", "/posts/p1/follow", None)]),
        (lambda m: _fn(m.follow_post)(SLUG, "p1", False, confirm=True), "unfollowed",
         [("POST", "/posts/p1/unfollow", None)]),
    ]
    for call, status, writes in cases:
        c = _client()
        out = _with_client(c, call)
        assert out["status"] == status, (status, out)
        assert c.http.writes == writes, (status, c.http.writes)
        # the archived guard reads the group once, before the write (for the
        # three that already know the slug, the client does it itself)
        assert c.http.calls[0] == ("GET", f"/groups/{SLUG}", None), (status, c.http.calls)


def test_the_answer_goes_through_the_scrub():
    answer = {"id": "lb-9", "metadata": {"display_name": "Questions"}, "apiKeys": ["SECRET-KEY"]}
    for call in (
        lambda m: _fn(m.create_category)(SLUG, "Questions", "newest", 4, confirm=True),
        lambda m: _fn(m.rename_category)(SLUG, "lb1", "Questions", confirm=True),
        lambda m: _fn(m.update_about_description)(SLUG, ABOUT, confirm=True),
        lambda m: _fn(m.pin_post)(SLUG, "p1", confirm=True),
    ):
        c = _client(_FakeHTTP(answer=answer))
        out = _with_client(c, call)
        assert "SECRET-KEY" not in json.dumps(out), "the answer must go through _safe_raw"
    c = _client(_FakeHTTP(answer=answer))
    out = _with_client(c, lambda m: _fn(m.create_category)(
        SLUG, "Questions", "newest", 4, confirm=True))
    assert out["category"]["id"] == "lb-9", "the caller needs the new id"


def test_writes_into_an_archived_community_stop_at_the_tool():
    calls = (
        lambda m: _fn(m.vote_post)(SLUG, "p1", confirm=True),
        lambda m: _fn(m.pin_post)(SLUG, "p1", confirm=True),
        lambda m: _fn(m.create_category)(SLUG, "x", "newest", 4, confirm=True),
        lambda m: _fn(m.rename_category)(SLUG, "lb1", "x", confirm=True),
        lambda m: _fn(m.delete_category)(SLUG, "lb1", confirm=True),
        lambda m: _fn(m.update_about_description)(SLUG, ABOUT, confirm=True),
        lambda m: _fn(m.set_post_comments)(SLUG, "p1", False, confirm=True),
        lambda m: _fn(m.pin_comment)(SLUG, "p1", "c1", confirm=True),
        lambda m: _fn(m.follow_post)(SLUG, "p1", confirm=True),
    )
    for call in calls:
        c = _client(_FakeHTTP(group={"id": GID, "archived": True}))
        _refused(lambda: _with_client(c, call))
        assert c.http.writes == [], c.http.writes


def test_a_security_question_is_not_turned_into_a_result_by_the_tools():
    calls = (
        lambda m: _fn(m.vote_post)(SLUG, "p1", confirm=True),
        lambda m: _fn(m.pin_post)(SLUG, "p1", confirm=True),
        lambda m: _fn(m.create_category)(SLUG, "x", "newest", 4, confirm=True),
        lambda m: _fn(m.rename_category)(SLUG, "lb1", "x", confirm=True),
        lambda m: _fn(m.delete_category)(SLUG, "lb1", confirm=True),
        lambda m: _fn(m.update_about_description)(SLUG, ABOUT, confirm=True),
        lambda m: _fn(m.set_post_comments)(SLUG, "p1", False, confirm=True),
        lambda m: _fn(m.pin_comment)(SLUG, "p1", "c1", confirm=True),
        lambda m: _fn(m.follow_post)(SLUG, "p1", confirm=True),
    )
    for call in calls:
        c = _client(_ChallengedHTTP())
        try:
            _with_client(c, call)
        except SkoolSecurityChallenge:
            pass
        else:
            raise AssertionError("the tool swallowed Skool's security question")
        assert len(c.http.writes) == 1, f"a second try was sent: {c.http.writes}"


# -- audit: the line names the target, never the text --------------------------------

def test_every_post_action_leaves_a_line_without_content():
    secret_name = "Private inner circle"
    secret_about = "Doors open for the inner circle only."
    prev = os.environ.get("CATKNOWS_AUDIT_LOG")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CATKNOWS_AUDIT_LOG"] = str(Path(tmp) / "w.jsonl")
            c = _real_client()
            _with_client(c, lambda m: _fn(m.vote_post)(SLUG, "p-1", confirm=True))
            _with_client(c, lambda m: _fn(m.pin_post)(SLUG, "p-1", confirm=True))
            _with_client(c, lambda m: _fn(m.create_category)(
                SLUG, secret_name, "newest", 4, confirm=True))
            _with_client(c, lambda m: _fn(m.rename_category)(
                SLUG, "lb-1", secret_name, confirm=True))
            _with_client(c, lambda m: _fn(m.update_about_description)(
                SLUG, secret_about, confirm=True))
            _with_client(c, lambda m: _fn(m.set_post_comments)(SLUG, "p-1", False, confirm=True))
            _with_client(c, lambda m: _fn(m.pin_comment)(SLUG, "p-1", "c-1", confirm=True))
            _with_client(c, lambda m: _fn(m.follow_post)(SLUG, "p-1", confirm=True))

            lines = [json.loads(x) for x in audit.log_path().read_text("utf-8").splitlines()]
            vote, pin, made, renamed, about, switch, pinned, follow = lines
            assert (vote["tool"], vote["method"], vote["path"]) == (
                "vote_post", "PUT", "/posts/p-1/vote"), vote
            assert vote["post_id"] == "p-1" and vote["community"] == SLUG, vote
            assert (pin["tool"], pin["path"], pin["post_id"]) == (
                "pin_post", "/posts/p-1/pin", "p-1"), pin
            assert (made["tool"], made["method"], made["path"]) == (
                "create_category", "POST", "/labels"), made
            assert made["community"] == SLUG and made["created_id"] == "lb-9", made
            assert (renamed["tool"], renamed["method"], renamed["path"]) == (
                "rename_category", "PUT", "/labels/lb-1"), renamed
            assert renamed["category_id"] == "lb-1", renamed
            assert (about["tool"], about["method"]) == ("update_about_description", "POST"), about
            assert about["path"] == f"/groups/{GID}/update-landing-page-description", about
            assert switch["path"] == "/posts/p-1/disable-comments", switch
            assert switch["post_id"] == "p-1", switch
            assert pinned["path"] == "/posts/p-1/pin-comment?comment=c-1", pinned
            assert pinned["post_id"] == "p-1" and pinned["comment_id"] == "c-1", pinned
            assert follow["path"] == "/posts/p-1/follow" and follow["status"] == 200, follow
            blob = "\n".join(json.dumps(x) for x in lines)
            assert secret_name not in blob and secret_about not in blob, \
                "a category name or the About text reached the audit log (no content, ever)"
    finally:
        if prev is None:
            os.environ.pop("CATKNOWS_AUDIT_LOG", None)
        else:
            os.environ["CATKNOWS_AUDIT_LOG"] = prev


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all green")
