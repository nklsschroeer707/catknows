"""Self-check: member data an admin can see must not leave through the tools.

Three leaks, one test file:

1. ``raw=True`` returned every admin-side field Skool attaches to a member
   (join survey, email answer, attribution, request location, who approved,
   request count, device flag). ``normalize.scrub`` now drops those too, in
   every depth and both spellings, next to the credential-class SECRET_KEYS.
2. ``normalize.member`` and the vault note carried an ``email`` field. Skool
   sends it empty today; the field is gone so a day it is filled changes nothing.
3. ``list_join_requests`` printed the applicant's email answer in clear text.
   It is masked (``a***@example.com``) unless the operator passes
   ``reveal_email=True``. Every other place the join queue is shown masks too.

Run: python test_privacy_scrub.py    (no network, no pytest)
"""

import inspect
import json
import os
import tempfile
import time
from pathlib import Path

os.environ["CATKNOWS_ALLOW_WRITE"] = "1"  # review_join_request only exists with this

import catknows.mcp_server as m
from catknows import normalize, vault
from catknows.client import MemberList

SLUG = "some-group"

# The eight admin fields of the brief, both spellings (`survey` has one).
ADMIN_KEYS = {
    "survey",
    "searchAnswer", "search_answer",
    "attrComp", "attr_comp",
    "attrSrcComp", "attr_src_comp",
    "requestLocation", "request_location",
    "approvedBy", "approved_by",
    "numRequests", "num_requests",
    "onPc", "on_pc",
}


def _fn(tool):
    return tool.fn if hasattr(tool, "fn") else tool


def _with_client(c, call):
    old = m._get_client
    m._get_client = lambda: c
    try:
        return call(m)
    finally:
        m._get_client = old


def _keys(obj):
    """Every dict key at every depth."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _keys(item)


SURVEY = json.dumps({"survey": [
    {"question": "Why join?", "type": "text", "answer": "to learn"},
    {"question": "Your email", "type": "email", "answer": "anna@example.com"},
]})


def _camel_member():
    """members.json style (Next.js, camelCase)."""
    return {
        "id": "u1", "name": "anna", "firstName": "Anna", "lastName": "Test",
        "email": "anna.account@example.com",
        "member": {
            "id": "m1", "role": "member", "groupId": "g1",
            "searchAnswer": "anna@example.com", "approvedBy": "u-admin",
            "metadata": {
                "survey": SURVEY, "attrComp": "ad", "attrSrcComp": "discovery",
                "requestLocation": "berlin (germany)", "numRequests": 2, "onPc": 1,
                "highRiskScore": 0,
            },
        },
        "metadata": {"bio": "hello", "onPc": 1, "attrComp": "ad",
                     "spData": '{"pts":5,"lv":1}'},
    }


def _snake_member():
    """api2 style (snake_case)."""
    return {
        "id": "u2", "name": "bob", "first_name": "Bob", "last_name": "Test",
        "member": {
            "id": "m2", "role": "member", "group_id": "g1",
            "search_answer": "bob@example.com", "approved_by": "u-admin",
            "metadata": {
                "survey": SURVEY, "attr_comp": "ad", "attr_src_comp": "discovery",
                "request_location": "paris (france)", "num_requests": 3, "on_pc": 1,
                "high_risk_score": 0,
            },
        },
        "metadata": {"bio": "hi", "on_pc": 1},
    }


def _raw_payload():
    return {
        "pageProps": {
            "users": [_camel_member(), _snake_member()],
            # a list inside a list, and a field at the top of an object
            "extra": [[{"metadata": {"survey": SURVEY, "keep": "me"}}]],
            "survey": SURVEY,
            "totalPages": 1,
        },
    }


# -- step 1: scrub -------------------------------------------------------------

def test_the_admin_keys_are_one_named_set_next_to_the_secret_keys():
    assert normalize.PRIVATE_KEYS == frozenset(ADMIN_KEYS), \
        sorted(normalize.PRIVATE_KEYS ^ ADMIN_KEYS)
    assert normalize.PRIVATE_KEYS.isdisjoint(normalize.SECRET_KEYS)


def test_scrub_drops_every_admin_field_at_every_depth_in_both_spellings():
    payload = normalize.scrub(_raw_payload())
    left = ADMIN_KEYS & set(_keys(payload))
    assert not left, f"still there after scrub: {sorted(left)}"
    blob = json.dumps(payload)
    for value in ("berlin (germany)", "paris (france)", "anna@example.com",
                  "bob@example.com", "u-admin", "to learn"):
        assert value not in blob, f"value of a dropped field survived: {value!r}"


def test_scrub_keeps_everything_else():
    pp = normalize.scrub(_raw_payload())["pageProps"]
    camel, snake = pp["users"]
    assert camel["name"] == "anna" and camel["firstName"] == "Anna", camel
    assert camel["member"]["role"] == "member" and camel["member"]["id"] == "m1", camel
    assert camel["member"]["metadata"] == {"highRiskScore": 0}, camel["member"]
    assert camel["metadata"] == {"bio": "hello", "spData": '{"pts":5,"lv":1}'}, camel
    assert snake["member"]["metadata"] == {"high_risk_score": 0}, snake["member"]
    assert snake["metadata"] == {"bio": "hi"}, snake
    assert pp["extra"] == [[{"metadata": {"keep": "me"}}]], pp["extra"]
    assert pp["totalPages"] == 1


def test_secret_keys_still_work_together_with_the_admin_keys():
    payload = {"self": {"metadata": {"payoutAccountId": "acct_X"}},
               "env": {"KEY": "pk_live_X"},
               "member": {"billingEmail": "a@b.c", "apiKeys": ["zap"],
                          "searchAnswer": "anna@example.com", "role": "member"}}
    out = normalize.scrub(payload)
    assert out == {"member": {"role": "member"}}, out


def test_safe_raw_is_the_door_and_it_uses_scrub():
    out = m._safe_raw(_raw_payload())
    assert not ADMIN_KEYS & set(_keys(out)), sorted(ADMIN_KEYS & set(_keys(out)))


class _RawClient:
    """Hands back raw member objects, as Skool does."""

    def members(self, slug, *, limit=None, **kw):
        return MemberList([_camel_member(), _snake_member()])

    def profile(self, user_name, slug):
        return {**_camel_member(), "profileData": {"member": _camel_member()["member"]}}


def test_list_members_raw_leaks_none_of_the_fields():
    out = _with_client(_RawClient(),
                       lambda mod: _fn(mod.list_members)(SLUG, raw=True))
    assert [u["name"] for u in out[:2]] == ["anna", "bob"], out
    assert not ADMIN_KEYS & set(_keys(out)), sorted(ADMIN_KEYS & set(_keys(out)))


def test_get_member_profile_raw_leaks_none_of_the_fields():
    out = _with_client(_RawClient(),
                       lambda mod: _fn(mod.get_member_profile)("anna", SLUG, raw=True))
    assert out["name"] == "anna", out
    assert not ADMIN_KEYS & set(_keys(out)), sorted(ADMIN_KEYS & set(_keys(out)))


def test_a_normalized_join_request_keeps_its_own_num_requests():
    """Normalization reads the raw object BEFORE any scrub, and its output never
    goes through scrub (that would eat the flat `num_requests`)."""
    raw = {"id": "u1", "name": "anna", "member": {
        "id": "m1", "searchAnswer": "anna@example.com",
        "metadata": {"numRequests": 4, "requestLocation": "berlin (germany)",
                     "attrSrcComp": "discovery", "survey": SURVEY}}}
    r = normalize.join_request(raw)
    assert r["num_requests"] == 4, r
    assert r["location"] == "berlin (germany)" and r["source"] == "discovery", r
    assert [a["question"] for a in r["answers"]] == ["Why join?", "Your email"], r
    # The tool path must keep it too.
    out = _with_client(_queue_client(), lambda mod: _fn(mod.list_join_requests)(SLUG))
    assert out["requests"][0]["num_requests"] == 1, out["requests"][0]


# -- step 2: no email on members ----------------------------------------------

def test_member_record_has_no_email_even_if_the_raw_object_carries_one():
    rec = normalize.member(_camel_member())
    assert "email" not in rec, rec
    assert "anna.account@example.com" not in json.dumps(rec, default=str), rec
    assert rec["name"] == "anna" and rec["points"] == 5, rec


def test_search_member_has_no_email_either():
    rec = normalize.search_member({"id": "m1", "role": "member", "groupId": "g1",
                                   "user": {"id": "u1", "name": "anna",
                                            "email": "anna@example.com"}})
    assert "email" not in rec and rec["member_id"] == "m1", rec


def test_the_vault_note_has_no_email():
    with tempfile.TemporaryDirectory() as d:
        # a record from normalize.member, and an old-style record that still has one
        for rec in (normalize.member(_camel_member()),
                    {"skool_id": "u9", "name": "Old Person", "email": "old@example.com",
                     "role": "member"}):
            path = vault.write_member(Path(d), "some-group", rec)
            text = path.read_text(encoding="utf-8")
            assert "email" not in text.lower(), text
            assert "@example.com" not in text, text
        assert "name: anna" in (Path(d) / "some-group" / "members" / "anna.md"
                                ).read_text(encoding="utf-8")


# -- step 3: masked email in the join queue -----------------------------------

def test_mask_email():
    assert normalize.mask_email("anna@example.com") == "a***@example.com"
    assert normalize.mask_email("Bob.Smith+x@mail.example.org") == "B***@mail.example.org"
    assert normalize.mask_email("a@example.com") == "a***@example.com"


def test_mask_email_leaves_text_without_an_email_alone():
    for text in ("", "hello world", "Ping @anna and @bob", "meet at 5 @ noon",
                 "see example.com", "I build MCP tools"):
        assert normalize.mask_email(text) == text, text


def test_mask_email_masks_every_email_in_a_text():
    got = normalize.mask_email("write anna@example.com or bob@example.org, thanks.")
    assert got == "write a***@example.com or b***@example.org, thanks.", got
    # a sentence ending right after the address keeps its full stop
    assert normalize.mask_email("mail me: anna@example.com.") == "mail me: a***@example.com."


def test_mask_email_passes_non_text_through():
    assert normalize.mask_email(None) is None
    assert normalize.mask_email(3) == 3


def test_mask_email_hides_the_whole_local_part_whatever_it_is_made_of():
    """RFC 5322: a local part may hold any "atext" character and dots, or be quoted."""
    for address, masked in (
        ("a/b@x.com", "a***@x.com"),
        ("anna!work@example.com", "a***@example.com"),
        ("anna#1$2%3&4'5*6+7/8=9?0^_`{|}~-x@example.com", "a***@example.com"),
        ("zoë.müller@example.com", "z***@example.com"),
        ('"anna smith"@example.com', '"***@example.com'),
        ('"anna@home"@example.com', '"***@example.com'),
        ('"an\\"na"@example.com', '"***@example.com'),
    ):
        assert normalize.mask_email(address) == masked, (address, normalize.mask_email(address))
        got = normalize.mask_email(f"write to {address}, thanks.")
        assert got == f"write to {masked}, thanks.", (address, got)


def test_mask_email_finds_a_quoted_local_part_inside_a_json_text():
    """An error body is JSON: the quotes of a quoted local part arrive escaped."""
    body = json.dumps({"error": 'no user "anna smith"@example.com', "to": "bob/x@example.org"})
    got = normalize.mask_email(body)
    assert "anna" not in got and "smith" not in got and "bob" not in got, got
    assert "***@example.com" in got and '"to": "b***@example.org"' in got, got


def test_mask_email_leaves_quotes_and_signs_without_an_address_alone():
    for text in ('she said "hello" @ noon', '"quoted" text', 'a "b" c@d', "50% off + more!",
                 '{"name": "@anna.smith", "x": "y"}', '{"": "@anna.smith"}',
                 "path/to/file.txt", "x = y?z : w"):
        assert normalize.mask_email(text) == text, (text, normalize.mask_email(text))


def test_mask_email_takes_linear_time_on_a_long_text():
    """It used to take seconds on a long text without an address (every start
    position scanned to the end). The limit is wide so a slow machine passes."""
    n = 200_000
    cases = {
        "letters only": "a" * n,
        "dots and hyphens": ("anna.b-c.d--e." * (n // 14 + 1))[:n],
        "signs of a local part": ("a!b#c/d=e?" * (n // 10))[:n],
        "quotes": '"' * n,
        "open quotes": ('"a\\' * (n // 3 + 1))[:n],
        "at signs without a domain": "a@" * (n // 2),
        "a domain without a dot": "a@" + "b-" * (n // 2),
    }
    for label, text in cases.items():
        start = time.perf_counter()
        got = normalize.mask_email(text)
        took = time.perf_counter() - start
        assert took < 2.0, f"{label}: {took:.2f} s for {len(text)} characters"
        assert got == text, f"{label}: a text without an address was changed"
    # and a long text WITH addresses still gets every one of them
    many = "x anna@example.com y " * 5000
    start = time.perf_counter()
    got = normalize.mask_email(many)
    assert time.perf_counter() - start < 2.0
    assert "anna" not in got and got.count("a***@example.com") == 5000


def _pending(i, name, answers, search_answer):
    return {"id": f"u{i}", "name": name, "firstName": name.title(), "lastName": "X",
            "metadata": {"bio": f"bio {i}"},
            "member": {"id": f"m{i}", "role": "pending", "userId": f"u{i}",
                       "searchAnswer": search_answer,
                       "metadata": {"requestedAt": 1786008427315217000,
                                    "requestLocation": "berlin (germany)",
                                    "highRiskScore": i % 2, "numRequests": 1,
                                    "attrSrcComp": "discovery_browse_group_link",
                                    "survey": json.dumps({"survey": answers})}}}


QUEUE = [
    _pending(1, "anna", [
        {"question": "Why join?", "type": "text", "answer": "I build MCP tools"},
        {"question": "Email?", "type": "email", "answer": "anna@example.com"},
        {"question": "Reach you?", "type": "text",
         "answer": "also anna.work@example.org if you like"},
    ], "anna@example.com"),
    _pending(2, "bob", [
        {"question": "Why join?", "type": "text", "answer": "cats"},
        {"question": "Email?", "type": "email", "answer": "bob@example.com"},
    ], "bob@example.com"),
]


class _FakeHTTP:
    def __init__(self, queue):
        self.queue, self.writes = queue, []

    def get_next(self, q, slug):
        page = int(q.split("&p=")[1]) if "&p=" in q else 1
        users = self.queue if page == 1 else []
        return {"pageProps": {"users": users, "total": len(self.queue),
                              "totalPages": 1, "page": page}}

    def post_api2(self, path, body):
        self.writes.append((path, body))
        return {}

    put_api2 = delete_api2 = post_api2


def _queue_client():
    from catknows.client import SkoolClient
    c = SkoolClient.__new__(SkoolClient)
    c.http = _FakeHTTP(list(QUEUE))
    return c


CLEAR = ("anna@example.com", "bob@example.com", "anna.work@example.org")


def test_a_join_request_is_masked_by_default():
    r = normalize.join_request(QUEUE[0])
    assert r["email_answer"] == "a***@example.com", r
    assert r["answers"][1] == {"question": "Email?", "type": "email",
                               "answer": "a***@example.com"}, r["answers"]
    assert r["answers"][2]["answer"] == "also a***@example.org if you like", r["answers"]
    assert r["answers"][0]["answer"] == "I build MCP tools", "other answers stay as they are"
    blob = json.dumps(r, default=str)
    assert not [c for c in CLEAR if c in blob], blob


def test_an_email_typed_answer_is_masked_even_when_it_has_no_email_shape():
    odd = _pending(3, "cy", [{"question": "Email?", "type": "email",
                              "answer": "cy at example dot com"}], "")
    r = normalize.join_request(odd)
    assert r["answers"][0]["answer"] == "c***", r["answers"]
    assert r["email_answer"] == "", "an empty answer stays empty"


def test_reveal_email_gives_the_clear_text_back():
    r = normalize.join_request(QUEUE[0], reveal_email=True)
    assert r["email_answer"] == "anna@example.com", r
    assert r["answers"][1]["answer"] == "anna@example.com", r["answers"]
    assert r["answers"][2]["answer"] == "also anna.work@example.org if you like", r["answers"]


def test_reveal_email_is_keyword_only_and_off_by_default():
    p = inspect.signature(normalize.join_request).parameters["reveal_email"]
    assert p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is False, p
    q = inspect.signature(_fn(m.list_join_requests)).parameters["reveal_email"]
    assert q.default is False, q
    assert "reveal_email" in (_fn(m.list_join_requests).__doc__ or ""), \
        "the tool description must explain the switch"


def test_list_join_requests_masks_by_default_everywhere_in_the_output():
    out = _with_client(_queue_client(), lambda mod: _fn(mod.list_join_requests)(SLUG))
    assert out["count"] == 2, out
    blob = json.dumps(out)
    assert not [c for c in CLEAR if c in blob], blob
    assert out["requests"][0]["email_answer"] == "a***@example.com", out["requests"][0]


def test_list_join_requests_reveals_on_request():
    out = _with_client(_queue_client(),
                       lambda mod: _fn(mod.list_join_requests)(SLUG, reveal_email=True))
    assert out["requests"][0]["email_answer"] == "anna@example.com", out["requests"][0]
    assert out["requests"][1]["answers"][1]["answer"] == "bob@example.com", out["requests"][1]


def test_searching_by_name_works_masked_and_revealed():
    for reveal in (False, True):
        out = _with_client(_queue_client(), lambda mod: _fn(mod.list_join_requests)(
            SLUG, query="BOB", reveal_email=reveal))
        assert [r["name"] for r in out["requests"]] == ["bob"], (reveal, out)
        out = _with_client(_queue_client(), lambda mod: _fn(mod.list_join_requests)(
            SLUG, query="mcp", reveal_email=reveal))
        assert [r["name"] for r in out["requests"]] == ["anna"], (reveal, out)


def test_the_search_runs_over_the_printed_form():
    """An address only matches when it is printed: masked output hides it from
    the search too, so a masked call cannot be used to probe for an applicant."""
    masked = _with_client(_queue_client(), lambda mod: _fn(mod.list_join_requests)(
        SLUG, query="bob@example.com"))
    assert masked["count"] == 0, masked
    shown = _with_client(_queue_client(), lambda mod: _fn(mod.list_join_requests)(
        SLUG, query="bob@example.com", reveal_email=True))
    assert [r["name"] for r in shown["requests"]] == ["bob"], shown


def test_the_join_queue_helper_is_masked():
    rows = _with_client(_queue_client(), lambda mod: mod._join_queue(SLUG))
    assert [r["name"] for r in rows] == ["anna", "bob"], rows
    blob = json.dumps(rows)
    assert not [c for c in CLEAR if c in blob], blob


def test_the_review_draft_is_masked_and_still_names_the_person():
    c = _queue_client()
    out = _with_client(c, lambda mod: _fn(mod.review_join_request)(SLUG, "m1", "approve"))
    assert "DRAFT" in out["status"] and c.http.writes == [], out
    assert out["would_approve"]["name"] == "anna", out
    blob = json.dumps(out)
    assert not [x for x in CLEAR if x in blob], blob
    assert "a***@example.com" in blob, "the draft still shows the masked address"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all green")
