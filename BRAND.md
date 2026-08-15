# BRAND.md — the catknows canon

One page. Anything user-facing — README copy, About texts, Skool posts, event
descriptions, release notes — follows this file. Agents writing public-facing
text: read this first.

## The one line

> **catknows. — Your Skool data, in your AI.**
> Free, open source, runs on your machine. You own it.

Use it verbatim on every first-contact surface (README H1, GitHub description,
About page, package description). Message hierarchy, in this order:

1. **Benefit** — your data, in the AI you already use ("talk to your own data").
2. **Free** — always anchored against the rent-seekers ("Stop paying to read
   your own Skool data"), never free-floating: unanchored "free" reads cheap.
3. **Local & ownership** — your machine, your login, no middleman server.
4. **Open source** — the *proof* of 2 + 3, not a separate selling point.

For non-technical readers, prefer "private" over "open source" and "the AI you
already use" over "your AI".

## Naming

| Name | Use |
|---|---|
| `catknows` | prose, code, package, CLI — always lowercase |
| `catknows.` (with dot) | display wordmark only: logo, H1, About display name |
| `catnose` | the Skool community slug, nothing else — it's the cat's *nose*; explain the pun once per surface ("the community lives at the cat's nose") |
| `catknews.` | the news format (weekly call, news posts) — the only spelling |

Never: `CatKnows`, `Catknows`, `catknwes`, `cat-knows-1423`, `cat-knows.com`.
The hyphenated domain is legacy and points at nothing — catknows lives on
GitHub and at skool.com/catnose.

## The signature

The signature is the cat, alone, on its own closing line:

> ᓚᘏᗢ

- No wordmark line above it. The cat signs off; nothing needs to say so.
- It is the *only* brand voice allowed inside reference docs and code (module
  docstrings, CLI success output). Everything else there stays sober.

**Retired 2026-08-14: "You have been served by catknows. — you are welcome."**
Dan Schaad flagged it from the English side: "You have been served" reads as
*being sued* or *being beaten*, and paired with "you are welcome" it lands
aggressive or sarcastic — the opposite of the German "Sie wurden bedient" it
was translating. Not a phrasing tweak; the phrase carries a meaning we can't
overwrite. The short form "You have been served. 🐈" is retired with it.

## Voice registers

- **First-contact** (README header + ELI5, About page *including tiers*,
  anything a stranger sees before trusting catknows with a Skool login):
  warm, clear, typo-free, no profanity. Inge-safe.
- **Community interior** (posts, comments, events inside catnose): bffr —
  "hoomans", "pawsome", 😼/🐾 welcome.
- **Reference** (API.md, AGENTS.md, code): sober and precise. No cat voice.

### The changelog comment is written for users, not for developers

`CHANGELOG.md` in the repo and the changelog comment in catnose are **two
different texts for two different readers.** The file may name functions,
parameters and commits. The comment may not — it is read by people who never
open the repo, and it has to make sense to someone who does not know what an
endpoint is.

Rules for the Skool comment:

- **Lead with what the reader can now do**, not with what was built. "The cat
  can build your classroom now" — not "added `create_course_item`".
- **A fix describes the symptom the user had.** "Sending a DM with a file
  attached failed before anything left the building" — not "the internal
  channel lookup used an invalid limit".
- **Short sentences. Small headings with an emoji.** People skim this.
- **Name a tool only when the reader has to type it.** Otherwise say what it
  does.
- **Say if something needs doing.** The update line belongs in every entry:
  self-hosted users update and reconnect, hosted users do nothing.
- **Credit the person who found it**, via a real API mention.
- **Leave out what does not reach the reader.** A refactor, a test, a
  parameter rename — those live in the file, not in the comment. Anything not
  shipped yet stays out entirely.
- Register is community interior, so the cat voice is welcome — but the
  clarity bar is first-contact.

## Speaker

- "I" = Niklas. catknows is a solo-owned project.
- "we"/"us" only when it literally means the community ("join us", "we grow it
  together") — never a team or "maintainers" plural.

## Mascot & emoji

The mascot is the whisker face with the pink heart nose. Canonical emoji: 🐈.
Flavor: 🐾; 😼 fine in community posts. Avoid 🐽 — right idea (a nose), wrong
animal.

## The three steps (canonical)

1. **Plug the cat in.** Once.
2. **Ask the cat.** Plain words.
3. **The cat serves it back — secrets scrubbed.**

Optional memory line: *"Give it a vault and it remembers."* Never claim
automatic memory ("gets smarter every day") — memory only exists via the vault.
Every promise on a public surface must be covered by a shipped tool or code
path; when in doubt, say less.
