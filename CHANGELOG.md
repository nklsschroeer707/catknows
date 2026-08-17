# Changelog

All notable user-facing changes. To update your install: `/catknows-update`
(Claude Code) or `git pull && pip install -e ".[mcp]"`, then restart your
MCP client — a long-running MCP server keeps old code until reconnected.

> **Posting an entry to the community?** This file may name functions and
> parameters; the changelog comment in catnose may not. It is written for
> people who never open this repo — see `BRAND.md` §"The changelog comment is
> written for users, not for developers".

## 2026-08-17

### Fixed
- **A missing membership row no longer poses as a founding-date join.**
  `metadata.member` carries both your role and your real join date. When it was
  absent, `joined_at` silently fell back to the community's founding date and
  `role` defaulted to `member`. Both read like facts and neither was measured,
  which is how a community you joined last year could report a 2019 join.
  `joined_at` is now `None` in that case and the record carries an `incomplete`
  note saying why. A gap is honest; a plausible wrong date hid this for two
  weeks. Owners keep a trustworthy role, it comes from `metadata.owner`.
  Measured over 26 live communities first: `metadata.member` is present and
  parseable in all 26, so this changes no real output. It closes the blind
  case, it does not fix a live miss. (Reported by Dan Schaad)

### Note
- **Roles are per account, so two people can measure the same tool and both be
  right.** `list_my_communities` reporting `member` for a community where
  someone else sees `group-admin` is not a bug when the two accounts hold
  different roles there. Verified live: `list_members` with the `admins` filter
  lists 8 admins/moderators of `hoomans`, and the account behind a
  contradicting measurement was simply not one of them. When a role looks
  wrong, compare against `list_members --filters admins` for the same account
  before assuming the parser is at fault.

## 2026-08-16

### Documentation
- **Attaching a file works differently on the hosted service, and now says so.**
  `create_post` reads attachment paths on the *server*, so a path on your own
  machine is invisible to the hosted deployment — the attachment silently never
  appears. Hosted users attach media in Skool's own editor instead; self-hosted
  users pass local paths as before. A `.gif` uploads and animates fine either
  way (verified live).
- **`DELETE /posts/{id}` is verified, not guessed.** docs/API.md §5.9 listed
  deleting a post under "not documented, confirm it yourself". It is measured
  now: empty 200, same header set as every other write, post and attachment
  gone from the feed immediately. Editing a post and casting a like remain
  unverified.
- **The `privacy` field on `about.json` does not tell you if a group is
  private.** It was documented as `1 = private, 2 = public`. Measured live, a
  public community and a paid private one both return `1` — and the same public
  community returned `1` and then `0` a few hours later. `get_community_about`
  still passes the field through, but nothing should read it as a privacy flag.

## 2026-08-15

### Fixed
- **A truncated member list now says so instead of looking complete.** Per
  community/role/session, Skool re-serves page 1 of the members list for
  every later page (measured live: the same fresh session walked a 592-member
  community fully but got page 1 repeated on a 5.5k community). The 14.08.
  dedupe stopped the duplicate rows, but the walk then ended silently at ~30
  members — a result that looked complete and wasn't, which is worse.
  `list_members` now appends a final `{"incomplete": true, ...}` entry (with
  pages served vs. total) whenever the walk ends early, and `pull_to_vault`
  reports `members_incomplete`. Regression test: `test_members_truncation.py`.
  (Reported by Dan Schaad)
- **`list_members` no longer walks every page of huge communities.** A
  `limit=25` call on a 160k-member community tried to fetch all 5000+ pages
  before slicing; the walk now stops as soon as `limit` unique members are
  collected.
- **The `t=active` story is resolved: Skool changed it server-side.** On
  2026-08-12 the parameter measured 404 for everyone, owners included; on
  2026-08-15 it measures 200 for everyone and returns exactly the default
  view. Both datasets were honest — Skool moved between them, which Dan
  Schaad's browser captures were the first to show. catknows keeps not
  sending it (it buys nothing and has flipped once already). The
  lifecycle/status/sort filter grammar works for regular members too;
  documented in docs/API.md §1.1 for a future filter interface.
- **401/403 errors now explain the real fix for both deployments.** The old
  message said "delete the profile dir", which hosted users can't do. It now
  points hosted users at reconnecting their Skool login and explains that the
  WAF token can go stale while the login itself is still valid.

### Added
- **`list_members` can filter and sort now** — the full Skool members query
  model, mapped and verified by Dan Schaad's report (2026-08-14) and measured
  live: `lifecycle` (active/cancelling/churned/banned), `sort`
  (newest/last_active/most_points), `filters` (comma-separated flags:
  admins, online, trials, monthly, annual, one_time, free — AND-combined),
  `tiers` (comma-separated, OR-combined) and `course_ids` (comma-separated,
  AND-combined; ids from `get_classroom`). Works for regular members, not
  just admins. Unknown values fail with a clear error instead of Skool's
  misleading 404. Note the Skool-native asymmetry: "annual OR free" needs
  two calls, merged by the caller. (Spec: Dan Schaad's PDF — thanks!)
- **Native polls** — `create_post` takes `poll_options` (2–10 comma-separated
  answers) and creates a real Skool poll widget on the post, no more "vote via
  comment" workarounds. Draft-first like every write: the poll is only created
  on Skool with `confirm=true`. Poll posts read back with their results too:
  normalized posts now carry `poll: [{option, votes}]`. Endpoint reference in
  [docs/API.md §5.5](docs/API.md); runnable proof: `test_poll_live.py`
  (verified live 2026-08-15).
- **Classroom management** — catknows can now build and maintain courses, not
  just read the tile list. New MCP tools: `get_course_tree` (the first way to
  read pages/modules at all — Skool's classroom payload only carries the
  tiles), and behind `CATKNOWS_ALLOW_WRITE=1`: `create_course`,
  `create_course_item` (folders and pages, plain text becomes Skool rich text
  automatically), `update_course_item`, `publish_course`, `move_course_item`
  and `delete_course_item`. All writes are draft-first (`confirm=true` to
  execute); new courses start as invisible drafts by default.
  The full endpoint reference — including the traps: Skool silently resets a
  course's `privacy` on partial updates (guarded here), deleting a folder
  *lifts* its pages instead of deleting them, and deleting a whole course
  needs an email-verified `client_id` — is documented in
  [docs/API.md §7](docs/API.md); the runnable proof is
  `test_classroom_live.py`.
- `get_classroom` now includes each course's `id` (needed by the tools above)
  and `is_draft` flag.

### Fixed
- **A comma inside a poll option silently split it in two.** `poll_options`,
  `attachments`, `labels` and `video_links` are comma-separated strings (MCP
  arguments are flat text), and the split had no escape: the option
  "Yes, the cat has served me" was posted as two separate options. Nothing
  complained, because three options are still inside the valid 2–10 range.
  A comma can now be escaped as `\,` in any of these arguments — plain
  `"a,b"` splits exactly as before, so no existing call changes. The
  option-count error now also prints the parsed list and names the escape,
  and the tool description says to check the draft's option list before
  confirming.
- `send_dm` with attachments died with `HTTP 400: invalid limit: 100` before
  sending anything: the internal channel lookup listed `/self/chat-channels`
  with `limit=100`, but Skool refuses anything above 30 (measured live: 30 ok,
  31 already fails) — and a one-shot listing could never find channels beyond
  its window anyway (accounts can hold hundreds). The group_id now comes off
  the channel itself via the messages endpoint: one call, works for any number
  of channels. Regression test: `test_send_dm_attachment_lookup.py`.
  (Reported by Dan Schaad)

## 2026-08-14

### Added
- **catknows as a hosted service**: [catknows.app](https://catknows.app) — sign
  up, confirm your address, connect Skool in a browser that runs on the server,
  and add `https://mcp.catknows.app/mcp` to your AI client. No install, no
  Python, no terminal. Sign-up is self-service; the local GitHub version stays
  free and needs no account at all.
- `read_dms`: read a whole DM conversation, not just the last line per channel.
  Skool's endpoint caps a single request at 50 messages and only goes further
  via a per-message cursor — catknows walks it for you. Verified on a real
  channel spanning ~21 months: 51 messages before, 248 after.
- **Attachments on writes**: `create_post`, `create_comment` and `send_dm` take
  an `attachments` list of local file paths. With `confirm=false` nothing is
  uploaded — the preview only states name, type and size, so a wrong path fails
  there instead of halfway through a confirmed post.

### Fixed
- `list_my_communities` reported `member` for every community, including the
  ones you run. Skool's payload has no `role` field at that level at all; the
  real membership row sits in `metadata.member` as a JSON string. Admin,
  moderator and owner now come through correctly.
- `list_my_communities` showed the community's **founding** date as your
  `joined_at`. Now it's your actual join date — a community founded in 2021 that
  you joined in 2024 says 2024.
- `list_members` returned duplicates instead of paginating: past member 30,
  Skool re-serves page 1 to non-admins, so a walk of 65 came back as 65 rows of
  which only ~30 were distinct. Members are now deduped by id, like posts.
  Fewer rows, all of them real.
- Role names differed per tool — `list_my_communities` said `admin` while
  `list_members` and `get_member_profile` said `group-admin`. All paths share
  one mapping now, so filtering for admins gives the same answer everywhere.

## 2026-08-10

### Added
- `get_discovery_rank` is back: your own community's true discovery
  standing — overall rank (works beyond the top-1000 board), category +
  category rank, visibility, language and growth-boost status. The old
  per-community endpoint was thought WAF-blocked; that block died with the
  TLS-fingerprint fix in August.
- `get_community_about` now decodes all five Skool pricing models —
  `free` / `paid` / `freemium` / `tiers` / `one_time` — instead of a bare
  number, and exposes a new `tiers` field (tier names + benefits) for
  freemium and tiered communities. Verified against all 1000 discovery-board
  communities; older groups without a model set safely return `null`.

### Fixed
- `get_discovery` returned `rank: 0` for every community — Skool stopped
  populating the rank field, so catknows now derives the ordinal from the
  page order (page 1 → 1–30, page 34 → 991–1000). Verified across pages
  1, 2 and 34. (Reported by Dan & Maya's clean-room diagnostic — thanks!)
- Freemium communities showed `membership_model: 3` with `price: null`,
  which looked broken. Cause: Skool's About payload never carries tier
  amounts for freemium — joining is free and the tiers ARE the pricing.
  The model is now labeled, tiers are listed, and for `tiers` communities
  the entry price comes through correctly.

## 2026-08-09

### Added
- **Updates from ANY AI client**: new `update_catknows` MCP tool — ask
  ChatGPT (or any connected AI) to update catknows; without `confirm` it
  only shows what's new, nothing changes. Same engine as the new
  `python -m catknows.update` one-liner. Both refuse to touch local
  changes and remind you to reconnect afterwards.
- **Agent workspace catalog** (`workspaces/`): 21 ready-to-use agents for
  Skool jobs — members, posts, classroom, research, calendar, reports
  (`read/`, look-only) and acting agents (`write/`: feedback triage → GitHub
  issues, publish a post, send a DM, report a bug). Every write agent
  enforces the WRITE RULE: nothing is sent, posted, or created until you
  approved the exact content. Start at `workspaces/START-HERE.md`; personal
  setup lives in gitignored `workspaces/_config/me.md`.
- **Vault librarian scaffold**: every new vault gets a `CLAUDE.md` with
  knowledge-base rules (source of truth, distill don't dump, append-only).
- **Trend snapshots**: `python -m catknows.snapshot --vault ./vault <slugs>
  [--discovery]` appends dated member/activity numbers to
  `<vault>/trends/*.jsonl` — headless, scheduler-friendly, your growth
  curves build themselves.
- **Speed**: in-process read cache (repeat calls ~instant; writes invalidate
  it; tune via `CATKNOWS_CACHE_TTL`), compact `get_classroom` (−98% payload),
  `CATKNOWS_PAGE_DELAY` knob for paginated pulls.

### Fixed
- `get_post_comments` returned blank `text` and `created_at: null` — the
  comment body lives in `metadata.content`, timestamps are ISO strings.
  (Reported by Dan — thanks!)
- `list_posts` dropped post title and body (only the URL slug survived);
  vault notes now carry real titles and full post text.

## 2026-08-08

### Fixed
- **403/WAF rejection on comments and likes**: AWS WAF fingerprints the TLS
  handshake itself, so headers alone can never pass. catknows now sends a
  real Chrome TLS handshake via `curl_cffi`. (Reported by Dan.)
- **Posts capped at 32 with duplicates**: the feed hides its page count;
  catknows now walks every page and dedupes by post id — verified complete
  on communities with 700+ and 1,300+ posts. (Reported by Dan.)

## 2026-08-07

### Added
- **MCP server**: catknows becomes the bridge between Skool and any
  MCP-capable AI (Claude, ChatGPT, Cursor, …). 13 read tools; write tools
  (post, DM) exist only with `CATKNOWS_ALLOW_WRITE=1` and draft first.
- `/catknows-update` skill: pull, reinstall, health-check in one command.

### Fixed
- Member points were always 0 (wrong field) — rankings work now.
- Oversized responses errored instead of returning data — results are
  capped and compact.
- Discovery leaderboard 403 — rerouted to the working top-1000 endpoint.
- First-login hang; clearer "not a member" error instead of a bare 404.
