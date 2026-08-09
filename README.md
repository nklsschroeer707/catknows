<div align="center">

<img src="docs/assets/logo-mark.png" alt="catknows" width="150">

# `catknows.`

### ▸ the free, open-source bridge between Skool and any AI ◂

```text
Skool has no API. catknows is the one.
  members · posts · comments · likes · DMs · leaderboards
  → straight into Claude, ChatGPT, Notion, a vault, or raw JSON
  now you can finally talk to your own data.
  no subscription · no middleman · you own it.
  You have been served by catknows. — you are welcome.
```

`#skool` &nbsp;·&nbsp; `#mcp` &nbsp;·&nbsp; `#open-source` &nbsp;·&nbsp; `#free-forever` &nbsp;·&nbsp; `#own-your-data`

[![License: MIT](https://img.shields.io/badge/License-MIT-22c55e.svg?style=flat-square)](LICENSE)
[![Price](https://img.shields.io/badge/price-%240%20forever-22c55e.svg?style=flat-square)](#why-its-free)
[![Python](https://img.shields.io/badge/python-3.10+-3776AB.svg?style=flat-square&logo=python&logoColor=white)](pyproject.toml)
[![Playwright](https://img.shields.io/badge/playwright-driven-2EAD33.svg?style=flat-square&logo=playwright&logoColor=white)](https://playwright.dev)
[![Platform](https://img.shields.io/badge/macOS%20·%20Linux%20·%20Windows-lightgrey.svg?style=flat-square)](#quick-start)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-ff69b4.svg?style=flat-square)](#contributing)
[![Skool community](https://img.shields.io/badge/Skool-visit%20us%20here-DF1A1A.svg?style=flat-square)](https://www.skool.com/catnose/about)

**Stop paying to read your own Skool data. catknows is the bridge between Skool and every AI tool — pull your members, posts, comments, DMs and the discovery leaderboard straight into Claude, ChatGPT, Notion, a clean Obsidian vault, or raw JSON. Now you can finally talk to your own data. Free, open source, yours.**

<br>

### ▸ [**Join the community → skool.com/catnose**](https://www.skool.com/catnose/about) ◂

*You're not a customer here — you're a co-builder. Ask, request features, show what you built.* 🐈

</div>

## 🐈 catknows, explained like you're 5

You have a clubhouse on Skool. 🏠 Lots of friends, lots of talking, lots of stuff happening every day.

But looking through all of it yourself? Boring. Slow. Too much.

So you get a **cat.** 🐈 The cat has a *super* good nose.

You say: *"Cat, who came to my clubhouse today?"* The cat runs in, sniffs everything, runs back — and tells your robot friend (Claude, ChatGPT, Notion…) the answer. Fast. 🐾

**Three things. That's it:**

1. 🔌 **Plug the cat in.** One time. Like a lamp.
2. 🗣️ **Ask the cat stuff.** Normal words — *"Who's new?" "What did everyone like?" "Who's leaving?"*
3. 📎 **The cat brings it back.** Neat and tidy. And it remembers — so it gets smarter every day.

The cat also **hides your secret stuff** (money things, passwords) so no one bad ever sees it. 🛡️

It costs **nothing.** 🆓 Only *you* see your things. The cat works for you, in your own house, forever.

*You have been served. 🐈*

<br>

*(Want the grown-up version? Keep reading. 👇)*

You're running a Skool community and you want your data — who's engaged, who's
fading, what's happening. So the pitch goes: pay **Skoot, Skooly, Panda,
SkoolKit, Wingman** (or whatever's next) a monthly fee, just to have someone else
pull and hand **your own data** back to you.

catknows says: don't. Skool has no public API, but its site talks to a private
one — the same endpoints skool.com uses. **catknows is the bridge to exactly
those endpoints.** Log in with your normal Skool account, and it hands your data
to whatever you already use: through [MCP](#plug-it-into-your-ai-mcp) it plugs
straight into **Claude, ChatGPT, Notion, Cursor** and any other AI tool, so you
can *talk* to your community data — or dump it into a clean Obsidian vault or raw
JSON for anything else. **No subscription. No middleman. You own it.**

catknows is the **foundation, not a finished product** — the reports,
automations, and dashboards you'd normally rent are now things *you* build on
top of it. And because it's open source, where catknows goes next is decided by
the people who use it, not a pricing page.

- 🧠 **Talk to your own data in the AI you already use.** MCP server for Claude, ChatGPT, Notion, Cursor & co. — [plug it in](#plug-it-into-your-ai-mcp).
- 🤖 **21 ready-made agent jobs.** [`workspaces/`](workspaces/START-HERE.md) — every job is a folder your AI can run: member lists, digests, fan rankings, classroom research, backups, one big report. Acting jobs (posting, DMs, issues) never fire without your explicit approval.
- 🆓 **Free forever, open source (MIT).** No trial, no seat limits, no locked endpoints. See [Why it's free](#why-its-free).
- 🔒 **Runs on your machine, with your login.** Your data never leaves your house — there's no "us" server in the loop.
- 🛡️ **Secrets never leak into your AI.** Skool's payloads secretly carry credential-class fields — Zapier keys, Stripe payout ids, billing details. catknows scrubs every one of them out before any data reaches your AI or your disk.
- 🗂️ **Or take the raw data anywhere** — clean Obsidian vault, raw JSON, a CSV, a spreadsheet, your own tooling.
- 📖 **[Full API reference →](docs/API.md)** — every endpoint, header, and JSON field documented. Hand it to Claude/Codex and it builds a client in any language.
- 📜 **[Changelog →](CHANGELOG.md)** — what shipped, in plain words.

> ⚠️ **Unofficial & unstable.** This talks to Skool's undocumented endpoints by
> driving your own logged-in browser session. It is not affiliated with Skool
> and can break when they change their site. Use it on communities you have
> access to, and respect [Skool's ToS](LEGAL.md).

---

## Quick start

Needs **Python 3.10+** and **git**. Works on macOS, Linux, and Windows.

**macOS / Linux**

```bash
git clone https://github.com/nklsschroeer707/catknows.git
cd catknows
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
playwright install chromium          # on Linux: playwright install --with-deps chromium
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/nklsschroeer707/catknows.git
cd catknows
py -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e .
playwright install chromium
```

> **Why the venv?** Recent macOS (Homebrew) and Linux (Debian/Ubuntu/Fedora)
> ship an "externally managed" Python that refuses a bare `pip install`
> ([PEP 668](https://peps.python.org/pep-0668/)). The virtual environment above
> is the portable fix and keeps this tool's deps off your system Python.
> On Linux, `--with-deps` also pulls the OS libraries Chromium needs (otherwise
> run `sudo playwright install-deps` once).

Then pull a community into a vault (with the venv active):

```bash
python -m catknows pull your-community-slug --vault ./MyVault
```

A browser window opens **the first time** — log in to Skool normally (email or
Google/Apple). The window closes itself once you're signed in, and your session
is remembered for next time. Output lands in `./MyVault/your-community-slug/`.

The slug is the bit in the URL: `skool.com/`**`your-community-slug`**.

> **No display (SSH / headless server / CI)?** The first-run browser login needs
> a screen. On a headless box, skip it with [No-browser mode](#no-browser-mode)
> below — paste a `Cookie` header and pass `--cookie`.

### For an AI agent (Claude / Codex)

Point it at this repo and say:

> "Read `docs/API.md` and `AGENTS.md`, then pull my Skool community `<slug>`
> into `./vault` using this library."

Everything it needs — endpoints, auth, quirks, the runnable CLI — is in the docs.

---

## Use it as a library

```python
from catknows import SkoolClient, login
from catknows import normalize

client = SkoolClient(login())          # opens browser first time, reuses after

for user in client.members("my-community"):
    m = normalize.member(user)         # flat, clean record with real datetimes
    print(m["name"], m["role"], m["points"])

posts = client.posts("my-community")
gid   = client.group_id_for("my-community")
tree  = client.comments(posts[0]["post"]["id"], gid)
```

Every method returns the **raw Skool JSON** (nothing hidden), and `normalize.*`
flattens it into tidy records. See [`examples/quickstart.py`](examples/quickstart.py).

---

## Plug it into your AI (MCP)

catknows ships an [MCP](https://modelcontextprotocol.io) server — the standard
that lets Claude Desktop, Claude Code, Cursor & co. use external tools. One
registration, and your AI can query members, posts, comments, DMs, leaderboards,
or pull the whole vault — you just ask in plain language.

```bash
pip install -e ".[mcp]"
claude mcp add catknows -- python -m catknows.mcp_server   # Claude Code
```

For other MCP clients, register `python -m catknows.mcp_server` as a stdio
server (in Claude Desktop: Settings → Developer → Edit Config). On the first
tool call a browser window opens once for the Skool login; after that the
session is reused silently. Headless machine? Set the `CATKNOWS_COOKIE` env var
to your Cookie header instead.

Then just talk to your AI:

> "Pull my community `my-slug` into a vault" · "Who are my 10 most active
> members?" · "Send this week's new posts to my Notion" (via your Notion
> connector — your AI is the router, catknows is the data tap.)

**Writing (posting & DMs) is off by default.** The server only registers
`create_post` and `send_dm` when you opt in with an env var in the MCP config:

```json
"catknows": {
  "command": "python", "args": ["-m", "catknows.mcp_server"],
  "env": { "CATKNOWS_ALLOW_WRITE": "1" }
}
```

Without the flag your AI can't even see the write tools. With it, three guards
remain: your MCP client asks permission before every tool call, the tools are
draft-first (the AI must show you the draft, then call again with
`confirm=true`), and emailing all members (`notify_members`) is a separate
explicit switch. Test in a private community first — these post as *you*.

---

## Keeping it updated

Three doors, one engine — pick whichever fits your setup:

- **Any AI, no terminal:** tell your connected AI *"update catknows"* — the
  `update_catknows` MCP tool first shows what's new (nothing changes), and
  installs only when you confirm.
- **Claude Code:** `/catknows-update`.
- **Terminal:** `python -m catknows.update`.

All three refuse to touch local changes and only fast-forward. **Afterwards,
reconnect your AI client once** — a running MCP server keeps the old code
loaded until it restarts.

---

## No-browser mode

Don't want Playwright? Copy your `Cookie` header from DevTools (logged-in
skool.com → Network tab → any request → Request Headers → `cookie`) and:

```bash
python -m catknows pull my-community --vault ./MyVault --cookie "auth_token=...; aws-waf-token=..."
```

You'll re-copy it whenever the session expires. The browser flow is smoother for
regular use because it refreshes the WAF token automatically.

---

## What you get

```
MyVault/
└── your-community-slug/
    ├── members/
    │   ├── Ada Lovelace.md      # frontmatter: role, points, last_active, ...
    │   └── ...
    └── posts/
        ├── Welcome to the community.md   # post + its comments inline
        └── ...
```

Each note has YAML frontmatter so Obsidian Dataview (or your own tooling) can
query it. Every new vault also gets a `CLAUDE.md` with librarian rules baked
in — open the vault with any AI and it knows how to file, link, and grow your
knowledge (research notes in `research/`, never touching the mirror). And
`python -m catknows.snapshot --vault ./MyVault my-slug` appends your
community's numbers to `trends/*.jsonl` — run it on a schedule and your
growth curves build themselves.

---

## What can it pull?

Members · posts · comments (full nested threads) · likes/upvoters · single-member
profiles · community About · calendar · classroom · discovery · admin metrics
(owner only) · chat channels & messages · your communities & roles · pending
join requests (with the applicants' survey answers). Full list and JSON shapes
in **[docs/API.md](docs/API.md)**.

**Market research from the discovery pages** ([docs/API.md §6](docs/API.md#6-discovery-the-global-leaderboard--scraping-any-community)):
the global **revenue leaderboard** (top 100 communities with real monthly
recurring revenue), the top-1000 board, and — for *any* community without
joining — its price, plan, size, active plugins, and owner. Plus any member's
public profile stats.

The reference also documents Skool's **write** endpoints — creating posts (with
GIFs, images, videos, polls, attachments, category labels, and email broadcast)
and sending DMs — in [docs/API.md §5](docs/API.md#5-writing-to-skool-posts-polls-gifs-images-videos-dms).
The client implements the two everyday ones: `create_post` and `send_dm`
(plain posts with optional category label and video link; the rich-media
upload flows stay documented for you to build on). **Write carefully** — these
act as *you*, visible to real members; see the MCP section for the safety
switch.

---

## Why it's free

Paid Skool tools charge you a monthly fee to hand back data that was already
yours. catknows removes the fee and the middleman — it just gives you the access:

- **$0, forever.** No trial that expires, no seat limits, no "premium" endpoints
  held back. Everything in this repo is MIT-licensed and free to use, fork, and
  ship.
- **Your data stays yours.** It runs on *your* machine with *your* login. Nothing
  is sent to us — there's no "us" server in the loop at all.
- **You decide where it goes.** Today it pulls your data into a vault. Whether it
  grows churn reports, outreach drafts, dashboards — or stays a clean data tap —
  is up to the people who use it. File an issue, open a PR, or say what you need
  in the [Skool community](https://www.skool.com/catnose/about). No pricing page
  decides for you.

If catknows saves you a subscription and you *want* to buy the maintainers a
coffee (or a bagel 🥯), the door's open — but it is never required. Use it free,
no strings.

---

## How it works (30 seconds)

1. **Login** — a real browser (Playwright) so we can read the `httpOnly`
   `auth_token` cookie and let the AWS-WAF challenge solve itself.
2. **Discover** — grab the Next.js `buildId` from the community page; the group
   UUID falls out of the first posts response.
3. **Fetch** — call the documented endpoints with browser-faithful headers so
   AWS-WAF lets us through; walk pagination cursors for members/posts/comments.
4. **Normalize** — flatten the nested JSON, fix the nanosecond/microsecond
   timestamps and snake/camelCase mismatches.
5. **Write** — one Markdown note per member/post with frontmatter.

Details, including why a browser instead of a raw HTTP client, are in
[docs/API.md §0](docs/API.md#0-the-two-things-that-make-it-work).

---

## Contributing

PRs welcome — new endpoints, other output targets (Notion, CSV, a CRM),
other languages ported from [docs/API.md](docs/API.md). The API reference is the
source of truth; keep it in sync with the code.

## Community

catknows is built in the open, and where it goes next is decided by the people
who use it. The Skool community is the workshop: ask questions, request features,
show what you built on top of your data, or help shape the roadmap. You're not a
customer here — you're a co-builder.

### ▸ [**Visit us here → skool.com/catnose**](https://www.skool.com/catnose/about) ◂

## License

MIT. See [LICENSE](LICENSE) and [LEGAL.md](LEGAL.md) for the ToS caveats.

<div align="center">

*You have been served by catknows. — you are welcome.*

</div>
