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

- Read tools (always on): members, posts, comments, likes, member profile,
  community about, discovery, admin metrics, calendar, classroom, chat channels,
  and `pull_to_vault`.
- Write tools (`create_post`, `send_dm`): **only registered when
  `CATKNOWS_ALLOW_WRITE=1`**. They're draft-first — `confirm=false` returns the
  draft without posting; `confirm=true` actually writes. `notify_members` (email
  broadcast) is a separate explicit flag. Never weaken this: writes act as the
  user, visible to real members.
- Install/run: `pip install -e ".[mcp]"` then `python -m catknows.mcp_server`
  (stdio). Register with `claude mcp add catknows -- python -m catknows.mcp_server`,
  or a project `.mcp.json` (gitignored — holds machine paths).
- **Transports:** stdio by default; `CATKNOWS_HTTP=1` serves streamable HTTP at
  `/mcp` instead (`CATKNOWS_HOST`/`CATKNOWS_PORT`, default `127.0.0.1:8000`).
  SSE is deprecated — don't add it. The switch lives in `main()` and hands the
  work to the SDK's `run(transport=...)`; no ASGI scaffolding of our own until
  auth needs middleware, then `streamable_http_app()` is the hook. Verify with
  `python -m catknows.mcp_server --self-check` (no port bound) and the MCP
  Inspector. **The HTTP transport has no auth yet** — it binds loopback, and
  that default is load-bearing: `0.0.0.0` would expose a live Skool session.
  Auth (OAuth 2.1/PKCE via AuthKit) and multi-tenancy are Phase 2, see
  [docs/HOSTED_MCP_PLAN.md](docs/HOSTED_MCP_PLAN.md).
- Login in HTTP mode can't fall back to a visible browser window (no display on
  a server) — an expired session raises instead, pointing at `CATKNOWS_COOKIE`
  or a pre-seeded profile. Don't "fix" that by re-enabling the window.
- `stdout` is the protocol channel — `login()`'s prints are redirected to stderr.
  Anything a tool prints to stdout would corrupt the stream.
- **Size cap:** tool results have a max token size. `list_members`/`list_posts`
  hard-cap their `limit` (`_cap()`, raw capped harder); `get_community_about` and
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
  scrub list lives in one place: `normalize.SECRET_KEYS` — add a field name
  there, never a one-off `pop()` at a call site. `normalize.scrub` mutates in
  place; that's safe because the HTTP cache stores/returns `copy.deepcopy`
  (never hand a scrubbed object back into the cache).
- **Points quirk:** member points/level come from `metadata.spData` (a JSON
  string), NOT `member.metadata.points` (always 0). Handled in `normalize.member`.
- **Discovery:** `get_discovery` uses the Next.js `discovery.json` board (global
  top-1000, paged). The api2 `/groups/{gid}/discovery` endpoint is WAF-blocked (403).
- **Gated 404:** member/post data is members-only. If the logged-in account isn't
  in the community, Skool returns `{"notFound":true}` → we raise a clear "not a
  member" error. `about`/`discovery` are public and work without membership.
- Hosting catknows as a product (hosted endpoint, OAuth, multi-tenant, streamed
  Skool login) is the plan in [docs/HOSTED_MCP_PLAN.md](docs/HOSTED_MCP_PLAN.md)
  — it supersedes the older [docs/MOBILE_MCP_PLAN.md](docs/MOBILE_MCP_PLAN.md).
  The transport (Phase 1) is built; everything above it is not.

## To add a new endpoint

1. Document it in `docs/API.md` first (URL, shape, JSON fields, quirks) —
   including any credential-class fields it carries (§6.6).
2. Add a method to `SkoolClient` in `client.py` (return raw JSON).
3. If callers want it flat, add a `normalize.*` function and a self-check assert.
4. **If the MCP tool can return the payload raw (a `raw=True` param, or a
   verbatim passthrough), send it through `_safe_raw()` — no exceptions.** New
   secret field names go in `normalize.SECRET_KEYS`.
5. Keep API.md and the code in sync — the docs are the contract.

## Guardrails

- **Don't remove the rate-limit safeguards** (inter-page sleeps, 202 back-off).
  Skool will block aggressive clients and can put the user's account at risk.
- **Never leak account secrets in tool output.** Any Skool payload returned raw
  goes through `_safe_raw()` / `normalize.scrub` first (see the MCP-server
  section). This matters most for the planned Cloud MCP, where output crosses a
  network to a third party — treat scrub as load-bearing, not cosmetic.
- **Never commit `.skool-profile/`** — it holds the user's live session.
- This is not an official API; write code defensively (endpoints may 403/change).
- Personal data (member emails/names) is being exported — see [LEGAL.md](LEGAL.md).
- Anything user-facing you write (README copy, posts, About/event texts) follows
  [BRAND.md](BRAND.md) — naming canon, voice registers, the exact signature.
