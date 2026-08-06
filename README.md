# skoolapi

**Pull your own Skool community data into Obsidian, a plain folder, or any CRM — in one command.**

Skool has no public API. This is a small, documented, open-source client for
Skool's *private* internal API (the same endpoints skool.com's own site uses).
You log in with your normal Skool account in a real browser window, and the tool
pulls your community's members, posts, comments and likes into clean Markdown
notes you can do anything with.

- 📖 **[Full API reference →](docs/API.md)** — every endpoint, header, and JSON
  field documented. Hand it to Claude/Codex and it can build a client in any language.
- 🧩 **Works from a GitHub link** — clone, install, run. No account or key with us.
- 🗂️ **Obsidian-native output** — Markdown notes with YAML frontmatter, ready for
  Dataview or an AI "librarian" to sort and cross-link.

> ⚠️ **Unofficial & unstable.** This talks to Skool's undocumented endpoints by
> driving your own logged-in browser session. It is not affiliated with Skool
> and can break when they change their site. Use it on communities you have
> access to, and respect [Skool's ToS](LEGAL.md).

---

## Quick start (3 commands)

```bash
git clone https://github.com/<you>/skoolapi.git
cd skoolapi
pip install -e . && playwright install chromium
```

Then pull a community into a vault:

```bash
python -m skoolapi pull your-community-slug --vault ./MyVault
```

A browser window opens **the first time** — log in to Skool normally (email or
Google/Apple). The window closes itself once you're signed in, and your session
is remembered for next time. Output lands in `./MyVault/your-community-slug/`.

The slug is the bit in the URL: `skool.com/`**`your-community-slug`**.

### For an AI agent (Claude / Codex)

Point it at this repo and say:

> "Read `docs/API.md` and `AGENTS.md`, then pull my Skool community `<slug>`
> into `./vault` using this library."

Everything it needs — endpoints, auth, quirks, the runnable CLI — is in the docs.

---

## Use it as a library

```python
from skoolapi import SkoolClient, login
from skoolapi import normalize

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

## No-browser mode

Don't want Playwright? Copy your `Cookie` header from DevTools (logged-in
skool.com → Network tab → any request → Request Headers → `cookie`) and:

```bash
python -m skoolapi pull my-community --vault ./MyVault --cookie "auth_token=...; aws-waf-token=..."
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
(owner only) · chat channels & messages. Full list and JSON shapes in
**[docs/API.md](docs/API.md)**.

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

## License

MIT. See [LICENSE](LICENSE) and [LEGAL.md](LEGAL.md) for the ToS caveats.
