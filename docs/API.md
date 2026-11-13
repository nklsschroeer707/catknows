# The Skool API (unofficial, reverse-engineered)

This is a complete reference for Skool's **private, undocumented** internal API
— the same endpoints skool.com's own frontend calls. There is no public Skool
API; this document was reverse-engineered from network traffic and is what
powers this repo's client.

Read this if you want to:
- understand exactly how the data is pulled, or
- hand it to an AI (Claude, Codex, …) and have it build a client in any language.

> **Not official. Not stable.** These endpoints can change without notice. You
> are driving your own logged-in session against your own communities. Respect
> Skool's Terms of Service and don't hammer their servers. See
> [LEGAL.md](../LEGAL.md).

---

## 0. The two things that make it work

Two facts explain 90% of the client's complexity.

### 0.1 Auth = your browser session cookies

Skool authenticates with an **`auth_token`** cookie (a JWT). It is `httpOnly`,
so page JavaScript and browser extensions **cannot read it** — you get it from
the browser's cookie jar (this repo uses Playwright) or by copying it out of
DevTools manually.

Two cookies matter:

| Cookie | What it is | Sent as |
|---|---|---|
| `auth_token` | Session JWT | `Cookie:` header, **and** `Authorization: Bearer <jwt>` on api2 calls |
| `aws-waf-token` | AWS-WAF challenge token | `Cookie:` header, **and** `x-aws-waf-token: <value>` header on api2 calls |

### 0.2 Skool sits behind AWS-WAF

Requests that don't look like a real browser get **HTTP 403** from AWS-WAF,
especially the api2 endpoints. To get through you must:

1. **Send a real Chrome TLS handshake.** AWS-WAF fingerprints TLS (JA3/JA4):
   api2 returns a CloudFront 403 ("Request blocked") for python `requests` /
   `httpx` / `curl` **even with a valid token and browser-identical headers**.
   Use `curl_cffi` with `impersonate="chrome"` (what catknows does), or tunnel
   the call through an actual browser. This check is on the *handshake*, so no
   header work can compensate for it.
2. Send a **realistic `User-Agent`** plus matching `sec-ch-ua*` headers (a Chrome
   UA with no `sec-ch-ua` is a tell — and so is a Firefox UA on a Chrome TLS
   handshake). Pick one browser profile per session and keep it consistent.
3. Send the **`x-aws-waf-token`** header (the bare `aws-waf-token` value) on
   api2 requests, not just the cookie.
4. Set `Origin: https://www.skool.com`, `Referer`, and the `sec-fetch-*` headers
   that a real cross-origin `fetch()` would carry.

The `aws-waf-token` is produced by JavaScript solving a WAF challenge — which is
exactly why a **real browser** (Playwright) is the robust way to obtain it. It
expires roughly every 24h; re-visit skool.com in the browser to refresh it.
(With a correct Chrome TLS handshake, api2 has been observed answering even
without the token — but keep sending it; how strictly it's enforced varies.)

### 0.3 The Next.js `buildId`

Skool's site is Next.js. Its data endpoints live under
`/_next/data/{buildId}/…`. The `buildId` **changes on every Skool deploy**, so
you discover it at runtime instead of hardcoding it:

```
GET https://www.skool.com/{community_slug}
```

The returned HTML contains `"buildId":"XXXX"` inside the `__NEXT_DATA__` script.
Extract it with the regex `"buildId":"([^"]+)"`. Cache it for your session.

**The id can also come from outside.** A program that already has a browser tab
open on Skool knows it (`window.__NEXT_DATA__.buildId`) and can hand it over, so
no HTML page has to be loaded at all. In catknows that is the attribute
`SkoolHTTP.build_id_provider` (default `None`), a callable
`provider(refresh: bool) -> str`: `provider(False)` returns the current value,
`provider(True)` fetches it anew because the caller just saw a 404. It is set on
the finished instance and read only when a request needs the id. With a provider
set:

- `build_id()` returns `provider(False)` and **never** loads the HTML page, not
  even as a fallback. This holds for calls without a community slug too.
- On a 404, `get_next` calls `provider(True)` **once**. The same value as before
  means a real 404 and the error is raised; a new value gets **one** new try, and
  a second 404 is final. Never more than one refresh and one retry per call.
- A provider that returns an empty text (or no text) is an error
  (`SkoolHTTPError`), not a reason to fall back to the HTML page. Exceptions the
  provider itself raises reach the caller unchanged.

Without a provider nothing changes: the HTML page, the cached id, the one
re-discovery after a 404.

---

## 1. Endpoint reference

Two backends, two request "shapes":

- **Next.js shape** → `www.skool.com/_next/data/{buildId}/…` — camelCase JSON,
  wrapped in `pageProps`. Header `x-nextjs-data: 1`.
- **api2 shape** → `api2.skool.com/…` — snake_case (mostly), no `pageProps`
  wrapper. Needs `Authorization: Bearer` + `x-aws-waf-token`.

Below, `{slug}` = community slug, `{buildId}` = discovered buildId,
`{gid}` = group UUID, `{postId}` = post id.

### 1.1 Members — Next.js shape

```
GET /_next/data/{buildId}/{slug}/-/members.json
      ?sortType=-memberlastoffline&group={slug}                   # page 1
GET /_next/data/{buildId}/{slug}/-/members.json
      ?sortType=-memberlastoffline&p={page}
      &online=&levels=&price=&courseIds=&monthly=false
      &annual=false&trials=false&group={slug}                     # page N
```

**`t=active` is redundant — and Skool flipped it server-side.** Measured
2026-08-12: 404 for everyone, owners included (four communities). Re-measured
2026-08-15: 200 for everyone, returning exactly the unfiltered list — it's
the default view, so catknows simply doesn't send it (it buys nothing and
has already changed behaviour once). An *unknown* `t=` value still 404s
(`{"notFound":true}`). The real filter grammar works for regular
members too: `t=cancelling|churned|banned` (lifecycle), `admin=true`,
`online=true` (status), `sortType=-memberapprovedat|-memberlastoffline|`
`-memberpoints`, plus billing flags (`monthly|annual|oneTime|trials|free`,
AND-combined), `tiers=a,b` (OR-combined) and `courseIds=a,b` (AND-combined —
per Dan Schaad's captures, 2026-08-14).

**Pagination can silently degrade:** per community/role/session Skool
re-serves page 1 for every later page instead of paging (measured 2026-08-15:
same fresh session walked a 592-member community fully as a regular member,
but a 5.5k community repeated page 1 from page 2 on). `SkoolClient.members()`
detects this and flags the result via `MemberList.incomplete`.

`sortType=-memberlastoffline` sorts DESC by "last offline", so currently-active
members come first. Response: `pageProps.users[]`, plus `pageProps.totalPages`
for pagination. Each user:

```jsonc
{
  "id": "user-uuid",
  "name": "handle",                    // the @handle URL slug
  "email": "a@b.com",                  // only visible to owners/admins; normalize.member() does not carry it
  "firstName": "Ada", "lastName": "Lovelace",
  "createdAt": "2024-...", "updatedAt": "...",
  "member": {                          // membership in THIS community
    "id": "member-uuid", "role": "admin", "groupId": "group-uuid",
    "metadata": { }                    // ⚠ NO usable "points" here — see spData below
  },
  "metadata": {
    "online": true,
    "lastOffline": 1700000000000000000, // ⚠ NANOSECONDS since epoch
    "pictureProfile": "https://...",
    "bio": "...",
    "spData": "{\"pts\":42,\"lv\":3,\"pcl\":0,\"pnl\":5,\"role\":4}"  // ⚠ points/level here!
  }
}
```

> **⚠ Points & level live in `metadata.spData`, a JSON *string*.** There is no
> usable `points` field on `member.metadata` (reading it always yields 0 — a
> common trap). Parse `spData` (`pts` = points, `lv` = level) to rank members by
> activity.

### 1.2 Posts — Next.js shape

```
GET /_next/data/{buildId}/{slug}.json?group={slug}          # page 1
GET /_next/data/{buildId}/{slug}.json?group={slug}&p={page} # page N
```

Sorted by last activity. Response: `pageProps.postTrees[]`.

> **⚠ Pagination quirks (unlike members):** the feed sends **no
> `totalPages`** — walk `p` upward until a page returns an empty
> `postTrees[]` (page 1 holds ~30 + pinned, later pages ~30). And **pinned
> posts appear twice on page 1**: once at the top, once again in their feed
> position — dedupe by `post.id`. Stopping on a missing `totalPages` (or
> skipping the dedupe) is how you end up with "32 posts, some duplicated"
> no matter how big the community is.

Each tree has a `post`:

```jsonc
{
  "post": {
    "id": "post-id", "name": "Post title / body",
    "postType": "generic", "groupId": "group-uuid",  // ← group UUID lives here!
    "userId": "author-id", "rootId": "",             // "" => top-level post
    "createdAt": "...",
    "metadata": { "comments": 12, "upvotes": 30 },
    "user": { "name": "author-handle", "metadata": {} }
  }
}
```

**Bootstrapping the group UUID:** you only know the slug, but api2 endpoints
need the group UUID. It "falls out" of the first posts response as
`post.groupId` — one posts fetch bootstraps it.

**Sort and filter server-side** (the feed's sort menu and category chips,
measured 2026-09-22 on an own community; every page carries the same params):

| Param | Values | Website label |
|---|---|---|
| `s` | *(absent)* = `newest-cm` | Default: last activity |
| `s` | `newest` | New |
| `s` | `best-1d`, `best-1w`, `best-1m`, `best-1y`, `best` | Top: day, week, month, year, all time |
| `fl` | `unr`, together with `s=newest` | Unread (posts the viewer hasn't opened) |
| `c` | label id | a category chip |

`pageProps` echoes them as `sortType`, `filter`, `category`, and carries
`total` for the filtered view (unread: 2 of 80). A post names its own category
in `labelId` and `metadata.labels`. `metadata.hasNewComments` is the viewer's
"New comment" badge. Implemented as `posts(sort=, unread_only=, category_id=)`,
`post_categories()`, `resolve_category()`; the `list_posts` tool takes the
category by name.

**Categories (Skool calls them labels)** come from `getGroupLabels` in Skool's own client:

```
GET https://api2.skool.com/groups/{gid}/labels          (no query)
→ {"labels": [{id, group_id, created_at, updated_at, metadata}]}
```

`metadata` (snake_case): `display_name`, `posts` (count), `color`,
`default_sort`, `post_write_role` and sometimes `description`. The feed page
carries the same list as `currentGroup.labels[]` (camelCase:
`metadata.displayName`, `metadata.posts`, `metadata.defaultSort`).
The endpoint is verified and reachable with `post_categories(via_api=True)`,
which falls back to the feed page only if it answers 401, 403 or 404, never
after 429. That fallback is one more request after a 401/403: a caller that
must stop on those must not use `via_api=True`. The
default, `post_categories()`, reads the feed page (one request, usually a cache
hit on the page `posts()` just read; no group id needed); `resolve_category()`,
`list_posts` and `search_community` use the default. Both ways give
`{id, name, posts}`.

### 1.2b One post's own page — attachments and video (Next.js shape)

```
GET /_next/data/{buildId}/{slug}/{postName}.json?group={slug}
```

`{postName}` is the post's **`name`** (URL slug), not its UUID. Response:
`pageProps.postTree` — the same tree shape as the feed, but the feed is
**lossy** and this page is not. Two things exist only here:

**1. File attachments.** The feed gives you `metadata.attachments` — a
comma-joined id string and nothing else. No name, no type, no URL. The
detail page adds the sibling `metadata.attachmentsData`, a JSON **string**:

```jsonc
"attachments": "6ab9cacf0d444f63a441c69e22320f6a",
"attachmentsData": "[{\"id\":\"6ab9…\",\"metadata\":{
    \"file_name\":\"report.pdf\", \"content_type\":\"application/pdf\",
    \"read_url\":\"https://assets.skool.com/f/{groupId}/{hash}\",
    \"src_content_length\":60446}}]"
```

> **⚠ Mixed casing in one field:** the outer key is **camelCase**
> (`attachmentsData`) while the fields *inside* the JSON string are
> **snake_case** (`file_name`, `read_url`) — the same shape DMs return under
> the fully snake_case `attachments_data` (§1.6). Assume one casing for both
> and every file silently disappears. `read_url` needs no auth header and
> answers 200 directly.

**2. Skool-hosted video.** `metadata.videoIds` (comma-joined) only names the
videos; the playable handles live in `postTree.videos[]`, which the feed
omits entirely:

```jsonc
{ "id": "video-uuid", "playbackId": "mux-playback-id",
  "playbackToken": "eyJ…",   // signed JWT, aud:"v"
  "thumbnailToken": "eyJ…",  // aud:"t"    "storyboardToken": "…",  // aud:"s"
  "expire": 1787059512, "duration": 179560, "aspectRatio": "16:9" }
```

Skool hosts video on **Mux**. `duration` is in **milliseconds**. The tokens
expire (`expire`), so read them fresh per request.

**Transcripts — Skool generates them, but never says so.** No field anywhere
in the Skool payload mentions captions. They are reachable through the
player's own chain:

```
GET https://stream.mux.com/{playbackId}.m3u8?token={playbackToken}
    → #EXT-X-MEDIA:TYPE=SUBTITLES … NAME="English CC" … URI="…subtitles.m3u8"
GET {that URI}          → one or more .vtt segment URLs
GET {each segment}      → WebVTT; concatenate for the full transcript
```

> **⚠ Referer-restricted:** the playback token carries a
> `playback_restriction_id`. Without `Referer: https://www.skool.com/` Mux
> answers **403 `E184-1`** even with a perfectly valid token.

Two honest limits, both measured: a video with **no audio track** carries no
subtitle track at all (its manifest has no `mp4a` codec and no `SUBTITLES`
line) — absence of captions, not an error. And **externally embedded** video
(`metadata.videoLinksData`: YouTube, Loom, Vimeo, Wistia) never reaches Mux;
each hoster has its own transcript path, none of them this one.

Long videos are split into several VTT segments, each restarting with a
`WEBVTT` header and renumbering its cues from 1 — the timestamps are already
absolute, so concatenate and ignore the cue numbers.

### 1.3 Profile (single member) — Next.js shape

```
GET /_next/data/{buildId}/@{userName}.json?g={slug}&group=@{userName}
```

`{userName}` is the `@handle` slug (the `name` field from members), **not** the
display name. Response: `pageProps.currentUser` (a single object, not an array;
fallback `pageProps.renderData.user`):

```jsonc
{
  "id": "user-id", "name": "handle", "firstName": "...", "lastName": "...",
  "profileData": {
    "totalPosts": 5, "totalFollowers": 100, "totalFollowing": 20,
    "totalContributions": 8, "totalGroups": 3,
    "dailyActivities": [ ... ],
    "groupsMemberOf":     [ { "name": "slug", "metadata": { "displayName": "..." } } ],
    "groupsCreatedByUser":[ ... ],
    "member": { "id": "...", "role": "...", "metadata": {} }
  }
}
```

### 1.3b A member's posts in one community — same route, paged

```
GET /_next/data/{buildId}/@{userName}.json?g={slug}&group=@{userName}[&p={page}]
```

With `g={slug}` the profile's `pageProps.postTrees[]` is scoped to that
community and pages with `&p=N` (10 per page; `total`, `page`, `perPage`=30
as sent, `hasMore` always `false` — walk until an empty page). It lists every
post the member takes part in: their own **and** others' posts they commented
on or are @mentioned in. Measured 2026-09-22 on cat-knows-1423 for @niklas:
77 entries (= the search hit count for "niklas"), 46 with `post.userId` = his
id, which is exactly his 46 posts in the full feed. Filter on `post.userId`
to get "posts X created". `members.json`/the feed have no author parameter.

Implemented as `SkoolClient.posts_by()` / `list_posts(author=…)`;
`SkoolClient.resolve_member()` turns a display name into the handle via §1.8.

### 1.3a Followers / following — Next.js shape

```
GET /_next/data/{buildId}/@{userName}.json?t=followers&group=@{userName}[&p=N]
GET /_next/data/{buildId}/@{userName}.json?t=following&group=@{userName}[&p=N]
```

The profile page's two list tabs (`skool.com/@handle?t=followers`). Visible
on every profile, no community needed. Payload sits under
`pageProps.renderData.user.profileData` (also mirrored in `currentUser`):
`tabName`, `page`, `itemsPerPage` (30), `totalFollowers`, `totalFollowing`, and

```jsonc
"follows": { "list": [ { "src": {user}, "dst": {user}, "following": false } ] },
"followStats": [ false, ... ]          // one per entry, same order
```

- **Direction:** on `followers`, `src` is the follower and `dst` the profile
  owner; on `following`, `src` is the owner and `dst` the followed user.
- **`following`** is the *viewer's* relation to that person (does the
  logged-in user follow them), i.e. the Follow button's state.
- **Pagination:** `&p=N`, 30 per page. Past the last page `follows.list` is
  `null`. Measured 2026-09-21 on @niklas: 110 followers = pages 30/30/30/20,
  page 5 `null`; 2 following on one page, page 2 `null`.
- **Re-served page 1 on big profiles:** same day on @sam (778,891 followers)
  pages 2 and 3 came back byte-identical to page 1 — the members-list
  degradation (§1.1) again. `follows()` dedupes and flags `incomplete`; only
  the first 30 of such a list are reachable this way.
- **Secrets:** the owner's side is their full user object. On your own profile
  that includes `email` and `metadata.payoutAccountId` (§6.6). Surface only the
  far side (`normalize.follow`); never return entries raw.

### 1.4 Comments — api2 shape (snake_case, cursor-paginated)

```
GET https://api2.skool.com/posts/{postId}/comments
      ?group-id={gid}&limit=25&created-gt={cursor}
```

Returns ~30 top-level comments per call. **Pagination is a forward cursor:** the
response's top-level `last` field is the `created_at` (microseconds) of the last
comment; pass it as `created-gt` to get the next page. Walk from `created-gt=0`
until a page is empty or `last` stops advancing. Nested replies arrive inline
under each node's `children`.

```jsonc
{
  "post_tree": {
    "children": [
      {
        "post": {
          "id": "comment-id", "name": "comment body",  // ⚠ body is in `name`
          "user_id": "...", "root_id": "post-id",       // ⚠ snake_case!
          "created_at": 1700000000000000,               // ⚠ MICROSECONDS
          "metadata": { "upvotes": 2 },
          "user": { "name": "handle" }
        },
        "children": [ /* nested replies, same shape */ ]
      }
    ]
  },
  "pinned_post_tree": { ... },
  "last": 1700000000000000     // cursor for the next page
}
```

**Parenting:** a first-layer comment's parent is the post itself (`root_id`); a
reply's parent is the top-level comment it hangs under. Skool shows two
levels only — writing a third gets the comment deleted (§5.8).

### 1.5 Likes (upvoters) — api2 shape (mixed case)

```
GET https://api2.skool.com/posts/{postId}/vote-users?limit=10&tab=upvotes&cursor=
```

```jsonc
{
  "tab": "upvotes",
  "users": [
    { "id": "user-id", "name": "handle",
      "first_name": "Ada", "last_name": "Lovelace" }  // or firstName/lastName — handle both
  ],
  "has_more": true,
  "cursor": "…",                // opaque; send it back for the next page
  "total_upvote_users": 25,
  "total_downvote_users": 2
}
```

The query order is `limit`, `tab`, `cursor`, and there is **no `group-id`**:
Skool's own client never sends it. It asks for the first page with
`limit=10&tab=upvotes&cursor=` (empty cursor) and for the next with `cursor` =
the `cursor` field of the answer, as long as `has_more` is true. The highest
`limit` Skool accepts is not measured here.

Implemented as `SkoolClient.likes_page()` (one page, raw object) and
`SkoolClient.likes()` (walks every page; stops when `has_more` is false, a page
is empty, the cursor is empty or did not change, or after `max_pages`, 50 by
default; a group id passed to it is accepted and ignored). The
`get_post_likes` tool returns **one** page — `likes`, `has_more`, `cursor`,
`total_upvotes`, `total_downvotes` — and the caller pages on with `cursor`.

Skool gives no "un-liked at" signal — to detect a rescinded like you diff the
current set against a previously stored set (this repo's graph app does; the
plain client just returns the current set).

### 1.6 Other read endpoints

| Purpose | Method | Shape |
|---|---|---|
| Community About | `GET /_next/data/{buildId}/{slug}/about.json?group={slug}` | Next.js |
| Calendar/events | `GET /_next/data/{buildId}/{slug}/calendar.json?group={slug}` (`&calDate={unixTs}` for other months) | Next.js (writing events: §5.11) |
| Classroom/courses (tiles only — modules need §7.1) | `GET /_next/data/{buildId}/{slug}/classroom.json?group={slug}` | Next.js |
| Single course/module | `GET /_next/data/{buildId}/{slug}/classroom/{courseId}.json?group={slug}&course={courseId}` (`&md={moduleId}` for a specific lesson) — or the api2 tree, §7.1 | Next.js |
| Pending join requests (admin) | `GET /_next/data/{buildId}/{slug}/-/pending.json?group={slug}` (send `X-KL-Ajax-Request: Ajax_Request`; see §6.5) | Next.js |
| Member segments | `GET /_next/data/{buildId}/{slug}/-/members.json?t={active\|cancelling\|churned}&group={slug}&p={page}` | Next.js |
| Your own settings | `GET /_next/data/{buildId}/settings.json?t=profile` | Next.js |
| Group info | `GET https://api2.skool.com/groups/{gid}` | api2 |
| Discovery (related communities) | `GET https://api2.skool.com/groups/{gid}/discovery` | api2 |
| Single post detail | `GET https://api2.skool.com/groups/{slug}/post-detail?post={postSlug}&with-comments=false` | api2 |
| Admin metrics (owner only) | `GET https://api2.skool.com/groups/{gid}/admin-metrics?range=30d&amt=monthly` | api2 |
| Your community's discovery rank (owner) | `GET https://api2.skool.com/groups/{gid}/discovery` | api2 |
| Your communities + roles | `GET https://api2.skool.com/self/groups?offset={n}&limit=30&prefs=false&members=true` | api2 |
| Your own user object | `GET https://api2.skool.com/self` | api2 |
| All your communities (compact) | `GET https://api2.skool.com/self/list-visibility-groups` | api2 |
| Your saved location | `GET https://api2.skool.com/self/location` | api2 |
| Chat channels | `GET https://api2.skool.com/self/chat-channels?offset={n}&limit=30&last=true&unread-only=false` (`unread-only=true` = only channels with unread messages) | api2 |
| Chat messages | `GET https://api2.skool.com/channels/{channelId}/messages?before=50` (older: `&after=0&msg={oldestId}`; no `limit`) | api2 |
| Categories (labels) | `GET https://api2.skool.com/groups/{gid}/labels` (§1.2; opt-in via `post_categories(via_api=True)`) | api2 |

> Live-verified against two logged-in accounts (Aug 2026). Notes: `/self/me`
> and `/self/profile` return **404** — only bare `/self` works. `/self/groups`
> pages 30 at a time (`has_more` tells you when to stop); `/self/list-visibility-groups`
> returns everything in one call as `groups_member_of[]` + `groups_created_by_user[]`.
> `self/chat-channels` rejects `limit=50` (`invalid limit`) — 30 works.
> With `unread-only=true` and nothing unread, Skool answers `{"channels": null}`
> (measured live, not an empty list); `chat_channels()` turns that into
> `"channels": []`.

**Chat messages** need `before` (or `after`), and the endpoint is unusually
easy to misread — three separate traps:

* `before` is **not** a timestamp and **not** a message id. It is a *count of
  messages back from newest*: `before=1` returns the latest one, `before=3` the
  latest three. It is **capped at 50** — larger values are refused with
  `invalid before: N`, which reads like a format error but is a limit.
* Getting past those 50 needs the **`msg={messageId}` cursor**: it returns a
  window *around* that message, with `before`/`after` counting outwards from
  it. `before=50&after=0&msg={oldestSeenId}` is the "one page further back"
  call. Without it you are stuck at the newest 51 no matter what you pass —
  `has_more_before` stays `true` and nothing you do to `before` advances.
  Windows include the boundary message, so consecutive pages overlap by one:
  dedupe by id.
* Anything non-numeric in `before`/`after` — an ISO date, a message id, `now` —
  is silently discarded and you get `either before or after must be set` even
  though you set it. `before=0` also counts as unset. That error means your
  **value** is wrong, not your parameter name.

There is **no `limit`** on this endpoint and Skool's own client sends none: live,
`?before=30` answers exactly like `?limit=30&before=30` (31 messages, the same
ids). `chat_messages()` sends `before` only and trims the result to `count`.

Response: `{messages[], has_more_before, has_more_after, channel}`, oldest
first. Per message, everything sits in `metadata`: `content`, the sender in
**`src`** and recipient in **`dst`** (there is no `user`/`user_id` field —
reading one blanks every author), `attachments` as a comma-joined id string,
and `attachments_data`, a JSON string with each file's `file_name`,
`content_type` and a ready `read_url`.

Implemented as `SkoolClient.chat_messages()` / the `read_dms` MCP tool, which
walks the cursor and dedupes for you. Live-verified: a channel reporting 51
messages actually held **248**, back to 2024-11-28.

**`self/groups`** is the "which communities am I in" endpoint — paginate
`offset` by 30 until a page is short. Each group object: `name` (the slug),
`role` (may be absent), and `metadata.{displayName, logoUrl, color,
totalMembers, owner}`. `metadata.owner` is a user UUID; compare it to your own
id (from `/self`) to tell **owner** apart from plain **member** when `role` is
missing.

Implemented as `SkoolClient.self_groups()` / the `list_my_communities` MCP tool
(`normalize.my_community` does the owner resolution). Watch the naming trap:
`name` is the **slug**, the human label is `metadata.displayName` — the same
trap that made `list_posts` return slugs instead of titles.

**Archived groups** carry `archived: true` at the top level **and**
`metadata.archived: 1`; live groups omit the field entirely in both places
(measured 2026-08-17 across 53 groups, 2 of them archived). Either spelling
alone is enough to treat a group as archived. `GET /groups/{slug}` returns the
same two fields, which is why the write path can check it without an extra
request. `client.group_id_for` raises when that request gets 401, 403 or 429,
and on the write path (`for_write=True`) on any error, so nothing else is
requested and nothing is written; only a read that failed some other way falls
back to the feed page (`post.groupId` of the first post). Archiving is **read-only, not hidden**: posts and members still list,
while creating posts, comments, polls and courses is off.

**`admin-metrics`** (owner-only) returns `total_members[]` and `active_members[]`
as 30-day time series (`{value, time}` points) plus `latest_active_members` and a
`daily_activities` range — the raw numbers behind a community's admin dashboard.
`range` only accepts **`30d`**; 7d/60d/90d/1y/12m and bare numbers all answer
`400 unsupported time range` (probed live 2026-08-14). `daily_activities` covers
a full year regardless, so the long view is in there even though the query isn't.

**`groups/{gid}/discovery`** (owner) is *your own* community's discovery
standing: `{is_showing, rank, category:{id,name}, category_rank, language_code,
boost_enabled, rank_updated_at}`. (This is different from §6's global board.)

**Member view of members.json**: when you fetch `members.json` as an **admin**,
each `users[]` entry additionally carries `email`, `timeZone`, and a full
`member.{role, approvedAt, lastOffline, userId, groupId, metadata}` — the fields
a non-admin fetch omits. `pageProps` also has `totalPages`, `total`, `isAdmin`,
and the filter facets (`annual/monthly/trials/free/levels/courses`).

---

### 1.7 Notifications (the bell) — api2 shape

```
GET https://api2.skool.com/self/notifications?limit=30&type=all
GET https://api2.skool.com/self/notifications?limit=30&type=group&group={gid}
    optional: &category={mentions|comments|following|requests|new_posts|recordings}
              &cursor={cursor from the previous page, URL-encoded}
```

Exactly what the website sends when the bell opens (captured 2026-09-22 from
the `_app` bundle, `listNotificationMessages({limit:30, type, groupID,
category})`). `limit` above 30 → `400 invalid limit` (50 measured); an unknown
category → `400 invalid category: …`. `type=group` + `group={gid}` is the
website's "this community only" toggle.

```jsonc
{
  "messages": [{
    "id": "91239a16…", "action": "mention-comment", "unread": false,
    "created_at": "2026-09-22T07:23:09.38925Z", "updated_at": "…",
    "user_id": "<you>", "group_id": "<gid>",
    "metadata": {
      "data": "{…}"          // a JSON STRING — everything useful is in here
    }
  }],
  "has_more": true,
  "cursor": ":<id>:0:2026-09-14T16:58:41.863364Z",
  "type": "all"
}
```

Parsed `metadata.data`: `action`, `text` (Skool's own caption: "mentioned you
in reply", "(broadcast) new post", "commented on your post", …),
`display_name` + `src_user_id` (who; for `membership-request` the display
name is the literal "New membership request!" and there is no user),
`content` (the text, full length), `group_id`, `group_display_name`,
`group_logo/color/initials`, `image_url`, `link_href` (Next route pattern),
`link_as` (the real path, `/{slug}/{post-name}?p={first 8 of the id}`),
`post_id`, `root_post_id`, sometimes `preview_image_url`,
`src_user_act_status`, `src_user_mrr_status`, `follow`.

- **`post_id` is the object, `root_post_id` the post.** For a comment or a
  mention in a reply they differ: `post_id` is the comment, `root_post_id` the
  post it hangs under. For a post notification they are equal.
- **The community slug is only in `link_as`.** The display name is not a
  slug (hoomans' slug is `the-skool-memes-4437`), and the payload carries
  `group_id` but no name.
- Actions seen on 30 live entries: `admin-post` (broadcast), `mention-comment`,
  `membership-request` (link to `/{slug}/-/pending`, no post),
  `new-post-user-following`, `comment`, `mention`.

**Reading does not mark anything read.** The website marks read with separate
POSTs: `POST /messages` (the "mark all read" button, body `{cmd: "read", all,
group_id, category}`), `POST /messages/{id}/read` (clicking one entry, body
`{created_at}`),
`POST /messages/{id}/unread`, and `POST /self/sync-unread-notification-count`.
The unread count sits on `/self` as `metadata.notifs` (with `unread_chats`).
Measured 22.09.: `notifs` was 0 before and after the GET, so the GET was not
tested against an unread entry — the claim rests on the website's code, which
only ever marks read through those POSTs.

Implemented as `SkoolClient.notifications()` / the `get_notifications` MCP tool
(`normalize.notification` flattens and builds `target` for `get_post` /
`get_post_comments`). Never cached: "what just happened?" has to be live.

A live push channel exists too: `wss://groups-ps.skool.com/ws`, subscribed with
`sub:usp:{uid},cun:{uid},uspc:{uid},ganp:{uid},alss:{gid},gnp:{gid},gps:{gid},pup:{gid}:{postId},…`.
Not used — a poll of the GET on demand is all "what happened?" needs.

---

### 1.8 Search (posts and members) — Next.js shape

```
GET /_next/data/{buildId}/{slug}/-/search.json?q={query}&t=posts&group={slug}[&p={page}]
GET /_next/data/{buildId}/{slug}/-/search.json?q={query}&t=members&group={slug}
```

The search box inside a community (measured 2026-09-22). `t=posts`:
`pageProps.postTrees[]` (same trees as the feed, 10 per page), `totalPosts`,
`totalPostPages`, `page`; page on with `&p=N`. `t=members`:
`pageProps.members[]`, `totalMembers`. **A member hit is the membership**
(`id` = member id, `role`, `groupId`, `approvedAt`, `lastOffline`, `metadata`)
with the person under `user` — the reverse nesting of `members.json`.
`members.json` itself ignores a `q` parameter: this is the only member search of the
Next.js routes (the api2 `@mention` search below is another).
`settings.secondaryMenusItems` carries the hit count per tab.

Implemented as `SkoolClient.search()` / the `search_community` MCP tool
(`normalize.search_member` turns a member hit back into a `list_members`
record, plus `member_id`).

A quicker name-only member search is the editors' `@mention` call
`POST /search/users` (§5.13, `search_members`).

---

### 1.9 Admin dashboard analytics (wait-token) — api2 shape, owner/admin

The Dashboard tab of a community's settings (captured 2026-09-22):

```
GET /groups/{gid}/analytics-growth-overview-v2   -> {num_visitors, num_signups, new_mrr}
GET /groups/{gid}/analytics-overview-v2          -> {num_members}
GET /groups/{gid}/analytics-v2?chart={chart}     -> {chart_data: {items: [...]}}
```

Charts: `signups_by_source` (`attribution` skool | affiliate | direct,
`total`, `percent`), `signups_by_day` (`date`, `num_signups`), `members`
(monthly since founding: `new`, `existing`, `churned` as a negative number,
`total`), `retention_members` (`upgrade`, `downgrade`, `churn`,
`reactivation`, `retention_rate`), `retention_cohorts`, `mrr`,
`mrr_cashflow`, `mrr_unit`. The tiles cover the last 30 days; the website
offers no other range.

**None of these answers with data at first.** Each takes three requests:

1. `GET …` → `{"token": "<hex>"}`
2. `GET /wait?token=<hex>` → **plain text**, not JSON: `in-progress` or
   `completed` (the website also listens on the websocket topic
   `wat:{userId}:{token}` and gives up after 10 s)
3. `GET …&token=<hex>` (or `?token=` without a query) → `{"data": {...}}`

A cached step 1 would replay a used token, so none of it may be cached.
Engagement and retention percentages on the dashboard tiles are not in these
payloads. Implemented as `SkoolClient.growth_overview()` /
`analytics_chart()` (`_analytics()` does the dance), the `get_growth` MCP
tool, and `snapshot --growth SLUG` → `<vault>/trends/growth.jsonl`.

### 1.10 Leaderboards — Next.js shape

The Leaderboards tab (measured 2026-10-11 in a community the account owns and
in `skoolers`, where it is a plain member):

```
GET /_next/data/{buildId}/{slug}/-/leaderboards.json?group={slug}
```

One request carries everything the page shows. `pageProps`:

| Field | Shape |
|---|---|
| `allTime`, `past30Days`, `past7Days` | `{type: "all"\|"30d"\|"7d", offset: 0, limit: 10, updatedAt, users[]}` |
| `users[]` | `{userId, rank, points, updatedAt, user}` — `points` are earned **in that period**; `user` is the full user object, level in `user.metadata.spData` |
| `selfAllTime`, `self30Days`, `self7Days` | `{userId, rank, points, updatedAt, user}` or **null** |
| `self.metadata.spData` | the viewer's overall standing: `{"pts", "lv", "pcl", "pnl", "role"}` — `pcl`/`pnl` are the point thresholds of the current and next level |
| `groupLevels` | `[{number: 1..9, name: "Level N", title, percentOfMembers, actions?}]` |
| `leaderboardsDateQuery`, `leaderboardsCategoryQuery` | `"YYYYMM"`, `""` — only used by the affiliate/MRR variants (`?ld=`, `?lc=`, `?mrr=`) |

Quirks:

- **Top 10 per board, nothing more.** `limit: 10`, and the page has no
  "load more"; members see exactly these ten. There is no further page to
  fetch, and per the access policy none is looked for.
- **`self*` is null** when Skool doesn't rank the viewer: owners and admins
  are left off the boards (measured: the owner with 1,620 points has no
  rank while #1 has 328), and so is a member without points in that period
  (`self7Days` null in skoolers). The level in `self.metadata.spData` is
  there either way.
- **Custom level names are in `title`**; `name` is always `"Level N"`.
  `actions` lists what a level unlocks (`members_post`, `members_chat`).
  The same list sits in `currentGroup.metadata.levels` as a JSON string
  with `percent_of_members`.
- **Access:** a private community bounces non-members to `/about` (the
  usual members-only stub, §0, now with Sentry keys beside it). A public
  one serves the full board to any logged-in account, as the website does;
  `self*` is then null.
- Level 9 is the top. Skool still sends a `pnl` for it (133015), which
  points nowhere.
- **Sensitive:** the viewer's own `self*.user` is the full own user object,
  incl. `payoutAccountId` and `aflCode` (§6.6). Flatten, never pass on raw.

Implemented as `SkoolClient.leaderboards()`, `normalize.leaderboard()` and the
`get_leaderboard` MCP tool (`period="all"|"30d"|"7d"`).

### 1.11 Community rules and level names — api2 shape

> **Status: read from Skool's own page code (build `1794436674711`, 12 Nov 2026),
> never run against Skool by catknows.** The paths are the page client's; the
> **shape of both answers was not measured.** Where a line says "inferred", that
> is a reading of the minified code, not a measurement.

Two plain GETs on `https://api2.skool.com`: no query, no body, no cookie beyond
the usual session. `{gid}` is the group UUID (slug → id as everywhere else, §7.1).

| Call (Skool's name) | Request | What the page reads from the answer |
|---|---|---|
| `getGroupRules` | `GET /groups/{gid}/rules` | `rules[]`, and per rule `title` and `content` (the form writes only `title`, at most 30 characters; at most 10 rules) |
| `getGroupLevels` | `GET /groups/{gid}/levels` | not read anywhere in the captured page chunks; see below |

**Rules** are shown to every member who can read the community: the Rules page
(`/{slug}/-/rules`) lists them as a numbered list of titles (its data comes from a
server-side page call, `readGroupRulesData`, not from this one), and the "Report to
group admins" dialog of a post loads them **with this call** for an ordinary member
and offers them as the reasons to pick from. The admin's Rules editor loads them the
same way.

**Levels.** No page chunk calls `getGroupLevels` itself; the Leaderboards page
gets its level list from the server-side page data (`groupLevels.levels`), which
the page then shows to every viewer (§1.10: `[{number 1..9, name "Level N",
title, percentOfMembers, actions?}]`, the custom name in `title`). That the
direct call `GET /groups/{gid}/levels` answers with the same `{levels: [...]}` is
**inferred**, not run. The admin-only part (naming the levels, `PUT
/groups/{gid}/levels/{number}` with `{title}`) is not built.

**Access policy (`LEGAL.md`).** Both pass the test "can the logged-in user see this
in normal use of Skool?": the rules and the level names are what every member of
the community sees on the Rules and Leaderboards pages. Neither touches one of the
five boundaries (no video, no embedded player, no limit to get around). Membership
stays the gate on Skool's side. Rules and level names are community content, no
personal data.

Implemented as `SkoolClient.group_rules(slug)` and `group_levels(slug)` (raw
answer, no archived guard: an archived community stays readable) and the
`get_community_rules` and `get_community_levels` MCP tools, always on, flagged
read-only. Both return Skool's answer **as it is, through the secret scrub**
(`_safe_raw`), with no normalisation: the shape is not known well enough to
reshape it. A security question (§3) is never swallowed and never retried.

**Open, not verified live:** the shape of both answers (whether the list sits under
`rules` / `levels`, and which other fields come with it, such as an order or a
share of members), what a non-member or a logged-out account gets (the other group
endpoints answer 401 `action not permitted` to a non-member, §3; not measured
here), and whether the rules text in `content` is plain text or `[v2]` TipTap.

---

## 2. Timestamp & casing quirks (read before you parse)

These bite everyone. The client's `normalize.py` centralizes them.

| Field | Format | Convert with |
|---|---|---|
| member `metadata.lastOffline` | **nanoseconds** | `/ 1e9` → unix seconds |
| member `member.createdAt` | nanoseconds **or** ISO string | branch on magnitude / type |
| comment `post.created_at` (api2) | **microseconds** | `/ 1e6` → unix seconds |
| post `createdAt` (Next.js) | ISO string | parse ISO |

**Casing:** Next.js endpoints are **camelCase** (`userId`, `createdAt`,
`groupId`). api2 endpoints are **snake_case** (`user_id`, `created_at`,
`group_id`). The `vote-users` endpoint is **mixed** — read `first_name` with a
`firstName` fallback. When you port the parsers, read snake_case first and fall
back to camelCase.

---

## 3. Response HTTP codes

| Code | Meaning | What to do |
|---|---|---|
| 200 | OK | parse JSON — but any server-side redirect answers 200 with a **stub** (`pageProps` holds only `__N_REDIRECT`/`__N_REDIRECT_STATUS`). A stub targeting `…/about` is the membership gate ("not a member", not "empty community"); a stub targeting deeper into the route (e.g. classroom `?md=…`) is normal navigation — follow it |
| 202 | ISR deferred — page still building | wait ~2s, retry (up to 3×) |
| empty body, 2xx | same as 202 | retry |
| 401 | `auth_token` invalid/expired | re-login |
| 403 | AWS-WAF blocked you | most likely your **TLS fingerprint** (§0.2 — use `curl_cffi`/a browser, headers can't fix it); else refresh `aws-waf-token` and check headers |
| 429 / repeated 202 | rate-limited | back off (this repo pauses ~1h after 5 consecutive 202s) |

**The security question (HTTP 200, no data).** Skool can answer an api2 call with
HTTP 200 and a JSON body whose top-level `code` is a text matching
`^AUTH-SEC-5\d\d$` (known: `AUTH-SEC-501`, `AUTH-SEC-502`), instead of the data.
It means Skool wants a security check for that path (a nonce for 501; 502 is a
Turnstile refusal, read from the page code). It is not an HTTP error: the page's
own client reads `code` only on HTTP 200. Today the nonce is required on eight
`/auth/*` paths (login, sign-up, password reset) that catknows does not call, and
the Turnstile check is switched off in the page data (as read on 2026-11-12). Skool can extend the nonce to
more paths without a new client, and this is how that would show. catknows raises
`catknows.http.SkoolSecurityChallenge` (a `SkoolHTTPError`, `.status` 200,
`.code` the AUTH-SEC code) on `get_api2`, `get_api2_text`, `post_api2`,
`post_api2_read` (the POSTs that only read, §5.13), `put_api2` and `delete_api2`,
and then stops:

- Only HTTP 200, only a JSON object, only the top-level `code`, and only a text
  that matches in full. A number, another text (`OK`, `AUTH-SEC-401`,
  `xAUTH-SEC-501`), a list on top or a body that is not JSON is an ordinary answer.
- No second attempt and no attempt to answer the question.
- The message names the code and the path without its query string, never
  anything else from the answer (no nonce, token or personal data). It ends
  with "Do not retry automatically: stop and tell the user", because the
  caller reading it is often a model that would otherwise try again.
- A write that gets it counts as **not sent**: the audit log gets no `200` line,
  only `status: "security_challenge"` with the code in `note`, and the read
  cache is left as it was. A read that gets it is never cached.
- A batch job over many entries does not count it as one failed entry and go on
  (as it does for an ordinary error): `pull_to_vault` raises the error with how
  far it got, `python -m catknows pull` exits with code 1, `python -m
  catknows.snapshot` exits with code 3. None of them requests anything after it;
  notes and rows already written stay.

**Politeness:** sleep ~0.8s between paginated requests, and don't run tight
loops. Skool will rate-limit aggressive clients.

---

## 4. Minimal request example (any language)

Pseudocode for one api2 call, showing the full header set that gets past WAF.
Remember §0.2: these headers only work when sent over a **Chrome TLS
handshake** (`curl_cffi`, a browser, …) — from a default python/curl TLS
stack, api2 answers 403 no matter what you send:

```
GET https://api2.skool.com/posts/{postId}/vote-users?limit=10&tab=upvotes&cursor=
Headers:
  Cookie:            auth_token={jwt}; aws-waf-token={waf}; <rest of jar>
  Authorization:     Bearer {jwt}
  x-aws-waf-token:   {waf}
  User-Agent:        Mozilla/5.0 (Windows NT 10.0; Win64; x64) ... Chrome/131.0.0.0 ...
  sec-ch-ua:         "Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"
  sec-ch-ua-mobile:  ?0
  sec-ch-ua-platform:"Windows"
  Origin:            https://www.skool.com
  Referer:           https://www.skool.com/
  Accept:            application/json, text/plain, */*
  sec-fetch-dest:    empty
  sec-fetch-mode:    cors
  sec-fetch-site:    same-site
```

For the exact working implementation, read [`catknows/http.py`](../catknows/http.py)
(the request layer) and [`catknows/client.py`](../catknows/client.py) (the
endpoints). Field mappings and every quirk above are implemented in
[`catknows/normalize.py`](../catknows/normalize.py).

---

## 5. Writing to Skool (posts, polls, GIFs, images, videos, DMs)

Everything above is read-only. Skool's frontend also **creates** content through
`api2.skool.com`, and those endpoints work the same way from a script: same
cookies, same `Authorization: Bearer` + `x-aws-waf-token` header set as the api2
GETs (§4), but `POST` with a JSON body.

> **Write carefully.** These act as *you*, in your own communities, visible to
> real members. A post with `notify=members` emails everyone. Test in a private
> group first, respect Skool's ToS, and don't automate spam.

All write endpoints need the **group UUID** (`{gid}`), which you bootstrap from a
posts fetch (§1.2, `post.groupId`).

### 5.1 Create a post

```
POST https://api2.skool.com/posts?follow=true
```

Add `notify=members` to email every member (`?notify=members&follow=true`) — this
is Skool's "email broadcast", so use it sparingly and mind the group's cooldown:
one admin broadcast per 72 hours ("Admins can only send a post via email once
every 72 hours"). A successful send stamps the group's `lastNotifyAll` (ns
timestamp) with the post's `createdAt` — that field is the proof the broadcast
fired; members can individually opt out of admin broadcast emails, so one empty
inbox proves nothing. Verified live 2026-08-13.

Body:

```jsonc
{
  "post_type": "generic",          // "generic" is a normal feed post
  "group_id": "{gid}",
  "metadata": {
    "title": "My post title",
    "content": "The body text.\nNewlines are literal \\n.",
    "action": 0,                    // 0 = normal post
    "video_ids": "",               // comma-joined uploaded-video ids (§5.4)
    "attachments": "id1,id2",      // comma-joined file ids (§5.2 / §5.3) — omit if none
    "labels": "{labelId}",         // category id — omit if none
    "video_links": "https://youtu.be/...",  // YouTube/Loom/Vimeo — omit if none
    "poll": "{pollId}"             // poll id from §5.5 — omit if none
  }
}
```

Only `title` and `content` are required; every other `metadata` field is
optional — send it only when you have a value. On success you get the created
post object back (with its `id`). `content` carries the body as plain text;
@mentions and hyperlinks are just inline text/markup in that string.

### 5.2 Attach a GIF (external, no upload)

GIFs are **external files** — you register the GIF's URL with Skool (no bytes
uploaded) and get back a file id to put in the post's `metadata.attachments`.

```
POST https://api2.skool.com/files
```

```jsonc
{
  "file_name": "reaction.gif",
  "content_type": "image/gif",
  "content_length": 0,             // 0 => external, no S3 upload
  "content_disposition": "",
  "ref": "",
  "owner_id": "{gid}",
  "large_thumbnail": false,
  "external_src": "https://media.giphy.com/.../giphy.gif",       // full-size
  "external_src_small": "https://media.giphy.com/.../200w.gif"   // preview (optional)
}
```

Response nests the id: `{ "file": { "id": "..." }, "write_url": "" }`. For an
external file `write_url` is empty (nothing to upload) — just take `file.id` and
add it to `metadata.attachments`.

*(CatKnows sourced GIFs from GIPHY's search API and passed the `fixed_height` URL
as `external_src`, `fixed_height_small` as `external_src_small`. Any public GIF
URL works.)*

### 5.3 Attach an image (upload → S3)

Local images need a two-step upload: register to get a presigned S3 URL, then
`PUT` the bytes there.

**Step 1 — register** (same `POST /files` as above, but with a real size and no
`external_src`):

```jsonc
{
  "file_name": "screenshot.png",
  "content_type": "image/png",
  "content_length": 84213,         // real byte length => S3 upload path
  "content_disposition": "",
  "ref": "",
  "owner_id": "{gid}",
  "large_thumbnail": false
}
```

Response: `{ "file": { "id": "..." }, "write_url": "https://...s3...?X-Amz-..." }`.

**Step 2 — upload the bytes** to the presigned URL:

```
PUT {write_url}
Headers:
  Content-Type:  image/png          # must match content_type from step 1
  x-amz-acl:     public-read
Body: <raw image bytes>
```

Then add `file.id` to the post's `metadata.attachments` (comma-join multiple).

Implemented as `SkoolClient.upload_file()`, wired into the `attachments`
parameter of `create_post` / `create_comment` (both the client methods and the
MCP tools, which take local file *paths* and upload only on `confirm=true`).
Verified live: a 192-byte PDF round-tripped through register → S3 → post and
came back on the post detail as `attachmentsData` with the right
`content_type`, `file_name` and a working `read_url`. Note the response of
step 1 carries **only** `file.id` — no echo of name, type or length.

### 5.4 Attach a video (upload → Google Cloud Storage)

Videos register to a **different** endpoint and upload to GCS (not S3), with a
`Content-Range` header.

**Step 1 — register:**

```
POST https://api2.skool.com/videos
```

```jsonc
{
  "group_id": "{gid}",
  "file_name": "demo.mp4",
  "content_type": "video/mp4",
  "content_length": 5242880,
  "reference_type": "post"
}
```

Response: `{ "video_id": "...", "upload_url": "https://storage.googleapis.com/..." }`
(some responses use `id` / `write_url` — read both).

**Step 2 — upload the bytes** to GCS:

```
PUT {upload_url}
Headers:
  Content-Type:   video/mp4
  Content-Range:  bytes 0-{len-1}/{len}     # e.g. "bytes 0-5242879/5242880"
Body: <raw video bytes>
```

Then put `video_id` in the post's `metadata.video_ids` (comma-join multiple).

Reading one back (playback handles, and the auto-generated captions Skool
never advertises) is the post detail page, not this endpoint — see §1.2b.

### 5.5 Attach a poll

A poll is created **separately**, then referenced by id in the post metadata.

```
POST https://api2.skool.com/polls
```

```jsonc
{
  "group_id": "{gid}",
  "options": ["Option A", "Option B", "Option C"]   // 2–10 options
}
```

Response is flat and names the id `poll_id` — **not** `id`, and not nested
like `/files`:

```jsonc
{ "poll_id": "dda6e84fc46b4212889a6f278fde239f" }
```

Put that value in the post's `metadata.poll`. (Skool's UI caps polls at 10
options and requires at least 2; the client enforces the same bounds.)

**Reading polls back:** a poll post carries the results inline in its
metadata as a JSON *string* — `pollData` on the Next.js feed, `poll_data` on
api2 responses:

```jsonc
"poll": "dda6e84f...",                      // the poll id
"pollData": "{\"entries\":[{\"text\":\"Ja\",\"count\":3},{\"text\":\"Nein\",\"count\":1}],\"user_id\":\"...\",\"option_index\":-1}"
```

`entries` holds one `{text, count}` per option (in creation order);
`option_index` is which option *you* voted for, `-1` if you haven't.
`normalize.post` parses this into a flat `poll: [{option, votes}]` list.

Implemented as `SkoolClient.create_poll()`, wired into the `poll` parameter
of `create_post` and the MCP tool's `poll_options`. Verified live 2026-08-15:
poll created, attached to a post, results visible in the feed with counts,
post deleted again (`test_poll_live.py`).

### 5.6 The full "rich post" order of operations

```
1. group_id  = groupId from a posts fetch (§1.2)
2. for each GIF:    POST /files  (external_src)          -> file.id
   for each image:  POST /files  (size) -> write_url -> PUT S3   -> file.id
   for each video:  POST /videos -> upload_url -> PUT GCS        -> video_id
   for a poll:      POST /polls                            -> poll.id
3. POST /posts?follow=true  with metadata.{attachments, video_ids, poll,
                                            video_links, labels}
```

Attachments and the poll must exist **before** the post that references them.

### 5.7 Send a direct message

```
POST https://api2.skool.com/channels/{channelId}/messages?ct=wdm
```

```jsonc
{ "content": "hey!", "attachments": [] }
```

`{channelId}` comes from the chat-channels list (§1.6). Response is the created
message object (with `id`). Group-DM and 1:1 channels use the same shape.

`attachments` holds file ids from §5.3 — note the asymmetry against posts,
which is easy to get wrong: **a DM sends a JSON array, a post a comma-joined
string.** Skool stores it as a string either way, so the sent array comes back
as `metadata.attachments: "id1,id2"` on the channel's `last_message`. The POST
response itself does *not* echo the attachments — read the channel to confirm
one landed, not the reply. Verified live (see §5.3).

Implemented as `SkoolClient.send_dm(attachments=[...])` / the `send_dm` MCP
tool, which takes local file *paths* and uploads them on `confirm=true`. A DM
has no community slug: the owning group comes from the channel's `group_id`.

### 5.8 Create a comment / reply

Comments are posts with `post_type: "comment"` — same `POST /posts` endpoint
as §5.1 (captured from live traffic while replying to a comment):

```
POST https://api2.skool.com/posts?follow=false
```

```jsonc
{
  "post_type": "comment",
  "group_id":  "{gid}",
  "root_id":   "{postId}",     // the post the thread belongs to
  "parent_id": "{commentId}",  // the comment being replied to;
                               // for a top-level comment this is the post id too
  "metadata": { "title": "", "content": "reply text" }
}
```

**Two levels only.** `parent_id` must be the post or a *top-level* comment.
A comment hung under a reply is created, counted and searchable, then shows
as "Comment was deleted" (Dan's local write test, 2026-09-15: 5/5 replies
under top-level comments survived, 2/2 under replies died; the same texts
posted by hand stayed). Skool's own UI never sends that — replying to a reply
posts under the top-level comment with an @-mention of the person.
`create_comment()` does the same via `reply_target()`; `GET /posts/{replyId}`
gives the reply's `parent_id` (its top-level comment), the name comes from the
comment tree. That lookup, `GET /posts/{id}`, is catknows' own rule, not a call
of Skool's web client. A library caller that already knows the target is a
top-level comment can save it with `create_comment(..., parent_is_top_level=True)`:
no lookup, `parent_id` is the given comment, no mention, and the POST is the one
the lookup path sends for the same target. The hint is believed, not checked,
and the MCP tool does not take it, so the lookup stays on there.

`content` supports Skool's markdown-ish syntax; an @-mention is
`[@Display Name](obj://user/{userId})`. Response is the created comment
object. (`?follow=true` subscribes you to the thread.)

Implemented as `SkoolClient.create_comment()` / the `create_comment` MCP tool
(draft-first, behind `CATKNOWS_ALLOW_WRITE=1`). Note the asymmetry that makes
this easy to get wrong: `root_id` is **always** the post, while `parent_id` is
the *comment* for a reply and the *post* for a top-level comment.

### 5.9 Edit and delete a post or comment

Captured from live browser traffic 2026-08-17 (throwaway objects in
`hoomans-9944`). A comment **is** a post, so both verbs serve both.

```
POST   https://api2.skool.com/posts/{id}/update
DELETE https://api2.skool.com/posts/{id}
```

The update body is **flat** — no `metadata` wrapper (unlike §5.1) and no
`group_id`, because the id in the path is the whole address:

```jsonc
{
  "title": "catknows edit-endpoint probe",       // "" for a comment
  "content": "the new body text",
  "attachments": "",
  "labels": "c1c43fbd67844f79889c4edbbd223d47",  // absent on comments
  "video_links": "",
  "video_ids": []
}
```

> **The field trap.** Skool's editor sends *every* field on *every* save, so a
> field left out of the body is **cleared**, not kept. Editing only the text
> that way silently drops the post's category and attachments. Read the current
> object first — `GET /posts/{id}` (verified 2026-08-17) returns
> `metadata.{title, content, attachments}` plus `label_id` — and send the
> unchanged values back with the edit.

The DELETE carries a JSON body, at least `{}` (`feedback` and `rules` are
optional in Skool's client; catknows sends `{}`). It is the only DELETE catknows
sends with a body: `DELETE /courses/{id}` (§7.6) and the other DELETE calls it
builds send none, and so does `SkoolHTTP.delete_api2()` unless a body is passed.
(Of the 20 DELETE calls in Skool's page code, only this one and
`DELETE /groups/{id}`, which deletes a community and is never built here, carry
a body.) The answer is an empty `200`. The object is gone from the feed
immediately, and `GET /posts/{id}` then answers `404 post not found`.
Deleting somebody **else's** post needs moderator rights and is untested.

Implemented as `SkoolClient.post_by_id()` / `update_post()` / `delete_post()`
and the `edit_post`, `edit_comment`, `delete_post`, `delete_comment` MCP tools
(behind `CATKNOWS_ALLOW_WRITE=1`): edits are draft-first and show old vs new,
deletes require an explicit `confirm` and first show the text that would
disappear.

### 5.10 What's *not* here

Casting a **like/vote** was never observed in the captured browser traffic. The
verb (`PUT /posts/{id}/vote`) is known from Skool's page code instead and is
written down, with the other small post actions, in §5.12. The read side is §1.5
(`/posts/{id}/vote-users`).

### 5.11 Calendar events — create, change, delete

> **Status: read from Skool's own page code (build `1794436674711`, 12 Nov 2026),
> never run against Skool.** Every path, field and limit below is what the web
> client's event form uses. Nothing here was measured live, which is why the end
> of this section lists what is still open. Test with a throwaway event in a
> private community before relying on it.

```
POST   https://api2.skool.com/calendar-events
PUT    https://api2.skool.com/calendar-events/{id}?from_occurrence_id={occId}
DELETE https://api2.skool.com/calendar-events/{id}?from_occurrence_id={occId}
```

Create and update carry the **same object**, the whole event form. The DELETE has
no body (`SkoolHTTP.delete_api2()` sends none unless one is passed).

```jsonc
{
  "group_id": "…",
  "start_time": …, "end_time": …,
  "metadata": {
    "title": "…",                  // at most 30 characters
    "description": "…",            // at most 300 characters
    "timezone": "…",
    "reminder_disabled": 0,        // 0 = reminder email 1 day before, 1 = off
    "cover_image": …,
    "location": "{\"location_type\":4,\"location_info\":\"https://…\"}",   // JSON TEXT
    "privacy":  "{\"privacy_type\":0}"                                      // JSON TEXT
  },
  "recurrence": { "interval": 1, "frequency": "weekly", "days": [2],
                  "month_day": …, "position": …, "until": …, "count": 10 }
}
```

`location` and `privacy` are JSON **text** inside the JSON body, not objects.

| Field | Values |
|---|---|
| `location_type` | `1` Zoom, `2` Meet (both: `location_info` = the link), `3` Address (text), `4` Link, `5` Skool call, `6` Skool webinar |
| `location_info` for `5`, `6` | **not sent.** The form has no input there; the server hands out the call code and the link is `<community>/live/<code>` (`?ajw=1` for a webinar, which needs a paid plan) |
| `privacy_type` | `0` all members, `1` on/above a level (`min_access_level` 1–9), `2` members of a course (`access_course` = course id), `3` on/above a tier (`min_tier` 1 Standard, 2 Premium, 3 VIP) |
| `recurrence.frequency` | `daily`, `weekly`, `monthly`, `yearly`; `interval` from 1 to at most 15 (daily) / 12 (weekly) / 3 (monthly) / 3 (yearly) |
| `recurrence.days` | weekly: list of 1–7 (1 = Monday); monthly: one weekday together with `position` (1–4 or -1 for the last) |
| `recurrence` end | none, or `until` (the form uses 23:59:59 in the event's timezone), or `count` 1–99 |

**`from_occurrence_id`** is how Skool's "Edit recurring event" dialog says *This
and following events*: with it, an update or delete covers that occurrence and
all later ones; without it, the whole event. Changing or deleting **one**
occurrence alone is a different pair of calls
(`PUT /calendar-events/{id}/occurrence/{occId}`,
`DELETE /calendar-events/{id}/occurrence/{occId}`) and is **not built**.

The web client shows the Plus button and "Edit event" to community **admins**
only (the role `group-admin`). That is read from the page code; a moderator or member is
probably refused by the server too, but that is not verified.

**Checks before anything is sent** (`SkoolClient.check_calendar_event`, a
`ValueError` and zero requests on a violation): `title` ≤ 30 characters,
`description` ≤ 300, `location` must be JSON text of an object with a
`location_type`, and types `5` and `6` must not carry a `location_info` key at all.
The checks look at the fields that are present; no required set is enforced.
Nothing else is validated (recurrence maxima, privacy values, the time fields).
The event id must be real text and not made only of dots: `.` and `..` survive
percent-encoding and libcurl folds such a path piece away (`/calendar-events/..`
becomes `/`, the root), which would aim a PUT or DELETE at the API root instead of
one event. `from_occurrence_id` is a query
value: empty means "not given", otherwise it must be non-blank text (it is
percent-encoded). The refusals name the argument, never its value.

Implemented as `SkoolClient.create_calendar_event(slug, event)` (adds `group_id`
from the slug and refuses an archived community), `update_calendar_event(id,
event, from_occurrence_id=…)` (sends `event` as it is) and
`delete_calendar_event(id, from_occurrence_id=…)`; all three return Skool's answer
as it is. The `create_calendar_event`, `update_calendar_event` and
`delete_calendar_event` MCP tools (behind `CATKNOWS_ALLOW_WRITE=1`) are
draft-first: `confirm=false` contacts nobody and returns what would be sent,
plus a fixed `not_verified` text (create: required fields and the time format;
update: whether Skool keeps a field the update leaves out; delete: the draft cannot
show the event's title, so the user must know which event the id is).
A security question (§3) is never swallowed and never retried. The audit line
(`catknows/audit.py`) names the tool, the community and
`calendar_event_id`, and carries no title, description or location.

**Open, not verified live:**

- Which fields Skool requires on create and update.
- Whether an update that leaves fields out keeps them or resets them (courses do
  both, §7.4). The form always sends the whole event; send the full set.
- The format of `start_time`, `end_time`, `timezone` and `until`. The page code
  shows the half-hour choices (`"00"` … `"23:30"`) and an end = start + 0.5 to 24 h,
  not the wire form. catknows passes these values through unchanged and converts
  nothing.
- Whether the update body needs `group_id` (the form has it; the MCP update tool
  sends it, the client does not add it).
- The value form of `cover_image` (a cropped upload, up to 20 MB, hint 1460 x 752
  px). Not offered by the tools.
- Whether `location_info` is mandatory for types 1–4, and what Skool answers to a
  webinar (type 6) on a free plan.
- How the length is counted (the page code counts in JavaScript, catknows in
  Python characters: they can differ for emoji).
- Where the event id and the occurrence id come from in the read payload
  (`get_calendar` shows neither in its compact form), and what the DELETE answers
  (the web client only shows "Event deleted").

### 5.12 Small post actions — votes, pins, categories, About text, comments switch, follow

> **Status: read from Skool's own page code (build `1794436674711`, 12 Nov 2026),
> never run against Skool by catknows.** The vote, pin, category and About calls
> also run in the author's autopilot; the comments switch, pin-comment and follow
> calls are from the page code only. Where a row says "web client", that is the
> menu the page shows, not a rule measured on the server. Test with a throwaway
> post or category in a private community before relying on any of it.

All twelve are on `https://api2.skool.com`. `{id}` is a **post** id (a comment
is a post, §5.9, but the pin-comment calls take the post here and the comment in
the query).

| Call (Skool's name) | Request | Body | Web client |
|---|---|---|---|
| `updatePostVote` | `PUT /posts/{id}/vote` | `{"old": "", "new": "up"}` (like) or `{"old": "up", "new": ""}` (take it back) | not in the sources (the page has a permission reason `upvote`, what it checks is not recorded); who the server lets vote is not verified |
| `pinPost` | `POST /posts/{id}/pin` | none in Skool's client; catknows sends `{}` | "Pin to feed", group admin only |
| `unpinPost` | `POST /posts/{id}/unpin` | same as `pinPost` | "Unpin from feed" |
| `createLabel` | `POST /labels` | `{"group_id": "…", "metadata": {"display_name": "…", "default_sort": "…", "post_write_role": 4}}` | Settings, Community, Categories |
| `updateLabel` | `PUT /labels/{labelId}` | Skool's client sends the whole form object (name, sort, write role); **catknows sends only** `{"display_name": "…"}` (see below) | same |
| `updateLandingPageDescription` | `POST /groups/{gid}/update-landing-page-description` | `{"description": "…"}` | About page, admin only |
| `disablePostComments` | `POST /posts/{id}/disable-comments` | none | "Turn off comments", at least admin / moderator (the post's author: not checked) |
| `enablePostComments` | `POST /posts/{id}/enable-comments` | none | "Turn on comments" |
| `pinComment` | `POST /posts/{id}/pin-comment?comment={commentId}` | none | "Pin comment", at least admin / moderator |
| `unpinComment` | `POST /posts/{id}/unpin-comment?comment={commentId}` | none | "Unpin comment" |
| `followPost` | `POST /posts/{id}/follow` | none | the bell on a post |
| `unfollowPost` | `POST /posts/{id}/unfollow` | none | same |

"None" means no request body at all: `SkoolHTTP.post_api2(path, None)` leaves the
JSON out (`_write_api2` only sends `json` when the body is not `None`).

**Votes.** The only values are `""` (no vote) and `"up"`; there is no downvote in
Skool's client. Skool answers 429 "Liking too fast! Try again later" to a quick
series. The autopilot sends `{"old": "", "new": "up"}`.

**Pin to the feed.** Skool's own client sends no body; the autopilot sends `{}`
and got HTTP 400 once in 8 days (cause not clarified, not tested live), so the
body question is open (below). Pinning to a *course page* (`pin-module`) and
re-ordering pinned posts (`move-pinned`) are different calls and **not built**.

**Categories.** The category form in the community settings stops at 10 categories
(and keeps at least 1) and at 30 characters for a name. These are limits of the
form as read from the page code, not limits measured on the server. How Skool
counts the 30 is not checked either: catknows counts Python characters (code
points), while a JavaScript string length counts UTF-16 units, where an emoji
counts 2. `post_write_role` is `4` (members and
admins may post) or `3` (admins only). `default_sort` is `newest-cm` (shown as
"Default"), `newest` ("New"), `best-1w` ("Top (week)") or `best-1m` ("Top
(month)"); `best-1y` is shown in the feed but is not selectable in the form.
`createLabel` has no `description` in the request body (Skool's JS function takes
one, the body it builds does not). The `updateLabel` function builds its body from
whichever of `display_name`, `post_write_role` and `default_sort` it is given, but
its only caller, the category form, always passes all three, so Skool's own client
never sends a name-only update. catknows sends only `display_name` (rename), as the
autopilot does; that Skool then leaves the sort and the write role alone has never
been checked. Not built: `deleteLabel`, `updateGroupLabelOrder`
(`move-label`), changing the sort or the write role of an existing category. The
category ids are the `category_id` of a `list_posts` row, or `GET
/groups/{gid}/labels` (§1.2).

**About text.** `metadata.lpDescription` of the group (§6.3) is what this call
writes. Skool's editor counts at most 1000 characters of plain text; a refusal
comes back as 422 with the message in `fields[0].error`. Which format the
`description` string has on the wire (plain text, or `[v2]` TipTap as page bodies,
§7.3) is not documented: catknows sends the string unchanged.

**Comments switch.** With comments off, creating a comment answers 409
(read from the page code, not measured). Whether the existing comments stay visible is not known.

**Pinned comment.** `{id}` is the post the comment sits under, `comment` is the
comment's own id (`get_post_comments`). The web client allows at most one pinned
comment; what the server does with a second pin (refuse, or replace the first) is
its own rule and not verified. The page code shows a 422 with a field message for
a refused pin.

Implemented as `SkoolClient.vote_post(post_id, old, new)`, `pin_post(post_id,
pinned=True)`, `create_category(slug, display_name, *, default_sort,
post_write_role)`, `rename_category(category_id, display_name)`,
`update_about_description(slug, description)`, `set_post_comments(post_id, *,
enabled)`, `pin_comment(post_id, comment_id, pinned=True)` and
`follow_post(post_id, follow=True)`; each returns Skool's answer as it is. The
`vote_post`, `pin_post`, `create_category`, `rename_category`,
`update_about_description`, `set_post_comments`, `pin_comment` and `follow_post`
MCP tools (behind `CATKNOWS_ALLOW_WRITE=1`) are draft-first: `confirm=false`
contacts nobody and returns what would be sent, and for every action with a
side effect nobody verified it also carries a fixed `not_verified` text. A
security question (§3) is never swallowed and never retried. The audit line
(`catknows/audit.py`) names the tool, the community and the ids (`post_id`,
`comment_id`, `category_id`) and carries no category name and no About text.

**Checks before anything is sent** (a `ValueError` and zero requests on a
violation). These are catknows' own rules; none of them is known to be a rule of
Skool's server. Every id that goes into a path or the `comment` query must be
non-empty text and not made only of dots (`.` and `..` stay as they are when
percent-encoded, and libcurl folds `/posts/../pin` into `/pin`); the vote values
must be `""` or `"up"` (the two values Skool's client sends) and differ, because
`old == new` changes nothing; a category name must be non-blank text of at most 30
characters (the form's limit, see above); `default_sort` and `post_write_role` must
be one of the values above (create has no defaults of its own); the About text must
be non-blank text, because a blank text could empty the public About page and
nobody has checked what Skool does with one (see the open points); the on/off flags
must be real booleans. Ids and the `comment` value are percent-encoded into the
path text.

**Open, not verified live:**

- `pinPost` / `unpinPost`: catknows sends `{}` as the autopilot does; Skool's
  client sends no body. One HTTP 400 in 8 days in the autopilot, cause unclear.
- What Skool does with a vote whose `old` does not match the vote it has stored.
- Whether `updateLabel` with only `display_name` keeps the category's sort and
  write role. Skool's own form always sends all three; the only evidence for the
  short form is the autopilot, which sends `{display_name}`, and nobody checked
  what happened to the sort and the role.
- The 10-category limit and the 30-character name limit are the category form's;
  what the server enforces is not known. catknows does not count categories, and
  it counts name characters the Python way (not checked against how Skool counts).
- The wire format of the About `description`, how the 1000 characters are counted
  for a stored string, and what a blank text does (catknows refuses it).
- What a second pinned comment does, and what the comments switch does to
  existing comments.
- Who the server lets do each of these (all read from the web client's menus).
- What each call answers (the tools hand the answer back through the scrub;
  the shape is not documented).

### 5.13 Chat read marks, member search, file download link

> **Status: read from Skool's own page code (build `1794436674711`, 12 Nov 2026),
> never run against Skool by catknows.** The page code is minified; where a row
> says "inferred", that is a reading of the code, not a measurement. Test with a
> throwaway chat, a small community and a small file before relying on any of it.

Four calls on `https://api2.skool.com`. Two change the unread state of the
account (writes), two only read although they are POSTs.

| Call (Skool's name) | Request | Body | Kind |
|---|---|---|---|
| `markChatMessageRead` | `POST /channels/{id}/read?last={last}` | none | write |
| `markAllChatMessageRead` | `POST /self/chat-channels/read` | none | write |
| `searchUsers` | `POST /search/users` | `{"query": "…", "group_id": "…", "limit": 7}` | **read** |
| `getResourceUrl` | `POST /files/{id}/download-url?expire={n}` | none | **read** |

**Read mark of one chat.** `{id}` is a chat channel id (`list_chat_channels`).
`last` is optional. The page calls it three ways: from the channel list's "mark as
read" entry without `last`; from the open chat window (debounced 500 ms) with
`last` = the `id` of a chat message that has just come into view; and without
`last` for the newest message of the loaded list (inferred from the flag the code
tests there). So `last` is a **message id**, and no `last` means the whole chat.
What the server does with `last` is not verified, which is why catknows sends the
text exactly as given and leaves the query out when none is given (an empty text
is refused, not turned into "not given"). Anything that looks for unread chats
(`list_chat_channels(unread_only=true)`, a poller such as an autopilot trigger)
stops seeing a chat after this call: do not use it in a flow that depends on the
unread state.

**Read mark of all chats.** The web client's "Mark all as read". No query, no
body, and no way to name a community or a channel: it is **every chat of the
account**. The MCP draft says so in words.

**Member search.** The `@mention` search of the editors (debounced 300 ms). The
page sends `query`, the group id and `limit` (5 on a phone, 7 on desktop) and
**never a `cursor`** (the function takes one, the JSON drops the unset key).
catknows sends only that one documented desktop form: the body is always exactly
`{query, group_id, limit: 7}` (`SkoolClient.SEARCH_USERS_LIMIT`), with no cursor and
no other limit, so neither the client method nor the tool has a `cursor` argument.
The reason is "contracts, not guesses": what a cursor or another `limit` does is not
documented, and paging a search would be a detour around the member-list limit
(`LEGAL.md` boundary 4). It is sticking to the documented request, not a disguise of
the client. The tool's `limit` (1 to 7, default 7) only shortens the output; a larger
value is lowered to 7 and a `limit_capped` entry says so. The page reads `users[]` from the
answer, each with `id`, `firstName` and `lastName` after its own conversion; the
wire spelling is snake_case (`first_name`), and `normalize.search_user` reads both.
Whether a user object also carries the membership (`role`, `groupId`) is not known:
the record passes on what arrives, and an empty `role` or `group_id` means "not
delivered", not "has no role".
It is a read: `SkoolHTTP.post_api2_read`, which does not write an audit line, does
not wait for the 15 s write gap and does not clear the read cache (§3 still
applies: a security question stops it). The answer carries whatever Skool puts on
a user object; the tool returns the `list_members` record (`normalize.member`
fields, **no `email`, no other contact data**) or, with `raw=true`, the records
through `normalize.scrub` and `normalize.strip_contact`. It is the third member
search next to the community search (§1.8, which also finds bios and returns the
membership id) and `members.json` (§1.1, which ignores a `q`).

**File download link.** `expire` is the page client's `expireSecs`, so very
probably seconds. The page asks with `expireSecs: 28800` for chat attachments
that carry no ready link, and with none for a PDF in a course page and for the
member export file. The answer is used as the link itself (`window.location =
data`, `href = data`): a text. The page client reads a JSON answer as JSON and
anything else as text, so the answer is a JSON string or plain text; catknows
reads both, and returns the link as text. **The link is an access link.** The MCP
tool returns it (that is its job); catknows writes it nowhere: no audit line (a
read leaves none), and no error message repeats an answer: a successful answer is
never echoed, an answer that is not ONE `https://` text without white space (an
object, an HTML page such as a challenge page, `in-progress`, an `http://` link, a
link with a line break) is reported as "not a link" without being shown (the
"https only" requirement is catknows' own safeguard, not checked against Skool: a
link of another form would be refused too), and a link inside an error body is
replaced by `[link removed]`. `get_post` already
carries ready links for a post's attachments (§1.2b): this call is for a file id
without one. For files only: the page never asks for a link of a Mux video or an
embedded video this way (Skool's video download is a separate admin call that is
**not built**, `LEGAL.md` boundary 1).

Implemented as `SkoolClient.mark_chat_read(channel_id, last=None)`,
`mark_all_chats_read()`, `search_members(slug, query)` and
`file_download_url(file_id, expire=None)`. The first two return Skool's answer as
it is; `search_members` returns the answer object, `file_download_url` the link.
The `mark_chat_read` and `mark_all_chats_read` MCP tools (behind
`CATKNOWS_ALLOW_WRITE=1`) are draft-first and flagged destructive: `confirm=false`
contacts nobody and returns what would be sent plus a fixed `not_verified` text.
The `search_members` and `get_file_download_url` tools are read tools, always on,
flagged read-only; `search_members(slug, query, limit=7, raw=false)` returns at most
`limit` of Skool's 7 hits and says so in `limit_capped` when `limit` was above 7; a
`has_more` from Skool is passed on, its cursor never is.
The audit line of a mark names the tool and `dm_channel_id` (and the path, which
carries `last`), no community and no content. A security question (§3) is never
swallowed and never retried, on the marks and on the two reads.

**Checks before anything is sent** (a `ValueError` and zero requests on a
violation): `channel_id`, `last` and `file_id` must be text, not blank and not made
only of dots (`/channels/../read` would fold to `/read`), and are percent-encoded;
`expire`, when given, must be a whole number of 1 or more (nothing else about it is
known); the search needs a non-blank `query`, and the tool's `limit` must be a whole
number of 1 or more (not a boolean). The refusals name the argument, never its value.

**Access policy (`LEGAL.md`).** The member search passes the test "can the logged-in
user see this in normal use of Skool?" for what the page itself asks (the first 5
to 7 matches of a name in a community the account is in). Paging with `cursor` is
**not** something the page does, so catknows does not offer it: it sends the one
request, never walks, and requires a query, so it is a name search and no list of a
community (a member list is served to non-owners as the first page only, and a
workaround is not a bugfix task). Names, handles and bios are personal data: nothing is written
to the vault or a log, no email and no contact data come out.

**Open, not verified live:**

- `markChatMessageRead`: what `last` does on the server, whether no `last` marks the
  whole chat, whether the other person sees a read mark, what the call answers.
- `markAllChatMessageRead`: what it answers, whether the other people see read marks.
- `searchUsers`: the answer's shape beyond `users[]` (whether `has_more` comes back,
  whether a user carries `role` and `groupId`, and what else), what a query is
  matched against (name, handle, bio), whether `group_id` is required. Not built and not to be probed without a new decision:
  a `cursor` and any `limit` other than 7, because their effect is not documented
  and paging the search would route around the member-list limit.
- `getResourceUrl`: the unit and the allowed range of `expire`, the default
  lifetime of a link, whether the answer is a JSON string or plain text, and what
  Skool answers for a file the account may not see or for a video.

---

## 6. Discovery: the global leaderboard + scraping any community

This is the most useful *read* surface for market research: Skool's public
discovery pages expose, for **any** community — with no membership — its size,
price, plan, plugins, owner, and (for the top 100) its **actual monthly
revenue**. All of it is plain logged-in GETs.

### 6.1 The revenue leaderboard — `skoolers/-/games.json`

`skool.com/skoolers` is Skool's own community; its "games" tab is the public
ranking of the top-earning communities.

```
GET /_next/data/{buildId}/skoolers/-/games.json?group=skoolers
```

Response: `pageProps.rows[]` — **100 rows**, each a ranked community:

```jsonc
{
  "globalRank": 1,
  "categoryRank": 1,
  "category": "💰 Money",
  "mrr": 87759425,          // monthly recurring revenue in CENTS (=$877,594/mo)
  "mrrGrowth": 12632000,    // MRR change, cents
  "traffic": 3272,
  "user":  { "id": "...", "name": "handle", "firstName": "...", "lastName": "..." },  // owner
  "group": { "id": "...", "name": "slug", "metadata": { ... } }                        // community
}
```

`pageProps.categories[]` lists the 9 category chips. Per-category boards: append `&category={categoryId}` (the UUID, as in §6.2) — 50 rows each, with `categoryRank`. A row's `user.metadata.mrrStatus` carries the owner's badge (table in §6.3). About half of the communities on these boards are **not** in any category's first 150 on `discovery.json` (trending ≠ revenue), so the two boards are complementary sources. **`mrr` is in cents** —
divide by 100 for dollars (it arrives as a float). This is the "who earns the most on Skool" table.

Re-measured 2026-09-22: `&p=2` is ignored (page 1 again), so 100 overall / 50 per
category is everything there is. `categories[]` entries are `{id, name}`, e.g.
`8a7678583d3246a1a1a0a4a994321146` = 🎨 Hobbies. A row's `user.metadata` holds only
`mrrStatus`, `actStatus`, `pictureBubble`; `group.metadata` only `displayName`, `logoUrl`.
Implemented as `SkoolClient.revenue_leaderboard()` / the `get_revenue_leaderboard`
MCP tool (category by name, `normalize.revenue_row` for dollars and badge).

> **Access:** `skoolers` is gated. A banned/ineligible account gets a `307`
> redirect (`__N_REDIRECT` → `/skoolers/about`) on every members-only route
> (`games`, feed, members) — the JSON body is a ~135-byte stub. Eligibility is
> tied to owning your own community; the exact threshold (e.g. a minimum member
> count) is **not** encoded in the API — it's enforced server-side. Use an
> eligible account. The public `/skoolers/about.json` is readable either way.

### 6.2 The category board — `discovery.json`

```
GET /_next/data/{buildId}/discovery.json          # page 1
GET /_next/data/{buildId}/discovery.json?p={n}     # page 2, 3, …
```

- `pageProps.numGroups` = **1000** (Skool ranks the top 1000), 30 per page → ~34 pages.
- `pageProps.groups[]` — each `{ group, rank, tags }`.
- `pageProps.categories[]` — 9 categories with `slug` (`money`, `tech`, `health`,
  `hobbies`, `music`, `spirituality`, `sports`, `self_improvement`, `relationships`).
- Also present: `sortParam` (default `trending`), `categoryParam`, `priceParam`,
  `languageParam`, `typeParam`.

> **Category filter (verified 2026-09-18):** `?c={categoryId}` — the category's
> UUID from `pageProps.categories[].id` — returns that category's own ranked
> board, 30 per page, and combines with `?p={n}` (e.g. the first 150 of a
> category are `?c={id}` plus `&p=2` … `&p=5`). Each community appears in
> exactly one category (9 × 150 = 1350 distinct slugs). The category **slug**
> does not work: `?c=money` answers 400, and `?category=…`, `?sort=…`,
> `?price=…` are ignored (same trending list as page 1). Revenue sorting is not
> available here — cross-reference §6.1's `mrr` for that. Works without login.

### 6.3 Scraping any community's public profile — `{slug}/about.json`

For **any** slug from the boards above, this returns the full community object,
no membership needed:

```
GET /_next/data/{buildId}/{slug}/about.json?group={slug}
```

`pageProps.currentGroup.metadata` is a goldmine (all readable for outsiders):

| Field | Meaning |
|---|---|
| `displayPrice` | `{"currency":"usd","amount":900,"recurring_interval":"month"}` — real price (amount in **cents**) |
| `membershipModel` | 1 = free, 2 = paid, 3 = freemium, 4 = tiers, 5 = one-time; absent on many free groups. Tiers always carry an entry `displayPrice` — only 1/3/absent join free |
| `freeTrialEnabled` | 1 when a paid group offers a free trial |
| `plan` | `basic` ($9/mo tier) or `pro` ($99/mo tier) |
| `totalMembers`, `totalOnlineMembers`, `totalAdmins`, `totalPosts` | community size & activity |
| `numCourses`, `numModules`, `totalRules` | classroom & rules footprint |
| `owner` | `{id, name, metadata.bio}` of the owner — arrives as a JSON **string** (parse it, like `displayPrice`); `createdBy` = creator UUID |
| `aflPercent` | affiliate commission % |
| `privacy` | 1 = private, 0 = public (measured 2026-09-18: 77 of 1350 were 0, and their `classroom.json` is readable without membership; private ones redirect to `/about`) |
| `tabs` | which features are enabled (`classroom`, `calendar`, `audio-chat`, …) |
| `plugin*Enabled` | active integrations: `pluginHyrosEnabled`, `pluginZapierEnabled`, `pluginGoogleAdsEnabled`, `pluginMetaConversionsEnabled`, `pluginAutoDmEnabled` |
| `hyrosScriptUrl`, `googleTagId` | the actual tracking IDs the owner wired up |
| `lpDescription`, `lpAttachmentsData`, `survey` | landing-page copy & join-survey questions (`survey` is dropped from raw tool output, §6.6) |

Outside `metadata`: `pageProps.pixelId` is the owner's **Meta pixel id** (null
when none is wired up); `metaConversionsStatus` (`success`/`failing`) says
whether the Conversions API link works. Inside the parsed `owner` string,
`metadata.mrr_status` is the owner's revenue badge — the emoji Skool shows
next to their name (thresholds from the skoolers leaderboard, 2026-09-19):

| `mrr_status` | Emoji | MRR from |
|---|---|---|
| `clover` | 🍀 | $3k |
| `liftoff` | 🚀 | $10k |
| `crown` | 👑 | $30k |
| `diamond` | 💎 | $100k |
| `fire` | ♦️ | $300k |
| `goat` (seen 2026-09-22, rank 1 at ~$1.03m) | 🐐 | $1m |

The separate 🔥 next to some names is not a revenue badge (`act_status`,
seen as `hardcore`).

To get the api2 group object too (same data, snake_case + a few extras), take
`currentGroup.id` and call `GET api2.skool.com/groups/{gid}` (§1.6).

### 6.4 Scraping a member's profile — `@{handle}.json`

Any member's profile (see §1.3) returns far more than the display card:

```
GET /_next/data/{buildId}/@{handle}.json?g={anySlug}&group=@{handle}
```

`pageProps`: `currentUser.profileData` carries `totalPosts`, `totalFollowers`,
`totalFollowing`, `totalContributions`, `totalGroups`, `dailyActivities[]`,
`groupsMemberOf[]`, `groupsCreatedByUser[]`, and `following`/`followed` flags.
`pageProps.postTrees[]` holds that member's recent posts, and top-level
`totalGroups` tells you in how many communities they're active. Combined with
§6.1/§6.3 you can map an owner → their communities → size & revenue.

> **Ethics/ToS.** This is public data your logged-in browser can already see,
> but it's still other people's businesses. Scrape gently (rate-limit, cache),
> don't rebuild a competitor to Skool's own directory, and respect
> [LEGAL.md](../LEGAL.md).

### 6.5 Membership requests (pending members) — admin/moderator

When a community gates entry behind an application (join survey), the requests
queue up for an admin/moderator to approve or reject. Read the queue with:

```
GET /_next/data/{buildId}/{slug}/-/pending.json?group={slug}
Header: X-KL-Ajax-Request: Ajax_Request     # observed on this route
```

Owner/moderator only — a non-admin (or wrong community) gets `404`. Response:
`pageProps.users[]` (+ `page`, `total`, `totalPages`). Each pending user is a
normal user object whose `member` block is the application:

```jsonc
{
  "id": "user-uuid", "name": "handle", "firstName": "...", "lastName": "...",
  "member": {
    "role": "pending",                       // ← identifies a request
    "userId": "...", "groupId": "...",
    "searchAnswer": "applicant@email.com",   // the email answer, surfaced flat
    "metadata": {
      "requestedAt": 1786008427315217000,    // nanoseconds
      "requestLocation": "new york (united states)",
      "highRiskScore": 1,                    // Skool's spam/risk flag
      "numRequests": 1,
      "attrSrcComp": "discovery_browse_group_link",  // where they came from
      "survey": "{\"survey\":[                // ← the answered join questions
         {\"question\":\"...\",\"type\":\"email\",\"answer\":\"...\"},
         {\"question\":\"Choose the fruit\",\"type\":\"options\",\"answer\":\"🍌\"}
      ]}"
    }
  }
}
```

`member.metadata.survey` is a **JSON string** — parse it to read each
question/type/answer the applicant submitted. This lets you screen requests
programmatically (e.g. auto-flag `highRiskScore`, check a survey answer).

**Email in the tool output.** `normalize.join_request()` reads this raw user and
returns a flat record whose `email_answer`, every answer of `type: "email"` and
any address typed into another answer are **masked** (`anna@example.com` →
`a***@example.com`, `normalize.mask_email`). A value of type `email` that has no
address shape at all is cut to its first character plus `***`. The
`list_join_requests` MCP tool does the same by default; `reveal_email=true`
(`normalize.join_request(user, reveal_email=True)`) prints them as Skool sent
them. `query` searches the printed form, so a masked call cannot be used to
look an address up. `review_join_request` shows the queue entry masked too.
The rest of the record is unchanged, including `num_requests`.

**Approving / declining** (read from Skool's pending-page code 2026-09-22,
not yet watched on the wire — the queue was empty):

```
POST https://api2.skool.com/members/{member.id}/role
{"new": "member"}      # Approve
{"new": "declined"}    # Decline
```

`member.id` is the membership id from the queue entry, not the user id. The
constants come from the same module that defines `group-admin`,
`group-moderator`, `pending`, `banned`, `payment-pending`. **This endpoint sets
any member's role**, so only ever send it for an id that is in the queue at
that moment; `review_join_request` re-reads the queue and refuses otherwise.
Implemented as `SkoolClient.join_requests()` / `decide_join_request()` and the
`list_join_requests` / `review_join_request` MCP tools.

### 6.6 ⚠️ Sensitive fields in your OWN account payloads

Skool's Next.js pages embed `pageProps.self`, and for your own communities
`self.allGroups[].metadata` includes **secrets you would not want to log or
ship**:

- `apiKeys` — group Zapier API tokens **in cleartext**
- `paymentCard` — `{brand, last4, exp_month, exp_year}`
- `billingEmail`, `payoutAccountId` (Stripe `acct_…`), `billingCycleEnd`
- `aflCode` / affiliate setup on `self.metadata`

These arrive automatically inside otherwise-innocuous page payloads (about,
members, pending, settings). If you persist raw Skool JSON, **strip
`self`/`allGroups` first** — treat those payloads as containing credentials.

**Admin-side member fields (not credentials, still private).** As an owner or
admin, the member objects in `members.json`, `pending.json` and the profile
pages carry data about the person that no tool caller asked for. Where a field
sits (`member`, `member.metadata` or the user's own `metadata`) differs by
field and endpoint (§6.5 shows the pending-queue layout); `scrub` goes by name,
so it does not matter:

| Field (camelCase / snake_case) | What it is |
|---|---|
| `survey` | the join questions and the applicant's answers (JSON string) |
| `searchAnswer` / `search_answer` | the email answer, surfaced flat |
| `attrComp` / `attr_comp`, `attrSrcComp` / `attr_src_comp` | attribution: where the signup came from |
| `requestLocation` / `request_location` | location at the time of the join request |
| `approvedBy` / `approved_by` | who approved the membership (by its name; nothing in catknows reads it) |
| `numRequests` / `num_requests` | how often the person applied |
| `onPc` / `on_pc` | an on-PC flag (seen on the member's own entry; meaning not documented) |

`normalize.PRIVATE_KEYS` lists them, next to `SECRET_KEYS`; `normalize.scrub`
removes both lists at every depth, so no `raw=True` tool result carries them.
Normalizers read the raw object before scrubbing (that is how
`join_request()` still fills `answers`, `location`, `source` and
`num_requests`); their flat output is never scrubbed. A community's own
join-question list (`metadata.survey` in `about.json`, §6.3) shares the name
and is dropped from raw About output with them.

---

## 7. Classroom: courses, folders, pages — read AND write

Everything in Skool's classroom is **one object type** ("unit") arranged as a
tree, and every verb goes through `api2 /courses`. Live-verified 2026-08-15
(created, updated, reordered, published and deleted on a private community —
see `test_classroom_live.py` for the runnable proof).

| UI name | `unit_type` | Position in the tree |
|---|---|---|
| Course (tile) | `course` | root — `root_id` points at itself, no `parent_id` |
| Folder / module group | `set` | child of a course |
| Page / lesson | `module` | child of a course or a set |

A unit (snake_case, api2 shape):

```jsonc
{
  "id": "…32-hex…",          // server-assigned — the client sends NO id
  "name": "aca1d941",         // short URL slug, server-assigned
  "unit_type": "course",      // course | set | module
  "state": 1,                 // 1 = draft (invisible to non-admins), 2 = published
  "parent_id": "…",           // absent on courses
  "root_id": "…",             // always the course
  "group_id": "…", "user_id": "…",
  "created_at": "…", "updated_at": "…",
  "metadata": { /* per-type fields, see 7.7 */ }
}
```

### 7.1 Reading

| Purpose | Call |
|---|---|
| Slug → group UUID (no posts fetch needed) | `GET api2 /groups/{slug}` → `{id, name, metadata}` |
| Course list of a community | `GET api2 /groups/{gid}/courses` → `{courses: [unit], num_all_courses}` |
| **One course's full tree — members only** | `GET api2 /courses/{courseId}` (no query; `withChildren` is not sent and changes nothing, measured live) → `{course, children: [{course, children: […]}]}` |
| Same tree, as the website reads it (no membership needed) | `GET /_next/data/{buildId}/{slug}/classroom/{courseName}.json?group={slug}&md={moduleId}` → `pageProps.course` |

The Next.js classroom page (`{slug}/classroom.json`, §1.6) carries **only the
course tiles** (`pageProps.allCourses[]`, camelCase) — no modules, just
`numModules`. And `GET /courses/{moduleId}` (any query) answers **400 with an
empty body** for non-root units: pages are readable *only* through their
course's tree. Child order in `children[]` is the display order; there is no
position field on the unit.

**Membership gates both api2 course reads — but the page does not.** An
account that is not in the community gets `401 action not permitted` on
`/groups/{gid}/courses` AND on `/courses/{id}`, for **every** course, open
ones included. The classroom page has no such gate: it serves whatever the
community publishes, which is why a non-member can open a course in a browser
at all. So api2 is not "the ONLY module read" — it is the door that needs
membership (and the only one for our own drafts and the whole write path),
while the page is the door the website itself uses.

Reading a course through the page (measured 2026-08-19, `wealthpack` and
`skooligans`, neither joined):

```
GET /_next/data/{buildId}/{slug}/classroom/{courseNAME}.json?group={slug}
  -> pageProps.__N_REDIRECT = "/{slug}/classroom/{name}?md={firstModuleId}"
GET  …same URL…&md={firstModuleId}
  -> pageProps.course = {course, children:[…]}   // the full tree
```

Two catches: the path wants the course's short `name`, not its `id` (resolve
it through `classroom.json`), and without `md=` Skool answers a redirect stub
instead of the tree — the browser follows it, so we do too. The payload is
camelCase here and snake_case on api2 (`unitType`/`unit_type`,
`hasAccess`/`has_access`); readers must take both.

`client.course_tree(course_id, community_slug)` tries api2 first and falls
back to the page, on 401/403 only (never after 429). That fallback is one more
request after a 401/403: a caller that must stop on those passes no
`community_slug`. `http._auth_rejected` also forks on the `action not
permitted` body, so when neither door opens the error names the real cause —
re-logging in never clears that one.

### 7.2 Creating — `POST /courses`

One endpoint for all three types; the server assigns `id` and `name`:

```jsonc
// course
{ "group_id": "{gid}", "unit_type": "course", "state": 1,   // 1=draft, 2=live
  "metadata": { "title": "…", "desc": "plain text!", "privacy": 0,
                "lock_free_trial": 0 } }
// folder
{ "group_id": "{gid}", "unit_type": "set", "parent_id": "{courseId}",
  "root_id": "{courseId}", "state": 2, "metadata": { "title": "…" } }
// page — parent_id = the course OR a set; root_id ALWAYS the course
{ "group_id": "{gid}", "unit_type": "module", "parent_id": "{setId}",
  "root_id": "{courseId}", "state": 2,
  "metadata": { "title": "…", "desc": "[v2][…]", "resources": "[]" } }
```

Validation quirk: `metadata.min_access_level` must be **≥ 1 when present**
(`400 invalid course min access level value: 0`) — omit unset gate fields
instead of sending 0. New units append at the end of their parent.
Also observed from the UI: `POST /courses/{unitId}/duplicate` (no body)
copies a unit; the copy starts as a draft.

### 7.3 Page content: the `[v2]` TipTap format

A **course** `desc` is plain text. A **page** `desc` is Skool's rich-text
document: the literal prefix `[v2]` followed by a compact JSON **array** of
TipTap nodes:

```
[v2][{"type":"paragraph","content":[{"type":"text","text":"Hello"}]}]
```

Node types captured from the live editor: `paragraph`, `heading`
(`attrs.level` 1–4), `hardBreak`, `horizontalRule`, `codeBlock`,
`blockquote`, `bulletList`/`orderedList` + `listItem`, and text marks `bold`,
`italic`, `code`, `link` (`attrs.href`, plus `class/rel/target` when the UI
writes one). An empty body is `[v2][]` or `[v2][{"type":"paragraph"}]`.

Beyond text, a page's metadata can carry:

- `video_link` — external embed URL (YouTube/Loom/Vimeo…); Skool fills
  `video_len_ms` and `video_thumbnail` itself.
- `video_id` — an **uploaded** video: `POST /videos` with
  `{"group_id", "file_name", "content_type", "content_length",
  "reference_type": "course"}` returns `{video_id, upload_url}`; the upload
  URL is **Mux** (posts used GCS, §5.4 — same call, different backend), PUT
  the bytes there, then set `video_id` on the page. `transcript` (string)
  rides along with it.
- `resources` — a JSON **string** holding the page's file/link attachments.
  Files come from `POST /files` (§5.3, same S3 flow — `owner_id` = the gid).
- Cover image (courses): upload via `POST /files` (register + S3 PUT), then
  set `cover_image` = the file's `read_url` **with the file extension
  appended** and `cover_image_file` = the file id — always as a pair.

### 7.4 Updating — `PUT /courses/{id}` (flat body!)

The update body is **flat** — the fields of `metadata` at the top level, NOT
wrapped in `{"metadata": …}` (a wrapped body answers 200 and changes
*nothing*, the classic silent no-op):

```jsonc
// page (send ALL of these — omitted page fields can be dropped)
{ "title": "…", "desc": "[v2][…]", "transcript": null, "video_id": "" }
// course (the full settings set the UI always sends)
{ "title": "…", "desc": "…", "cover_image": "…", "cover_image_file": "…",
  "privacy": 2, "min_access_level": 3, "min_tier": 2,
  "lock_free_trial": true, "is_afl_comp_eligible": false }
```

Two verified traps:

1. **The privacy reset.** A partial course PUT (e.g. only `title`) silently
   resets `privacy` to 0 — open to everyone — while other fields survive.
   Always read the course first and send the complete settings set
   (`SkoolClient.update_course_item` does this for you).
2. **Type asymmetry.** `lock_free_trial`/`is_afl_comp_eligible` are ints in
   the POST metadata but must be **booleans** in the PUT body — an int
   answers `400 invalid request body`.

**Publish / unpublish** is its own sub-resource, not a body field (a flat
`{"state": 2}` PUT is ignored):

```
PUT /courses/{courseId}/state?state=published    // state -> 2
PUT /courses/{courseId}/state?state=draft        // state -> 1
```

### 7.5 Reordering — `POST /courses/{id}/move2?dst={n}`

`dst` is the 0-based target index among the unit's **siblings** (course tiles
within the classroom, pages within their parent). No body. Re-parenting is
**not** possible here: `move2` keeps the parent, and a PUT `parent_id` is
ignored (both measured). Skool's page uses a separate call to put a page into
another folder, `update-parent` (§7.8, read from the page code, not run live);
until that is tried, recreating the page in the target folder and deleting the
original is the proven way.

### 7.6 Deleting — `DELETE /courses/{id}`

- **Pages and folders**: plain DELETE, no body, empty 200. ⚠ Deleting a folder does
  **not** delete its pages — they are lifted to the course root.
- **A whole course** (children are deleted with it) needs
  `?client_id={32-hex}` — without it: `400 invalid client ID`. The id is the
  web app's persistent client id, and it must have passed Skool's email-code
  check once: `POST /auth/email-verify-init {"client_id"}` (mails a 4-digit
  code) → `POST /auth/email-verify {"client_id", "code"}`. The verification
  **sticks to the client_id across sessions** — grab the id from your own
  browser after deleting any course in the UI once, then reuse it forever.
  (A fresh random hex id would need that email dance first.) Because it is reusable
  it is a credential: catknows keeps its value out of the audit line and out of error
  texts and chains (§7.8, `audit._SECRET_QUERY_KEYS`).

### 7.7 The option matrix (what a unit can carry)

Course `metadata` — all verified live except where noted:

| Field | Meaning |
|---|---|
| `title` | tile title (UI caps ~50 chars — reported, not probed) |
| `desc` | plain-text description under the tile |
| `privacy` | **0** open to members · **1** level-locked (needs `min_access_level` ≥ 1) · **2** private/tier (pairs with `min_tier`) |
| `min_access_level` | gamification level 1–9 that unlocks the course |
| `min_tier` | paid tier gate (0 = none) |
| `lock_free_trial` | keep free-trial members out (int on POST, bool on PUT) |
| `is_afl_comp_eligible` | counts toward affiliate commissions (bool, PUT) |
| `cover_image` + `cover_image_file` | cover pair (§7.3) — always both |
| `num_modules`, `has_access` | server-computed; `num_modules` lags/counts only published content — don't write them |

Page (`module`) `metadata`: `title`, `desc` (`[v2]`), `video_link` *or*
`video_id` (+ server-filled `video_len_ms`, `video_thumbnail`), `transcript`,
`resources` (JSON string), `lock_free_trial`. Folders (`set`): `title` only.

**Not yet mapped:** how drip / time-based unlock shows in a payload (no drip
field appeared in any captured payload — the communities tested never had it
configured; the page reads it from `metadata.dripConfig`, a JSON text
`{"enabled", "days"}`, and the create/update bodies spell it `drip_config`). Drip
and cross-folder moves have their own calls (§7.8, read from the page code, not
run live). Third-party docs claim `privacy: 3` (purchase) and `privacy: 4`
(drip) exist — unverified here.

### 7.8 Course settings — drip, folder, share key, import

> **Status: read from Skool's own page code (build `1794436674711`, 12 Nov 2026),
> never run against Skool.** Every path, query and field below is what the web
> client sends; what Skool answers was not measured, which is why the end of this
> section lists what is still open. Test with a throwaway course in a private
> community before relying on any of it.

Four calls on `https://api2.skool.com`, all `POST`, none with a body except the
first:

| Call (Skool's name) | Request | Body | In the web client |
|---|---|---|---|
| `updateCourseDrip` | `POST /courses/{id}/update-drip` | `{"enabled": true, "days": 7}` | a **page's** menu, "Drip status", dialog "Edit drip" (Drip ON / OFF, "Release page N days after members join") |
| `updateCourseParent` | `POST /courses/{id}/update-parent?parent={parentId}` | none | a **page's** menu, "Change folder"; also dragging a page into a folder in the course tree |
| `createCourseShareKey` | `POST /courses/{id}/clone-code` | none | the course tile's menu, "Share course key" (the owner only, inferred) |
| `importCourse` | `POST /courses/{sourceCourseId}/import?group={groupId}&clone_code={key}` | none | classroom, "Import course with key" |

**Drip.** The body is exactly `{"enabled": <bool>, "days": <number>}`. The web
client offers it from a page's menu, so `{id}` is a page id (from
`get_course_tree`); whether the server takes a course or folder id is not known,
the page never sends one. The days field is clamped to **1 to 999** and sent as a
whole number (`days: field ? +field : 0`). With drip on and no days the Save button
stays off, so `enabled: true` always goes out with 1 to 999; with drip off the form
sends whatever days are left in its field, or 0. catknows checks the same: a real
boolean, a whole number (not a boolean), 1 to 999 when `enabled`, 0 to 999 when
not (default 0). Whether the server enforces the same limits is not known. The page
reads the setting back from `metadata.dripConfig`, a JSON text of the same two
fields; the call does not read it first, so a draft cannot show the current one.

**Folder.** `parent` is the new parent's id: a folder's id (`unit_type` `set`, from
`get_course_tree`), or the id of the course itself, which is what the dialog sends
for "None" (the page's `root_id`). No body. The web client moves pages only, and
never puts a folder into a folder. The call sets the parent and nothing else: the
page's place among its new siblings is not set (when a page is dropped at a spot in
the tree, the page sends a second call, `moveCourse`, §7.5; the dialog sends only
this one), so where it lands is not known. The two calls that could not re-parent
are in §7.5.

**Share key.** No body. The answer is an object with `code` (the page reads
`data.code` and shows it in a dialog "Share course key": "Anybody with this key will
be able to duplicate your course in any group."). The page keeps the key it got for
the rest of the session; whether a second call returns the same key or a new one is
not known. The key is 64 characters, and its first 32 are the course id (the import
form takes the course id from them). **It is a credential**, see below.

**Import.** The form refuses a key that is not exactly 64 characters, lowercases it,
and sends the course id as `key.slice(0, 32)` in the path, the id of the community
it is imported into as `group` (`currentGroup.id`: the **group id, not the slug**)
and the whole key as `clone_code`. So the course id in the path is the **source**
course, the one the key belongs to, and the copy goes into `group`. No body. The
page reads nothing from a successful answer; it then says "Import in progress… Only
1 course may be imported at a time. You will be notified when importing is
complete." (the copy is made on Skool's side, the notice comes through the realtime
channel). An error text it would show comes from `data.fields[0].error`. The notes
list as unknown whether duplicating and importing are tied to a plan.

**The share key is a credential.** Whoever holds it can duplicate the whole course
into any community. catknows treats it like an access link (§5.13). What is
guaranteed, and where, so that a log never holds it:

- **Audit line.** `clone_code` is a **query value** and `catknows/audit.py` logs the
  path with its query, so `audit.redact()` replaces the value of `clone_code` with
  `[redacted]` before a line is written (the key name stays, the line still says what
  was sent). The list of such query names is `audit._SECRET_QUERY_KEYS`: a new one
  goes there, not into a call site. The key request's answer carries the key in its
  body; of an answer the line keeps only `response_hash` (16 hex characters of a
  SHA-256, which does not give the key back) and `created_id` when the answer has an
  `id`, so the key is in neither. `_AUDIT_ARGS` has no entry for `clone_code`, so the
  key never becomes an audit field either.
- **Error text.** `SkoolHTTP._write_api2` runs every error text it raises through
  `audit.redact`. On the **key request** (`POST /courses/{id}/clone-code`, whose
  *answer* is the key, `audit.secret_answer`) no error shows any piece of the answer:
  the HTTP error and the bad-JSON error say "answer not shown" instead of the first
  300 / 200 characters, and keep the HTTP status. The 401/403 refusal and the security
  question (§3) are unchanged: same type, code and status, no second try.
- **Exception chain.** The MCP SDK logs every tool exception that is not a
  `ToolError` with its whole chain (`__cause__`), so what hangs on the chain is in a
  log. On a path that carries a credential in the request or the answer
  (`audit.carries_secret`) the error is raised with no cause and no context
  (`from None`, outside the handler), so the library's own message, which can repeat
  the URL, does not travel on. Other write paths keep their cause.
- **Draft.** The `import_course` draft shows the key's length, never the key.
- **Not covered.** The MCP tool `create_course_share_key` returns the key in its answer
  to the caller: that is its job. A key you pass to `import_course` sits in the
  conversation that passed it. `audit.redact` finds a value only behind its name
  (`clone_code=`): an error body of the **import** that echoes the bare key without it
  is not scanned (that body is still shown, because Skool's reason, e.g. "Invalid code",
  is what the user needs).

The same rule covers `client_id`, the verified id that lets a whole course be deleted
(§7.6): `delete_course_item` puts it in the query (`?client_id=…`), it is reusable for
good and so as good as a password for deleting courses. It is in
`audit._SECRET_QUERY_KEYS`: the wire carries the real value, the audit line and every
error text show `client_id=[redacted]`, and the error has no chain.

Implemented as `SkoolClient.set_course_drip(id, enabled, days=0)`,
`move_course_to_folder(id, parent_id)`, `create_course_share_key(id)` and
`import_course(course_id, slug, clone_code)` (the slug decides `group`, and an
archived community is refused up front); all four return Skool's answer as it is.
The MCP tools of the same names (behind `CATKNOWS_ALLOW_WRITE=1`) are draft-first
and flagged destructive: `confirm=false` contacts nobody and returns what would be
sent plus a fixed `not_verified` text. A security question (§3) is never swallowed
and never retried. The audit line names the tool, the course (as
`course_item_id`) and, for the import, the community; no content.

**Checks before anything is sent** (a `ValueError` and zero requests on a
violation): ids must be real text, not blank and not made only of dots (`..` would
be folded away by libcurl and could aim the call at another endpoint), and are
percent-encoded; `course_id` and `parent_id` must differ (a unit cannot sit inside
itself); the drip limits above; for the import, the key must be exactly 64
characters (it is lowercased as the page does) and `course_id` must be its first 32
characters, which is the only form the page ever sends. The refusals name the
argument, never its value, and never the key.

**Access policy (`LEGAL.md`).** None of the four is a read, so the read test does
not apply; against the five boundaries: no video file is downloaded, no embedded
player is touched, and no limit is gone around. The share key is Skool's own
feature, created by the owner of a course and handed over on purpose, and the import
is Skool's own copy inside Skool. catknows sends the one request the page sends,
with the key the user holds, and reports Skool's answer: a plan limit or a refusal
is shown, not worked around. The tools are for a key its owner gave to the user.

**Open, not verified live:**

- `updateCourseDrip`: what it answers, whether the server enforces 1 to 999, whether
  a course or folder id works as well as a page id, and what drip does to members
  who already have access.
- `updateCourseParent`: what it answers, where the page lands among its new siblings,
  whether the server refuses a folder as `{id}` or a page as `parent`, and whether it
  checks that both belong to the same course.
- `createCourseShareKey`: what exactly the answer holds beside `code`, who the server
  allows (the page shows the entry to the owner), and whether a second call gives the
  same key.
- `importCourse`: what a successful call answers, whether the copy arrives as a draft
  or already published (visible to members), where in the classroom it lands, what
  happens to a key whose course was deleted, and whether a plan is needed.

### 7.9 In this repo

`SkoolClient`: `classroom_courses`, `course_tree`, `create_course`,
`create_course_item`, `update_course_item` (read-merge guard against the
privacy reset), `set_course_state`, `move_course_item`, `delete_course_item`,
`set_course_drip`, `move_course_to_folder`, `create_course_share_key`,
`import_course` (§7.8), plus the `tiptap()` helper (plain text → `[v2]`
paragraphs). MCP tools (reads always on, writes behind `CATKNOWS_ALLOW_WRITE=1`,
draft-first): `get_course_tree`, `create_course`, `create_course_item`,
`update_course_item`, `publish_course`, `move_course_item`,
`delete_course_item`, `set_course_drip`, `move_course_to_folder`,
`create_course_share_key`, `import_course`.
