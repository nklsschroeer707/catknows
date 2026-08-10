# Changelog

All notable user-facing changes. To update your install: `/catknows-update`
(Claude Code) or `git pull && pip install -e ".[mcp]"`, then restart your
MCP client — a long-running MCP server keeps old code until reconnected.

## 2026-08-10

### Added
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
