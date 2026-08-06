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
especially the paginated api2 endpoints. To get through you must:

1. Send a **realistic `User-Agent`** plus matching `sec-ch-ua*` headers (a Chrome
   UA with no `sec-ch-ua` is a tell). Pick one browser profile per session and
   keep it consistent.
2. Send the **`x-aws-waf-token`** header (the bare `aws-waf-token` value) on
   api2 requests, not just the cookie.
3. Set `Origin: https://www.skool.com`, `Referer`, and the `sec-fetch-*` headers
   that a real cross-origin `fetch()` would carry.

The `aws-waf-token` is produced by JavaScript solving a WAF challenge — which is
exactly why a **real browser** (Playwright) is the robust way to obtain it. It
expires roughly every 24h; re-visit skool.com in the browser to refresh it.

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
    "metadata": { "points": 42 }
  },
  "metadata": {
    "online": true,
    "lastOffline": 1700000000000000000, // ⚠ NANOSECONDS since epoch
    "pictureProfile": "https://...",
    "bio": "..."
  }
}
```

### 1.2 Posts — Next.js shape

```
GET /_next/data/{buildId}/{slug}.json?group={slug}          # page 1
GET /_next/data/{buildId}/{slug}.json?group={slug}&p={page} # page N
```

Sorted by last activity. Response: `pageProps.postTrees[]`. Each tree has a
`post`:

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

### 1.6 Other endpoints

| Purpose | Method | Shape |
|---|---|---|
| Community About | `GET /_next/data/{buildId}/{slug}/about.json?group={slug}` | Next.js |
| Calendar/events | `GET /_next/data/{buildId}/{slug}/calendar.json?group={slug}` (`&calDate={unixTs}` for other months) | Next.js |
| Classroom/courses | `GET /_next/data/{buildId}/{slug}/classroom.json?group={slug}` | Next.js |
| Group info | `GET https://api2.skool.com/groups/{gid}` | api2 |
| Discovery (related communities) | `GET https://api2.skool.com/groups/{gid}/discovery` | api2 |
| Admin metrics (owner only) | `GET https://api2.skool.com/groups/{gid}/admin-metrics?range=30d&amt=monthly` | api2 |
| Chat channels | `GET https://api2.skool.com/self/chat-channels?offset={n}&limit=30&last=true&unread-only=false` | api2 |
| Chat messages | `GET https://api2.skool.com/channels/{channelId}/messages?before=50` | api2 |

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
| 403 | AWS-WAF blocked you | refresh `aws-waf-token` (re-visit skool.com in browser); check headers |
| 429 / repeated 202 | rate-limited | back off (this repo pauses ~1h after 5 consecutive 202s) |

**Politeness:** sleep ~0.8s between paginated requests, and don't run tight
loops. Skool will rate-limit aggressive clients.

---

## 4. Minimal request example (any language)

Pseudocode for one api2 call, showing the full header set that gets past WAF:

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

For the exact working implementation, read [`skoolapi/http.py`](../skoolapi/http.py)
(the request layer) and [`skoolapi/client.py`](../skoolapi/client.py) (the
endpoints). Field mappings and every quirk above are implemented in
[`skoolapi/normalize.py`](../skoolapi/normalize.py).
