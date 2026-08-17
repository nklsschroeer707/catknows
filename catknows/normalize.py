"""Turn raw Skool JSON into flat, tidy records.

The raw API responses are nested and inconsistent (nanosecond timestamps here,
ISO strings there; camelCase on Next.js, snake_case on api2). This module is
the single place that knows those quirks, so the rest of your pipeline — and
whatever you do in Obsidian — sees clean, flat dicts with real datetimes.

Field mappings and the quirks below are ported 1:1 from the production
extractors, so a normalized record here matches what the graph app stored.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone


def _ns_or_iso_to_dt(raw) -> datetime | None:
    """Skool timestamps come as nanoseconds (int) OR ISO strings. Handle both.

    Values above 1e15 are treated as nanoseconds (Skool uses ns for member
    last-offline and some created-at fields); ISO strings are parsed as-is.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)) and raw > 1e15:
        return datetime.fromtimestamp(raw / 1_000_000_000, tz=timezone.utc)
    if isinstance(raw, (int, float)) and raw > 1e9:  # plausible unix seconds
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _micros_to_dt(raw) -> datetime | None:
    """api2 comment timestamps: microseconds as a number, or an ISO string.

    The cursor field (``last``) is microseconds, but ``post.created_at`` comes
    back as ISO ("2026-08-08T16:59:44.650886Z") — falling through to None on
    strings is what made every normalized comment show ``created_at: null``.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)) and raw > 0:
        return datetime.fromtimestamp(raw / 1_000_000, tz=timezone.utc)
    return _ns_or_iso_to_dt(raw)


def _sp_data(meta: dict) -> dict:
    """Parse the ``spData`` JSON-string blob (points/level/role live here)."""
    raw = meta.get("spData")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


# Skool's internal role names -> the nomenclature docs/API.md uses. Anything
# unlisted passes through unchanged rather than being swallowed into "member".
# Used by EVERY path that reports a role: the member list and self/groups spell
# the same thing differently, and a caller filtering for admins must not have to
# know which endpoint a record came from.
_ROLE_NAMES = {"group-admin": "admin", "group-moderator": "moderator"}


def _role(raw: str) -> str:
    return _ROLE_NAMES.get(raw, raw)


def member(user: dict) -> dict:
    """Flatten one raw ``pageProps.users[]`` object into a member record."""
    m = user.get("member") or {}
    meta = user.get("metadata") or {}
    sp = _sp_data(meta)

    last_offline = meta.get("lastOffline")  # nanoseconds
    return {
        "skool_id": user.get("id", ""),
        "name": user.get("name", ""),
        "email": user.get("email", ""),
        "first_name": user.get("firstName", ""),
        "last_name": user.get("lastName", ""),
        "role": _role(m.get("role", "")),
        "group_id": m.get("groupId", ""),
        # Points live in metadata.spData.pts (a JSON string), NOT in
        # member.metadata.points (which is absent → always read as 0).
        "points": int(sp.get("pts", 0) or 0),
        "level": int(sp.get("lv", 0) or 0),
        "is_online": bool(meta.get("online")),
        "last_active": _ns_or_iso_to_dt(last_offline),
        "picture_url": meta.get("pictureProfile") or meta.get("picture", ""),
        "bio": meta.get("bio", ""),
    }


def chat_message(msg: dict) -> dict:
    """Flatten one raw DM message (docs/API.md §1.6).

    Everything worth having sits in ``metadata``. Note the sender field is
    ``src`` (with ``dst`` for the recipient) — not ``user``/``user_id``, which
    do not exist here; reading those gives every message the same blank author
    and makes a whole conversation look like it came from one person.

    ``attachments`` is a comma-joined id string like a post's, and
    ``attachments_data`` (a JSON string) carries the file name, type and a
    ready-to-use ``read_url`` per attachment — surfaced as ``files``.
    """
    md = msg.get("metadata") or {}
    files = []
    for f in maybe_json(md.get("attachments_data")) or []:
        if not isinstance(f, dict):
            continue
        fm = f.get("metadata") or {}
        files.append({
            "id": f.get("id", ""),
            "file_name": fm.get("file_name", ""),
            "content_type": fm.get("content_type", ""),
            "url": fm.get("read_url") or fm.get("image_md_url", ""),
        })
    return {
        "message_id": msg.get("id", ""),
        "from_user_id": md.get("src", ""),
        "to_user_id": md.get("dst", ""),
        "content": md.get("content", ""),
        "attachments": md.get("attachments", ""),
        "files": files,
        "created_at": _ns_or_iso_to_dt(msg.get("created_at")),
    }


def post(tree: dict) -> dict:
    """Flatten one raw ``pageProps.postTrees[]`` tree into a post record."""
    p = tree.get("post") or {}
    u = p.get("user") or {}
    meta = p.get("metadata") or {}
    skool_id = p.get("id", "")
    root_id = p.get("rootId", "")
    # Native poll: metadata.pollData (camelCase feed) / poll_data (api2) is a
    # JSON string with per-option vote counts. Absent on non-poll posts.
    poll = None
    poll_raw = meta.get("pollData") or meta.get("poll_data") or ""
    if poll_raw:
        try:
            poll = [{"option": e.get("text", ""), "votes": int(e.get("count", 0) or 0)}
                    for e in json.loads(poll_raw).get("entries", [])]
        except (ValueError, AttributeError):
            poll = None
    return {
        "skool_id": skool_id,
        "name": p.get("name", ""),  # URL slug, NOT the display title
        # The human-readable title and body live in metadata; without them a
        # post record is just a slug and an AI can't summarize anything.
        "title": meta.get("title", ""),
        "content": meta.get("content", ""),
        "post_type": p.get("postType", ""),
        "group_id": p.get("groupId", ""),
        "user_id": p.get("userId", ""),
        "user_name": u.get("name", ""),
        "root_id": root_id,
        "is_toplevel": root_id == "" or root_id == skool_id,
        "comments": int(meta.get("comments", 0) or 0),
        "upvotes": int(meta.get("upvotes", 0) or 0),
        "poll": poll,
        "created_at": _ns_or_iso_to_dt(p.get("createdAt")),
    }


def comments(merged: dict) -> list[dict]:
    """Flatten a merged comment tree into a list of comment records.

    Walks recursively, setting ``parent_id`` so replies point at their parent
    comment (or the post itself for first-layer comments). api2 uses
    snake_case, so we read snake_case first, camelCase as fallback.
    """
    out: list[dict] = []
    children = ((merged.get("post_tree") or {}).get("children")) or []
    _walk_comments(children, "", out)
    return out


def _walk_comments(nodes: list, explicit_parent: str, out: list) -> None:
    for node in nodes:
        p = node.get("post") or {}
        skool_id = p.get("id")
        if not skool_id:
            continue
        u = p.get("user") or {}
        meta = p.get("metadata") or {}
        root_id = p.get("root_id") or p.get("rootId") or ""
        parent_id = explicit_parent or root_id
        out.append({
            "skool_id": skool_id,
            # Body is in metadata.content; `name` is a slug-ish title that is
            # None on real comments (only the self-check's fixtures set it).
            "text": meta.get("content") or p.get("name") or "",
            "user_id": p.get("user_id") or p.get("userId") or "",
            "user_name": u.get("name", ""),
            "root_id": root_id,
            "parent_id": parent_id,
            "upvotes": int(meta.get("upvotes", 0) or 0),
            "created_at": _micros_to_dt(p.get("created_at") or p.get("createdAt")),
        })
        sub = node.get("children") or []
        if sub:
            _walk_comments(sub, skool_id, out)


def like(user: dict, post_skool_id: str) -> dict:
    """Flatten one raw ``users[]`` liker into a like record (mixed case)."""
    return {
        "post_skool_id": post_skool_id,
        "user_skool_id": str(user.get("id", "")),
        "user_name": user.get("name", ""),
        "user_first_name": user.get("first_name") or user.get("firstName", ""),
        "user_last_name": user.get("last_name") or user.get("lastName", ""),
    }


def profile(user: dict) -> dict:
    """Flatten a raw ``pageProps.currentUser`` into a profile record."""
    pd = user.get("profileData") or {}
    m = pd.get("member") or {}
    return {
        "skool_id": user.get("id", ""),
        "name": user.get("name", ""),
        "email": user.get("email", ""),
        "first_name": user.get("firstName", ""),
        "last_name": user.get("lastName", ""),
        "role": _role(m.get("role", "")),
        "total_posts": int(pd.get("totalPosts", 0) or 0),
        "total_followers": int(pd.get("totalFollowers", 0) or 0),
        "total_following": int(pd.get("totalFollowing", 0) or 0),
        "total_contributions": int(pd.get("totalContributions", 0) or 0),
        "total_groups": int(pd.get("totalGroups", 0) or 0),
        "groups_member_of": _slugs(pd.get("groupsMemberOf")),
        "groups_created_by_user": _slugs(pd.get("groupsCreatedByUser")),
    }


def _slugs(groups) -> list[str]:
    """Normalize a groups array to a plain list of slugs.

    Skool sends either ["slug1", ...] or [{"name": "slug1", ...}, ...].
    """
    if not isinstance(groups, list):
        return []
    out = []
    for g in groups:
        if isinstance(g, str) and g:
            out.append(g)
        elif isinstance(g, dict) and g.get("name"):
            out.append(g["name"])
    return out


# Skool's five pricing models, in the order of the Preise settings UI.
# Older groups may carry no membershipModel at all -> passed through as None.
_MEMBERSHIP_MODELS = {1: "free", 2: "paid", 3: "freemium", 4: "tiers", 5: "one_time"}


def maybe_json(val):
    """Skool nests JSON as *strings* inside metadata (displayPrice, owner,
    event location, ...). Parse when it is one, pass through otherwise."""
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, ValueError):
            return val
    return val


def about_pricing(data: dict) -> dict:
    """Membership model, price and tiers from a raw about payload.

    Paid (2) and tiers (4) groups carry ``displayPrice`` on
    ``currentGroup.metadata`` — for tiers it is the entry price. Freemium
    groups (3) never do — joining is free, and the tiers in
    ``pageProps.groupMembershipProducts`` only carry billing-product *ids*,
    not amounts, so ``price`` stays None for them.
    """
    # ponytail: tier amounts would need an api2 billing-products call; add if needed
    pp = data.get("pageProps") or {}
    md = (pp.get("currentGroup") or {}).get("metadata") or {}
    price = maybe_json(md.get("displayPrice"))
    model = md.get("membershipModel")
    tiers = [
        {
            "tier": p.get("tier"),
            "benefits": maybe_json((p.get("metadata") or {}).get("benefits")),
        }
        for p in pp.get("groupMembershipProducts") or []
    ]
    return {
        "membership_model": _MEMBERSHIP_MODELS.get(model, model),
        "price": price,
        "tiers": tiers,
    }


def _either(d: dict, camel: str, snake: str, default=None):
    """Read a field that Skool spells differently per endpoint.

    ``self/groups`` returns snake_case (``display_name``, ``total_members``)
    while other endpoints return camelCase. Reading only one spelling silently
    yields None/0 — which is exactly how blank names and 0-member counts
    shipped. Accept both; the self-check fixtures cover both spellings.
    """
    v = d.get(camel)
    return d.get(snake, default) if v is None else v


def my_community(group: dict, my_user_id: str = "") -> dict:
    """Flatten one ``self/groups`` entry into "a community I'm in" record.

    Two fields do NOT live where they look like they do. ``group.role`` does
    not exist in this payload at all, and ``group.created_at`` is when the
    *community* was founded, not when you joined it. Both really live in
    ``metadata.member`` — a JSON *string* holding your membership row
    (``role``, ``created_at``). Reading the outer fields is how every
    community came back as "member", joined on its founding date.

    Owners are a special case: Skool lists them as ``group-admin`` in
    ``metadata.member``, so ``metadata.owner`` (a user UUID, sometimes a JSON
    string) against your own id still wins and yields ``role: "owner"``.

    ``archived`` matters to a caller because an archived community stays
    readable while posting, commenting and liking are off. Skool states it
    twice, outer ``archived`` (bool) and ``metadata.archived`` (1), and this
    record used to pass on neither, so an agent could not tell a read-only
    community from a live one until a write failed.
    """
    meta = group.get("metadata") or {}
    owner = maybe_json(meta.get("owner"))
    if isinstance(owner, dict):  # some payloads nest the id
        owner = owner.get("id") or owner.get("userId") or ""
    member = maybe_json(meta.get("member"))
    if not isinstance(member, dict):
        member = {}
    role = _role(member.get("role") or group.get("role") or "")
    is_owner = bool(my_user_id and owner and owner == my_user_id)
    if is_owner:
        role = "owner"
    # No membership row means we do NOT know when you joined. The outer
    # created_at is the community's founding date, and returning it here is
    # what made every community look like a day-one join for two weeks. A
    # wrong date that looks real is worse than a gap: say None and flag it,
    # so a caller can tell "joined at founding" from "never read the row".
    joined = _either(member, "createdAt", "created_at")
    out = {
        "slug": group.get("name", ""),  # `name` is the slug, display_name is the label
        "display_name": _either(meta, "displayName", "display_name", ""),
        "role": role or "member",
        "total_members": int(_either(meta, "totalMembers", "total_members", 0) or 0),
        "is_owner": is_owner,
        "joined_at": _ns_or_iso_to_dt(joined),
        # Either spelling counts, and either alone is enough: measured live on
        # two archived communities, both carried both. `read_only` is NOT
        # derived here — no payload states it, and archived is the only
        # read-only case there is evidence for.
        "archived": bool(group.get("archived") or meta.get("archived")),
        "color": meta.get("color", ""),
        "logo_url": _either(meta, "logoUrl", "logo_url", ""),
    }
    if not member:
        # Same silence covers `role`: without the row it defaults to "member",
        # which reads as a fact but is only a guess. Owner is derived
        # separately from metadata.owner, so it stays trustworthy.
        out["incomplete"] = "metadata.member missing: joined_at unknown" + (
            "" if is_owner else ", role is a default and not measured"
        )
    return out


# -- secret scrubbing (docs/API.md §6.6) --------------------------------------
# Skool's page/api payloads carry credential-class fields that must NEVER leave
# a raw=True tool result or land in a log: cleartext Zapier keys, Stripe payout
# ids, billing/payment details, affiliate secrets, and Skool's own client-side
# platform API keys. `scrub` walks any payload and drops every such key by name,
# camelCase AND snake_case, at any depth. It is the single guard every raw path
# routes through — add a name here, not a pop() at each call site.

SECRET_KEYS = frozenset({
    # per-group / per-account secrets (§6.6)
    "apiKeys", "api_keys",
    "paymentCard", "payment_card",
    "billingEmail", "billing_email",
    "billingCycleEnd", "billing_cycle_end",
    "payoutAccountId", "payout_account_id",
    "aflCode", "afl_code",
    "aflSetup", "afl_setup", "aflSetupStatus", "afl_setup_status",
    "aflUser", "afl_user", "aflPctAtJoin", "afl_pct_at_join",
    # the whole self-context blob (self.* holds all of the above, plus more)
    "self",
    # Skool's own client-side platform keys leaked via pageProps.env
    "env",
})


def scrub(obj):
    """Recursively strip SECRET_KEYS from a payload (in place where possible).

    Returns the same object with credential-class keys removed at every depth,
    so a raw=True tool result can be handed to an AI or written to disk without
    leaking account secrets. Idempotent; safe on any JSON-shaped structure.
    """
    if isinstance(obj, dict):
        for key in list(obj.keys()):
            if key in SECRET_KEYS:
                del obj[key]
            else:
                scrub(obj[key])
    elif isinstance(obj, list):
        for item in obj:
            scrub(item)
    return obj


if __name__ == "__main__":
    # ponytail: self-check for the quirky bits — ns timestamps, mixed-case
    # likes, recursive comment parenting. Runs with `python -m catknows.normalize`.
    ns = 1_700_000_000_000_000_000
    assert _ns_or_iso_to_dt(ns).year == 2023, "nanosecond parse failed"
    assert _ns_or_iso_to_dt("2024-01-15T10:00:00Z").year == 2024, "ISO parse failed"

    ap = about_pricing({"pageProps": {
        "currentGroup": {"name": "g", "metadata": {"membershipModel": 3}},
        "groupMembershipProducts": [{"tier": "premium",
                                     "metadata": {"benefits": '["a"]'}}],
    }})
    assert ap["membership_model"] == "freemium", "freemium model map failed"
    assert ap["price"] is None, "freemium must have no join price"
    assert ap["tiers"][0]["benefits"] == ["a"], "tier benefits parse failed"
    paid = about_pricing({"pageProps": {"currentGroup": {"metadata": {
        "membershipModel": 2, "displayPrice": '{"amount":900}'}}}})
    assert paid["price"]["amount"] == 900, "paid displayPrice parse failed"

    m = member({"id": "u1", "name": "Ann", "member": {"role": "admin"},
                "metadata": {"online": True, "lastOffline": ns,
                             "spData": '{"pts":42,"lv":3}'}})
    assert m["points"] == 42 and m["level"] == 3, "spData points/level parse failed"
    assert m["is_online"] and m["role"] == "admin"
    # Skool's own names must map the same way on EVERY path — the member list
    # said "group-admin" while list_my_communities said "admin", so a caller
    # filtering for admins got different answers per tool. Verified live
    # against a community holding both roles.
    mod = member({"id": "u2", "member": {"role": "group-moderator"}})
    assert mod["role"] == "moderator", mod
    adm = member({"id": "u3", "member": {"role": "group-admin"}})
    assert adm["role"] == "admin", adm
    prof = profile({"id": "u4", "profileData": {"member": {"role": "group-moderator"}}})
    assert prof["role"] == "moderator", prof

    # DM message: body, sender and attachments all sit in metadata, and the
    # attachment id is a comma-joined string like a post's — not an array.
    dm = chat_message({
        "id": "m1", "created_at": "2026-08-14T16:27:49.772220Z",
        "metadata": {
            "content": "hi", "src": "u9", "dst": "u8", "attachments": "f1",
            "attachments_data": '[{"id":"f1","metadata":{"file_name":"a.pdf",'
                                '"content_type":"application/pdf",'
                                '"read_url":"https://x/a.pdf"}}]',
        },
    })
    # src/dst, NOT user/user_id — reading the wrong field blanks every author
    # and a whole conversation looks like one person wrote it.
    assert dm["from_user_id"] == "u9" and dm["to_user_id"] == "u8", dm
    assert dm["content"] == "hi" and dm["attachments"] == "f1", dm
    assert dm["files"][0]["file_name"] == "a.pdf", dm
    assert dm["files"][0]["url"] == "https://x/a.pdf", dm
    assert dm["created_at"].year == 2026, dm
    bare = chat_message({"id": "m2"})
    assert bare["content"] == "" and bare["files"] == [], bare

    # Poll post: vote counts ride in metadata.pollData as a JSON string.
    pp = post({"post": {"id": "p9", "metadata": {
        "title": "t", "poll": "poll1",
        "pollData": '{"entries":[{"text":"Ja","count":3},{"text":"Nein","count":1}],'
                    '"option_index":-1}'}}})
    assert pp["poll"] == [{"option": "Ja", "votes": 3},
                          {"option": "Nein", "votes": 1}], pp
    assert post({"post": {"id": "p0", "metadata": {}}})["poll"] is None

    lk = like({"id": 7, "name": "Bo", "firstName": "Bo"}, "p1")  # camelCase liker
    assert lk["user_first_name"] == "Bo" and lk["user_skool_id"] == "7"

    # Shaped like real api2 comments: body in metadata.content (`name` is
    # None there), created_at an ISO string — both were silently dropped once.
    tree = {"post_tree": {"children": [
        {"post": {"id": "c1", "name": None, "user_id": "u1", "root_id": "p1",
                  "created_at": "2026-08-08T16:59:44.650886Z",
                  "metadata": {"content": "hi"}},
         "children": [{"post": {"id": "c2", "name": None, "user_id": "u2",
                                "created_at": 1_754_000_000_000_000,
                                "metadata": {"content": "re"}}}]}]}}
    cs = comments(tree)
    assert len(cs) == 2, "recursive walk missed nested comment"
    assert cs[0]["parent_id"] == "p1", "first-layer parent should be the post"
    assert cs[1]["parent_id"] == "c1", "nested reply should point at its parent comment"
    assert cs[0]["text"] == "hi" and cs[1]["text"] == "re", "comment body must come from metadata.content"
    assert cs[0]["created_at"].year == 2026, "ISO comment timestamp must parse"
    assert cs[1]["created_at"].year == 2025, "microsecond comment timestamp must parse"

    # scrub: credential-class keys must vanish at every depth, both cases,
    # while ordinary data survives. This is the raw=True safety net.
    payload = {
        "pageProps": {
            "self": {"metadata": {"payoutAccountId": "acct_X"}},
            "env": {"STRIPE_PUBLISHABLE_KEY": "pk_live_X"},
            "currentGroup": {"metadata": {
                "displayName": "keep me", "billingEmail": "a@b.c",
                "apiKeys": ["zap_secret"], "paymentCard": {"last4": "4242"},
            }},
            "users": [{"name": "keep", "metadata": {
                "afl_code": "x", "payout_account_id": "acct_Y", "bio": "keep"}}],
        }
    }
    scrub(payload)
    pp = payload["pageProps"]
    assert "self" not in pp and "env" not in pp, "self/env must be dropped whole"
    md = pp["currentGroup"]["metadata"]
    assert md == {"displayName": "keep me"}, f"only non-secret group keys survive, got {md}"
    umd = pp["users"][0]["metadata"]
    assert umd == {"bio": "keep"}, f"user afl/payout must be dropped, got {umd}"
    import json as _json
    blob = _json.dumps(payload)
    for leak in ("acct_", "pk_live_", "zap_secret", "billingEmail", "4242"):
        assert leak not in blob, f"scrub leaked {leak!r}"

    # my_community: shaped like a REAL self/groups entry — verified against live
    # api2 output, which is snake_case throughout. The fixtures used to be
    # camelCase, so the tests passed while production shipped blank names and
    # 0 members. `name` is the slug, the label lives in metadata.display_name,
    # and an owned group carries NO role at all (the owner id has to resolve it).
    # Even an owned group ships metadata.member (live: catnose lists the owner
    # as group-admin, joined on the founding day). Leaving it out of the fixture
    # is what let the founding-date fallback pass for correct.
    owned = my_community(
        {"name": "catnose", "created_at": "2025-01-02T03:04:05.000000Z",
         "metadata": {"display_name": "catknows.", "owner": "me-uuid",
                      "total_members": 29, "logo_url": "https://x/y.png",
                      "member": '{"role": "group-admin",'
                                ' "created_at": "2025-01-02T03:04:05.000000Z"}'}},
        my_user_id="me-uuid",
    )
    assert owned["slug"] == "catnose", owned
    assert owned["display_name"] == "catknows.", owned
    assert owned["role"] == "owner", f"owner has no role field, must be derived: {owned}"
    assert owned["is_owner"] is True and owned["total_members"] == 29, owned
    assert owned["joined_at"] and owned["joined_at"].year == 2025, owned
    assert owned["logo_url"] == "https://x/y.png", owned
    # Other endpoints spell the same fields camelCase — both must read.
    camel = my_community(
        {"name": "catnose", "createdAt": "2025-01-02T03:04:05.000000Z",
         "metadata": {"displayName": "catknows.", "owner": "me-uuid",
                      "totalMembers": 29, "logoUrl": "https://x/y.png",
                      "member": '{"role": "group-admin",'
                                ' "createdAt": "2025-01-02T03:04:05.000000Z"}'}},
        my_user_id="me-uuid",
    )
    assert camel == owned, f"camelCase and snake_case must normalize alike: {camel}"
    # Same group seen by somebody else: not the owner, keeps its stated role.
    other = my_community(
        {"name": "catnose", "role": "member",
         "metadata": {"displayName": "catknows.", "owner": "me-uuid"}},
        my_user_id="someone-else",
    )
    assert other["role"] == "member" and other["is_owner"] is False, other
    # Skool sometimes ships metadata.owner as a JSON string / nested object.
    nested = my_community(
        {"name": "g", "metadata": {"owner": '{"id": "me-uuid"}'}}, my_user_id="me-uuid"
    )
    assert nested["role"] == "owner", f"JSON-string owner must still resolve: {nested}"
    # No id to compare against: never claim ownership, fall back to member.
    unknown = my_community({"name": "g", "metadata": {"owner": "me-uuid"}})
    assert unknown["role"] == "member" and unknown["is_owner"] is False, unknown
    # metadata.member is a JSON *string* — role and join date both live in it.
    # Outer created_at is the community's founding date and must lose to it.
    admin = my_community(
        {"name": "some-group", "created_at": "2019-12-04T00:00:00.000000Z",
         "metadata": {"display_name": "Some Group", "owner": "someone-else",
                      "member": '{"role": "group-admin",'
                                ' "created_at": "2024-10-14T18:51:43.707306Z"}'}},
        my_user_id="me-uuid",
    )
    assert admin["role"] == "admin", f"group-admin must map to admin: {admin}"
    assert admin["joined_at"].year == 2024, f"joined_at must not be the founding date: {admin}"
    # Owner beats metadata.member, which lists owners as group-admin too.
    owner_admin = my_community(
        {"name": "catnose",
         "metadata": {"owner": "me-uuid", "member": '{"role": "group-admin"}'}},
        my_user_id="me-uuid",
    )
    assert owner_admin["role"] == "owner", f"owner must outrank group-admin: {owner_admin}"
    moderator = my_community(
        {"name": "g", "metadata": {"member": '{"role": "group-moderator"}'}}
    )
    assert moderator["role"] == "moderator", moderator
    # Unknown role values pass through instead of being swallowed into member.
    odd = my_community({"name": "g", "metadata": {"member": '{"role": "wat"}'}})
    assert odd["role"] == "wat", odd
    # Missing/unparseable metadata.member must NOT quietly become the founding
    # date. Dan Schaad measured `skoolers` as 2019 (its founding) while having
    # joined in 2024 — that is this branch, and it used to assert 2019 as if it
    # were right. joined_at stays None and the record says why.
    for broken in ({}, {"member": "not json"}, {"member": None}):
        fb = my_community({"name": "g", "created_at": "2019-12-04T00:00:00.000000Z",
                           "metadata": broken})
        assert fb["joined_at"] is None, f"founding date must never pose as a join: {fb}"
        assert "incomplete" in fb, f"a missing membership row must be stated: {fb}"
        assert "role is a default" in fb["incomplete"], fb
        assert fb["role"] == "member", fb  # still a usable default, just flagged
    # Owners keep a trustworthy role even without the row (metadata.owner wins),
    # so the flag must not claim their role is guessed.
    own_norow = my_community({"name": "g", "metadata": {"owner": "me-uuid"}},
                             my_user_id="me-uuid")
    assert own_norow["role"] == "owner" and own_norow["joined_at"] is None, own_norow
    assert "role is a default" not in own_norow["incomplete"], own_norow
    # A real row never sets the flag, even when join == founding date (day-one
    # members exist: `hoomans` founded 19:57, joined 20:41 the same day).
    dayone = my_community(
        {"name": "hoomans", "created_at": "2024-11-25T19:57:07.561720Z",
         "metadata": {"member": '{"role": "member",'
                                ' "created_at": "2024-11-25T20:41:16.864147Z"}'}}
    )
    assert "incomplete" not in dayone, f"a present row is complete: {dayone}"
    assert dayone["joined_at"].day == 25 and dayone["joined_at"].hour == 20, dayone
    # archived: an archived community stays readable but every write is off, and
    # this record used to pass on neither of Skool's two statements of it. Shape
    # copied from a live self/groups entry (vrooms-3264, 17.08.): outer bool AND
    # metadata 1, both present together. Either alone must still count — nothing
    # measured says Skool always sends both, and guessing that it does is how a
    # made-up fixture would freeze the bug in place.
    arch = my_community(
        {"name": "vrooms-3264", "archived": True, "public": True,
         "created_at": "2025-06-30T05:15:16.746011Z",
         "metadata": {"display_name": "vRooms - Real Connections", "archived": 1,
                      "total_members": 11, "color": "#E9597F",
                      "member": '{"role": "member",'
                                ' "created_at": "2025-07-01T05:15:16.746011Z"}'}}
    )
    assert arch["archived"] is True, f"both spellings present must read as True: {arch}"
    assert arch["role"] == "member" and arch["total_members"] == 11, arch
    outer_only = my_community({"name": "g", "archived": True, "metadata": {}})
    assert outer_only["archived"] is True, f"outer bool alone counts: {outer_only}"
    meta_only = my_community({"name": "g", "metadata": {"archived": 1}})
    assert meta_only["archived"] is True, f"metadata 1 alone counts: {meta_only}"
    # A live community must not come back archived, in any spelling of "no".
    for quiet in ({"name": "g", "metadata": {}},
                  {"name": "g", "archived": False, "metadata": {"archived": 0}}):
        assert my_community(quiet)["archived"] is False, quiet
    print("normalize self-check OK")
