<div align="center">

<img src="docs/assets/logo-mark.png" alt="catknows" width="150">

# `catknows.`

### ▸ get your own Skool data out — free & open source ◂

```text
$ catknows pull your-community --vault ./MyVault
  ↳ members · posts · comments · likes · DMs · leaderboards → your Obsidian vault ✓
  no subscription · no middleman · you own it
  the foundation — you build the rest.
  You have been served by catknows. — you are welcome.
```

`#skool` &nbsp;·&nbsp; `#open-source` &nbsp;·&nbsp; `#free-forever` &nbsp;·&nbsp; `#own-your-data` &nbsp;·&nbsp; `#build-it-yourself`

[![License: MIT](https://img.shields.io/badge/License-MIT-22c55e.svg?style=flat-square)](LICENSE)
[![Price](https://img.shields.io/badge/price-%240%20forever-22c55e.svg?style=flat-square)](#why-its-free)
[![Python](https://img.shields.io/badge/python-3.10+-3776AB.svg?style=flat-square&logo=python&logoColor=white)](pyproject.toml)
[![Playwright](https://img.shields.io/badge/playwright-driven-2EAD33.svg?style=flat-square&logo=playwright&logoColor=white)](https://playwright.dev)
[![Platform](https://img.shields.io/badge/macOS%20·%20Linux%20·%20Windows-lightgrey.svg?style=flat-square)](#quick-start)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-ff69b4.svg?style=flat-square)](#contributing)
[![Skool community](https://img.shields.io/badge/Skool-visit%20us%20here-DF1A1A.svg?style=flat-square)](https://www.skool.com/catnose/about)

**Stop paying to read your own Skool data. Pull it yourself — members, posts, comments, DMs, the discovery leaderboard — into a clean Obsidian vault. The foundation; what you build on top is up to you (and the community). Free, open source, yours.**

</div>

You're running a Skool community and you want your data — who's engaged, who's
fading, what's happening. So the pitch goes: pay **Skoot, Skooly, Panda,
SkoolKit, Wingman** (or whatever's next) a monthly fee, just to have someone else
pull and hand **your own data** back to you.

catknows says: don't. Skool has no public API, but its site talks to a private
one — the same endpoints skool.com uses. catknows is a documented, open-source
client for exactly those endpoints. Log in with your normal Skool account in a
real browser, and pull your community's data into a clean Obsidian vault (or raw
JSON for anything else). **No subscription. No middleman. You own it.**

Today catknows does one thing well: **it gets your data out.** That's the
foundation — the reports, automations, and dashboards you'd normally rent are
now things *you* can build on top of it. And because it's open source, where
catknows goes next is decided by the people who use it, not a pricing page.

- 🆓 **Free forever, open source (MIT).** No trial, no seat limits, no locked endpoints. See [Why it's free](#why-its-free).
- 🧱 **The foundation, not a walled garden.** It hands you your data in the open — you (and the community) decide what gets built on it.
- 🧑‍💻 **For everyone.** Can drive an AI with two left hands? You can use catknows. Can code a little? Even better — take the data and build whatever you want.
- 📖 **[Full API reference →](docs/API.md)** — every endpoint, header, and JSON field documented. Hand it to Claude/Codex and it builds a client in any language.
- 🗂️ **Obsidian-native output** — clean Markdown + YAML frontmatter out of the box, or take the raw JSON into Notion, a CSV, a spreadsheet, your own tooling.

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
query it. From there, an AI librarian in your vault can file, tag, and link
everything however you like.

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
