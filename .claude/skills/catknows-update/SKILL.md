---
name: catknows-update
description: >
  Bring the local catknows install up to date: pull the latest code from GitHub,
  reinstall so new dependencies land (e.g. curl_cffi), and run a quick health check.
  Use when the user says "catknows-update", "/catknows-update", "update catknows",
  "get catknows on the latest version", or asks to refresh/upgrade their catknows install.
---

# catknows-update

Bring the user's local catknows to the latest state and verify it still works.
Run these steps **in order**, from the repo root. Report what changed; stop and
surface any problem instead of pushing through it.

## 0. Locate the repo

The catknows repo is the one containing `catknows/mcp_server.py` and `pyproject.toml`
(the local folder is usually `skool-api`, the GitHub repo is `catknows`). `cd` there.
If you can't find it, ask the user for the path.

## 1. Guard local work before pulling

```bash
git status --short
```

- **Uncommitted changes present** → do NOT discard them. Tell the user what's
  modified and ask whether to stash (`git stash`), commit, or abort. Never blow
  away their work to force an update.
- **Clean** → continue.

Also note the current commit so you can report the delta:
```bash
git rev-parse --short HEAD
```

## 2. Pull the latest

```bash
git fetch origin
git log --oneline HEAD..origin/main   # what's new (may be empty = already current)
```

- **Nothing new** → say "already on the latest version" and skip to the health
  check (step 4) so the user still gets a green light. Don't reinstall for nothing.
- **New commits** → fast-forward:
  ```bash
  git pull --ff-only origin main
  ```
  If the pull is rejected for divergence (local commits that aren't on origin),
  stop and show the user `git log --oneline origin/main..HEAD` — let them decide
  (rebase / merge). Don't force.

Summarize the new commits in one or two lines so the user knows what they got.

## 3. Reinstall (new dependencies land here)

Updates can add dependencies — the 2026-08-08 fix added **`curl_cffi`** (needed for
the api2 TLS handshake), and a plain `git pull` alone would leave the install broken
with a 403 on comments/likes. So always reinstall after a code change.

Find the environment (auto-detect, don't assume):

- If `.venv/` exists in the repo, use its Python:
  `.venv/Scripts/python.exe` on Windows, `.venv/bin/python` on macOS/Linux.
- Otherwise use the active `python` / `python3` on PATH.

Then reinstall with the MCP extra so the server deps are present:
```bash
<python> -m pip install -e ".[mcp]"
```
If that errors (e.g. externally-managed Python, PEP 668), tell the user to activate
their venv first — don't silently fall back to a system install.

## 4. Health check

Confirm the package imports and the MCP tools register:
```bash
<python> -c "import catknows, catknows.mcp_server as m, asyncio; \
print('catknows', catknows.__version__); \
print('tools:', len(asyncio.run(m.mcp.list_tools())))"
```
Also run the pure-stdlib self-check (no network, fast):
```bash
<python> -m catknows.normalize
```
Expect a version line, a tool count (13 read-only, or 15 with
`CATKNOWS_ALLOW_WRITE=1`), and `normalize self-check OK`. Any traceback → report it
verbatim and stop; the update left something broken.

## 5. Remind about the MCP client restart

**Critical and easy to miss:** a running MCP server process (Claude Desktop, Claude
Code, Cursor) keeps the OLD code loaded — an in-place update does NOT reach it until
the client restarts. End by telling the user plainly:

> Updated to <new commit>. Reload your editor / restart your AI client so the MCP
> server picks up the new code — otherwise it keeps running the old version.

## Notes

- This updates a **local** install. It does not touch the hosted cat-knows.com
  server (that's a separate repo + deploy).
- Read-only by default; never runs the Skool login or any write tool as part of an update.
