# AGENTS.md — for AI agents working in this repo

You're an AI (Claude, Codex, …) asked to pull Skool data or extend this client.
Start here.

## What this repo is

An unofficial Python client for Skool's private API. It logs in via a real
browser (Playwright), calls Skool's undocumented endpoints with WAF-faithful
headers, and writes the results as Obsidian Markdown.

## Read these first, in order

1. **[docs/API.md](docs/API.md)** — the complete endpoint reference. This is the
   source of truth. Every endpoint, header, auth detail, and JSON field is here,
   including the two hard parts (§0): the `httpOnly` auth cookie and the AWS-WAF
   headers. **Do not guess field paths — they're all documented.**
2. **[skoolapi/http.py](skoolapi/http.py)** — the request layer (WAF headers,
   buildId discovery, retries). The working implementation of API.md §0.
3. **[skoolapi/client.py](skoolapi/client.py)** — one method per endpoint,
   pagination handled.
4. **[skoolapi/normalize.py](skoolapi/normalize.py)** — the quirks (ns/µs
   timestamps, snake/camelCase). Has a runnable self-check: `python -m skoolapi.normalize`.

## To fulfil "pull my community into a vault"

The whole flow already exists — don't rebuild it:

```bash
pip install -e . && playwright install chromium
python -m skoolapi pull <slug> --vault ./vault
```

The first run opens a browser for login. If a browser can't be shown (headless
environment), ask the user for their Skool `Cookie` header and use
`--cookie "..."` instead.

## To add a new endpoint

1. Document it in `docs/API.md` first (URL, shape, JSON fields, quirks).
2. Add a method to `SkoolClient` in `client.py` (return raw JSON).
3. If callers want it flat, add a `normalize.*` function and a self-check assert.
4. Keep API.md and the code in sync — the docs are the contract.

## Guardrails

- **Don't remove the rate-limit safeguards** (inter-page sleeps, 202 back-off).
  Skool will block aggressive clients and can put the user's account at risk.
- **Never commit `.skool-profile/`** — it holds the user's live session.
- This is not an official API; write code defensively (endpoints may 403/change).
- Personal data (member emails/names) is being exported — see [LEGAL.md](LEGAL.md).
