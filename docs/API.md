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
      ?t=active&sortType=-memberlastoffline&group={slug}          # page 1
GET /_next/data/{buildId}/{slug}/-/members.json
      ?t=active&sortType=-memberlastoffline&p={page}
      &online=&levels=&price=&courseIds=&monthly=false
      &annual=false&trials=false&group={slug}                     # page N
```

`sortType=-memberlastoffline` sorts DESC by "last offline", so currently-active
members come first. Response: `pageProps.users[]`, plus `pageProps.totalPages`
for pagination. Each user:

```jsonc
{
  "id": "user-uuid",
  "name": "handle",                    // the @handle URL slug
  "email": "a@b.com",                  // only visible to owners/admins
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
nested reply's parent is the comment it hangs under.

### 1.5 Likes (upvoters) — api2 shape (mixed case)

```
GET https://api2.skool.com/posts/{postId}/vote-users?group-id={gid}
```

```jsonc
{
  "users": [
    { "id": "user-id", "name": "handle",
      "first_name": "Ada", "last_name": "Lovelace" }  // or firstName/lastName — handle both
  ]
}
```

Skool gives no "un-liked at" signal — to detect a rescinded like you diff the
current set against a previously stored set (this repo's graph app does; the
plain client just returns the current set).

### 1.6 Other read endpoints

| Purpose | Method | Shape |
|---|---|---|
| Community About | `GET /_next/data/{buildId}/{slug}/about.json?group={slug}` | Next.js |
| Calendar/events | `GET /_next/data/{buildId}/{slug}/calendar.json?group={slug}` (`&calDate={unixTs}` for other months) | Next.js |
| Classroom/courses | `GET /_next/data/{buildId}/{slug}/classroom.json?group={slug}` | Next.js |
| Single course/module | `GET /_next/data/{buildId}/{slug}/classroom/{courseId}.json?group={slug}&course={courseId}` (`&md={moduleId}` for a specific lesson) | Next.js |
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
| Chat channels | `GET https://api2.skool.com/self/chat-channels?offset={n}&limit=30&last=true&unread-only=false` | api2 |
| Chat messages | `GET https://api2.skool.com/channels/{channelId}/messages?before=50` | api2 |

> Live-verified against two logged-in accounts (Aug 2026). Notes: `/self/me`
> and `/self/profile` return **404** — only bare `/self` works. `/self/groups`
> pages 30 at a time (`has_more` tells you when to stop); `/self/list-visibility-groups`
> returns everything in one call as `groups_member_of[]` + `groups_created_by_user[]`.

**`self/groups`** is the "which communities am I in" endpoint — paginate
`offset` by 30 until a page is short. Each group object: `name` (the slug),
`role` (may be absent), and `metadata.{displayName, logoUrl, color,
totalMembers, owner}`. `metadata.owner` is a user UUID; compare it to your own
id (from `/self`) to tell **owner** apart from plain **member** when `role` is
missing.

**`admin-metrics`** (owner-only) returns `total_members[]` and `active_members[]`
as 30-day time series (`{value, time}` points) plus `latest_active_members` and a
`daily_activities` range — the raw numbers behind a community's admin dashboard.

**`groups/{gid}/discovery`** (owner) is *your own* community's discovery
standing: `{is_showing, rank, category:{id,name}, category_rank, language_code,
boost_enabled, rank_updated_at}`. (This is different from §6's global board.)

**Member view of members.json**: when you fetch `members.json` as an **admin**,
each `users[]` entry additionally carries `email`, `timeZone`, and a full
`member.{role, approvedAt, lastOffline, userId, groupId, metadata}` — the fields
a non-admin fetch omits. `pageProps` also has `totalPages`, `total`, `isAdmin`,
and the filter facets (`annual/monthly/trials/free/levels/courses`).

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
| 200 | OK | parse JSON |
| 202 | ISR deferred — page still building | wait ~2s, retry (up to 3×) |
| empty body, 2xx | same as 202 | retry |
| 401 | `auth_token` invalid/expired | re-login |
| 403 | AWS-WAF blocked you | most likely your **TLS fingerprint** (§0.2 — use `curl_cffi`/a browser, headers can't fix it); else refresh `aws-waf-token` and check headers |
| 429 / repeated 202 | rate-limited | back off (this repo pauses ~1h after 5 consecutive 202s) |

**Politeness:** sleep ~0.8s between paginated requests, and don't run tight
loops. Skool will rate-limit aggressive clients.

---

## 4. Minimal request example (any language)

Pseudocode for one api2 call, showing the full header set that gets past WAF.
Remember §0.2: these headers only work when sent over a **Chrome TLS
handshake** (`curl_cffi`, a browser, …) — from a default python/curl TLS
stack, api2 answers 403 no matter what you send:

```
GET https://api2.skool.com/posts/{postId}/vote-users?group-id={gid}
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
is Skool's "email broadcast", so use it sparingly and mind the group's cooldown
(readable from `groups/{gid}`).

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

Response contains the poll `id`. Put it in the post's `metadata.poll`. (Skool's
UI caps polls at 10 options and requires at least 2.)

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

`content` supports Skool's markdown-ish syntax; an @-mention is
`[@Display Name](obj://user/{userId})`. Response is the created comment
object. (`?follow=true` subscribes you to the thread.)

### 5.9 What's *not* here

These were never observed in the reverse-engineered traffic, so they're left
undocumented rather than guessed: casting a **like/vote** and
**editing/deleting** a post. The read side (§1.4, §1.5) exists; the
corresponding write verbs likely follow the same `api2` conventions
(`POST`/`PUT`/`DELETE` on `/posts/{id}/...`) but confirm them from your own
browser's Network tab before relying on them.

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

`pageProps.categories[]` lists the 9 category chips. **`mrr` is in cents** —
divide by 100 for dollars. This is the "who earns the most on Skool" table.

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

> **Filtering caveat (verified):** the `discovery.json` data route **ignores**
> query params — `?c=money`, `?sort=…`, `?price=paid` all return 200 but the
> same trending list (`categoryParam` stays `null`; `?c=…` even 400s in some
> builds). Only `?p={n}` pagination is honoured. Skool applies category/sort
> filtering **client-side** (there's an Algolia `queryID` in the payload), so to
> filter by category/revenue you pull the pages and sort/group locally on
> `metadata.totalMembers` / `displayPrice`, or cross-reference §6.1's `mrr`.

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
| `membershipModel` | 1 = free, 2 = paid |
| `plan` | `basic` ($9/mo tier) or `pro` ($99/mo tier) |
| `totalMembers`, `totalOnlineMembers`, `totalAdmins`, `totalPosts` | community size & activity |
| `numCourses`, `numModules`, `totalRules` | classroom & rules footprint |
| `owner` | `{id, name, metadata.bio}` of the owner; `createdBy` = creator UUID |
| `aflPercent` | affiliate commission % |
| `privacy` | 1 = private, 2 = public |
| `tabs` | which features are enabled (`classroom`, `calendar`, `audio-chat`, …) |
| `plugin*Enabled` | active integrations: `pluginHyrosEnabled`, `pluginZapierEnabled`, `pluginGoogleAdsEnabled`, `pluginMetaConversionsEnabled`, `pluginAutoDmEnabled` |
| `hyrosScriptUrl`, `googleTagId` | the actual tracking IDs the owner wired up |
| `lpDescription`, `lpAttachmentsData`, `survey` | landing-page copy & join-survey questions |

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

> **Approving/rejecting** a request is a **write** action (`POST` to an api2
> `members`/`requests` endpoint). It wasn't captured here, so it's intentionally
> not documented — grab it from your own Network tab when you accept a member,
> the same way §5 was sourced. Reading the queue (above) is enough to *detect*
> pending members and drive an alert/automation.

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
