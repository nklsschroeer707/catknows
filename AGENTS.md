# AGENTS.md — for AI agents working in this repo

You're an AI (Claude, Codex, …) asked to pull Skool data or extend this client.
Start here.

## What this repo is

An unofficial Python client for Skool's private API. It logs in via a real
browser (Playwright), calls Skool's undocumented endpoints with WAF-faithful
headers, and writes the results as Obsidian Markdown.

If you were asked to *run a Skool job* (pull members, digest posts, build a
report, …) rather than work on the code: stop here and go to
[workspaces/START-HERE.md](workspaces/START-HERE.md) — the ICM job catalog.

## Read these first, in order

1. **[docs/API.md](docs/API.md)** — the complete endpoint reference. This is the
   source of truth. Every endpoint, header, auth detail, and JSON field is here,
   including the three hard parts (§0): the `httpOnly` auth cookie, the AWS-WAF
   headers, and — the subtle one — the **TLS handshake**. AWS-WAF fingerprints
   TLS (JA3/JA4), so plain `requests` gets a 403 on api2 *even with a valid token
   and perfect headers*; the client uses `curl_cffi` with `impersonate="chrome"`.
   **Headers can't fix a TLS 403.** Do not guess field paths — they're all documented.
2. **[catknows/http.py](catknows/http.py)** — the request layer (Chrome TLS via
   `curl_cffi`, WAF headers, buildId discovery, retries). The working implementation of API.md §0.
3. **[catknows/client.py](catknows/client.py)** — one method per endpoint,
   pagination handled.
4. **[catknows/normalize.py](catknows/normalize.py)** — the quirks (ns/µs
   timestamps, snake/camelCase). Has a runnable self-check: `python -m catknows.normalize`.

## To fulfil "pull my community into a vault"

The whole flow already exists — don't rebuild it:

```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: py -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e .
playwright install chromium                          # Linux: playwright install --with-deps chromium
python -m catknows pull <slug> --vault ./vault
```

The venv matters: recent macOS/Linux Python is "externally managed" (PEP 668)
and rejects a bare `pip install`. The first run opens a browser for login. If a
browser can't be shown (headless environment / no display), ask the user for
their Skool `Cookie` header and use `--cookie "..."` instead.

## MCP server (plug catknows into any AI)

`catknows/mcp_server.py` exposes the client as an [MCP](https://modelcontextprotocol.io)
server over stdio — this is catknows' primary direction: **the bridge between
Skool and any MCP-capable tool** (Claude, Codex, Cursor, …). It's a thin wrapper
over `SkoolClient`; don't duplicate client logic in it.

- The server registers **31 tools** by default and **63 tools** with
  `CATKNOWS_ALLOW_WRITE=1`. `test_mcp_tools_doc.py` counts both against the
  running server and fails if this file, the README or the `catknows-update`
  skill says another number.
- Read tools (always on, those in `_READ_ONLY`): members, member search,
  community search, posts, video transcripts, comments, likes (one page per
  call), member profile, follows, community about, rules and level names,
  discovery, leaderboards, admin metrics, growth, calendar, classroom, course
  tree, chat channels, DMs, notifications, join requests, a file's download
  link and `list_my_communities`. Three more are always on without being read
  tools: `login_to_skool`, `pull_to_vault` and `update_catknows`.
- `list_my_communities` is the entry point for cross-community questions —
  every other tool needs a slug, and this is where slugs come from. Skool omits
  `role` for communities you own, so it's derived by comparing
  `metadata.owner` against your own `/self` id (`normalize.my_community`).
- Write tools (those in `_DESTRUCTIVE`): posts and comments (`create_post`,
  `create_comment`, `edit_post`, `edit_comment`, `delete_post`,
  `delete_comment`), `send_dm`, `review_join_request`, the classroom writers
  (`create_course`, `create_course_item`, `update_course_item`,
  `publish_course`, `move_course_item`, `delete_course_item`,
  `set_course_drip`, `move_course_to_folder`, `create_course_share_key`,
  `import_course`), the calendar writers (`create_calendar_event`,
  `update_calendar_event`, `delete_calendar_event`), the small post actions
  (`vote_post`, `pin_post`, `create_category`, `rename_category`, `delete_category`,
  `update_about_description`, `set_post_comments`, `pin_comment`,
  `follow_post`) and the chat read marks (`mark_chat_read`,
  `mark_all_chats_read`): **only registered when `CATKNOWS_ALLOW_WRITE=1`**.
  They're draft-first — `confirm=false` returns the draft without writing;
  `confirm=true` actually writes. Deletes show the title and text that would
  disappear, because an id alone is not something a user can review.
  `notify_members` (email broadcast) is a separate explicit flag. Never weaken
  this: writes act as the user, visible to real members.
- Drafts that carry prose (post, comment, DM, course page) also return
  `before_you_show_this`: a short instruction telling the calling model to
  strip AI tells before the user reads the draft. The server has no model, so
  this is the only place the pass can happen. Patterns from Wikipedia's "Signs
  of AI writing" via [humanizer](https://github.com/blader/humanizer) (MIT).
  Settings-only drafts (a privacy flag, a move, a delete) skip it — no prose,
  no pass. Build drafts with `_draft(...)` so this stays in one place.
- `CATKNOWS_VOICE_FILE` (optional) points at a Markdown voice guide of the
  account owner. Every prose draft then also carries `write_as_the_user`:
  the guide (frontmatter stripped, read fresh per draft) and the order to run
  a humanizer skill with it as the writing sample. A configured file that
  can't be read says STOP in the draft instead of falling back silently.
  Unset = no change. `test_voice_guide.py`.
- Every new tool must be listed in `_READ_ONLY` or `_DESTRUCTIVE` — a tool in
  neither fails the self-check, on purpose.
- **Every write is logged, and you don't have to do anything for that.**
  `catknows/audit.py` writes one line per write in `http._write_api2` — the one
  function `post_api2`/`put_api2`/`delete_api2` all pass through, so the CLI,
  `pull_to_vault` and doc examples are covered as well as the tools. A new write
  method needs no logging call. What it *may* need is a line in `_AUDIT_ARGS`
  (`mcp_server.py`) if it names its target with an argument not already there —
  that map is how the tool boundary tells the HTTP layer which community and
  which id a write was aimed at. Board decision D8; `--self-check` asserts the
  context still reaches tool calls.
  Two rules that are not style: **no content in the log** (no post text, no DM
  text, no member data — the hosted deployment states this in its published
  privacy policy, so a content field added here would make a public document
  untrue), and **the log never steers** — no queue, no retry hanging off it.
  It also must never break a write: `record()` swallows its own errors on purpose.
  A credential that rides in a **query** (`clone_code`, `client_id`, the one-shot
  `token` of the dashboard numbers) belongs in `audit._SECRET_QUERY_KEYS`: the
  wire keeps the real value, the log line and every error text of the HTTP layer
  (writes and reads) show `[redacted]` or name the URL without its query, and on
  such a path the error carries no exception chain (docs/API.md §7.8, §7.6, §1.9).
- **At least 15 s between two writes of the same Skool account** (owner's decision
  2026-09-22, replaces the old rule D5 "no throttle"). Enforced in the same
  `http._write_api2`, so every write path waits, failed writes count as sent.
  Keyed by the account's token: a hosted server's users don't wait on each other.
  Fixed value, no jitter, no randomness: tempo hygiene, not disguise.
  `CATKNOWS_WRITE_GAP_S` overrides. Reads are never delayed by it.
  `test_write_gap.py` checks it offline. Don't add random pauses, fake
  fingerprints or anything else that hides automation — what protects the
  account is one approved write at a time, not looking human.
- Writes into an **archived** community refuse up front
  (`group_id_for(for_write=True)`). Archived communities stay readable, so
  reads must keep calling it without the flag.
- Install/run: `pip install -e ".[mcp]"` then `python -m catknows.mcp_server`
  (stdio). Register with `claude mcp add catknows -- python -m catknows.mcp_server`,
  or a project `.mcp.json` (gitignored — holds machine paths).
- `stdout` is the protocol channel — `login()`'s prints are redirected to stderr.
  Anything a tool prints to stdout would corrupt the stream.
- **Size cap:** tool results have a max token size. `list_members`/`list_posts`
  hard-cap their `limit` (`_cap()`, raw capped harder) and append a
  `limit_capped` trailer when the caller asked for more, so a capped list can't
  pass for a complete one; `get_community_about` and
  `get_discovery` return compact summaries, not the raw payload. Don't return big
  raw blobs by default — mobile clients have no filesystem fallback.
- **Secret scrubbing (SECURITY — never weaken):** Skool page/api payloads embed
  credential-class fields (cleartext Zapier `apiKeys`, Stripe `payoutAccountId`,
  `paymentCard`, `billingEmail`, affiliate secrets, `pageProps.self`, and Skool's
  own client-side keys in `pageProps.env` — docs/API.md §6.6). These ride along
  in *otherwise innocuous* payloads (about, calendar, classroom, members, posts,
  comments, profile, chat channels) and land on your OWN user object even on
  other people's data. **Every `raw=True` path and anything returning a Skool
  payload verbatim MUST route through `_safe_raw()` (→ `normalize.scrub`).** The
  scrub lists live in one place: `normalize.SECRET_KEYS` (credentials) and
  `normalize.PRIVATE_KEYS` (admin-side data about a member: join survey, email
  answer, attribution, request location, approver, request count, on-PC flag)
  — add a field name there, never a one-off `pop()` at a call site.
  `normalize.scrub` mutates in place; that's safe because the HTTP cache
  stores/returns `copy.deepcopy` (never hand a scrubbed object back into the
  cache). Scrub raw payloads only: a normalizer reads the raw object first and
  its flat output is never scrubbed (`join_request` has its own `num_requests`).
- **No member email in tool output.** `normalize.member()` and the vault note
  carry no `email`; `list_join_requests` and the `review_join_request` draft
  mask email answers (`normalize.mask_email`) unless the operator passes
  `reveal_email=True`. A new reader of the join queue masks too.
- **Points quirk:** member points/level come from `metadata.spData` (a JSON
  string), NOT `member.metadata.points` (always 0). Handled in `normalize.member`.
- **Discovery:** `get_discovery` uses the Next.js `discovery.json` board (global
  top-1000, paged). The api2 `/groups/{gid}/discovery` endpoint is WAF-blocked (403).
- **Gated 404:** member/post data is members-only. If the logged-in account isn't
  in the community, Skool returns `{"notFound":true}` → we raise a clear "not a
  member" error. `about`/`discovery` are public and work without membership.
  **`t=active` on `members.json` is redundant — and Skool flipped it
  server-side.** Measured 2026-08-12: 404 for everyone, owners included.
  Re-measured 2026-08-15: 200 for everyone, returning exactly the unfiltered
  list (it's the default view). Both measurements were real; Skool changed.
  Don't re-add the parameter — it buys nothing and has already flipped once.
  What still 404s is an *unknown* `t=` value; the known lifecycle values
  (`t=cancelling`/`churned`/`banned`) work for regular members too, as do
  `admin=true`, `online=true` and the `sortType` variants. A 404 that appears
  only on *some* queries is a parameter problem, not a permission one.
  **Members pagination can silently degrade:** per community/role/session
  Skool re-serves page 1 for every later page (seen on a 5.5k community as a
  regular member with a fresh session; a 592-member community walked fine).
  `members()` flags this via `MemberList.incomplete` — never treat a
  truncated walk as the whole community.
- Remote/mobile hosting (streamable-http transport + auth) is planned, not built:
  see [docs/MOBILE_MCP_PLAN.md](docs/MOBILE_MCP_PLAN.md). The two-doors model
  (open repo self-host, no auth / hosted with login-auth) lives there.

## To add a new endpoint

**Before you build a read tool, check it against the access policy**
([LEGAL.md](LEGAL.md#access-policy-what-catknows-will-read-and-what-it-wont)).
The test is not "is there a route?" but "can the logged-in user see this in
normal use of Skool?". Finding a way past a limit Skool drew on purpose is not
a bugfix task. Read it now, not after the feature.

1. Document it in `docs/API.md` first (URL, shape, JSON fields, quirks) —
   including any credential-class fields it carries (§6.6).
2. Add a method to `SkoolClient` in `client.py` (return raw JSON).
3. If callers want it flat, add a `normalize.*` function and a self-check assert.
4. **If the MCP tool can return the payload raw (a `raw=True` param, or a
   verbatim passthrough), send it through `_safe_raw()` — no exceptions.** New
   secret field names go in `normalize.SECRET_KEYS`, admin-side member data in
   `normalize.PRIVATE_KEYS`.
5. Keep API.md and the code in sync — the docs are the contract.
6. Add the tool to `workspaces/_shared/references/mcp-tools.md`. That cheatsheet
   is the only place the workspace agents learn what exists, so a tool missing
   there is a tool no agent will ever call. `python test_mcp_tools_doc.py`
   enforces it — it matches on word boundaries, because a plain substring check
   counts `get_post` as documented the moment `get_post_comments` is listed.
   The same test compares the tool counts (the two figures written as
   bold "N tools") in this file, in `README.md` and in the `catknows-update`
   skill with the running server, and checks that every write tool is named in
   this file: when you add a tool, update those three places too.

## Guardrails

- **Don't remove the rate-limit safeguards** (inter-page sleeps, 202 back-off).
  Skool will block aggressive clients and can put the user's account at risk.
- **Never leak account secrets in tool output.** Any Skool payload returned raw
  goes through `_safe_raw()` / `normalize.scrub` first (see the MCP-server
  section). This matters most for the planned Cloud MCP, where output crosses a
  network to a third party — treat scrub as load-bearing, not cosmetic.
- **Skool's security question is not an error to swallow.** HTTP 200 with a
  top-level `code` matching `AUTH-SEC-5xx` raises `SkoolSecurityChallenge`
  (a `SkoolHTTPError`): don't catch it with a broad `except`, don't retry it,
  and let a batch job stop on it (docs/API.md §3). The batch jobs
  (`pull_to_vault`, the CLI `pull`, `catknows.snapshot`) stop the same way on
  HTTP 401, 403 and 429 and on a 202 repeated until the retries are used up
  (`http.turned_away`); only other errors count as one failed entry.
- **Never commit `.skool-profile/`** — it holds the user's live session.
- **Pick the branch before you commit, not after.** `main` is the open-source
  side users pull via `update_catknows`; `hosted-mcp` adds HTTP transport, OAuth
  and `deploy/`. Anything generic — client fixes, new endpoints, the MCP server,
  tests, docs — belongs on `main` first and reaches `hosted-mcp` by merge. Only
  files that name `catknows.app` or the server's own structure are hosted-only.
  Committing generic work straight to `hosted-mcp` because that branch happens
  to be checked out means community users never get it; it happened on
  2026-08-17 and cost a cherry-pick plus a merge conflict to undo. Asking once
  before `git commit` is cheaper than repairing the history afterwards.
- This is not an official API; write code defensively (endpoints may 403/change).
- Personal data (member emails/names) is being exported — see [LEGAL.md](LEGAL.md).
- Anything user-facing you write (README copy, posts, About/event texts) follows
  [BRAND.md](BRAND.md) — naming canon, voice registers, the exact signature.
