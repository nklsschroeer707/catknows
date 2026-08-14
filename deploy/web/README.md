# The catknows.app pages

Standalone HTML, no dependencies, no build tooling on the host. Upload and done.

| File | Publish at | Source |
|---|---|---|
| `index.html` | `catknows.app/` | hand-written one-pager |
| `privacy.html` | `catknows.app/privacy` | `../PRIVACY.md` |
| `dpa.html` | `catknows.app/dpa` | `../DPA.md` |
| `legal.html` | `catknows.app/legal` | `../../LEGAL.md` |
| `impressum.html` | `catknows.app/impressum` | hand-written (German, §5 DDG) |
| `header-loop.mp4` | `catknows.app/media/…` | rendered clip, 1920×960 |
| `header-loop-mobile.mp4` | `catknows.app/media/…` | same clip, 1280×640 |
| `header-poster.jpg` | `catknows.app/media/…` | its first frame |

`index.html` is the landing page: one screen, no scrolling, with the clip
running full-bleed behind the copy. Fonts are embedded as `data:` URIs — no CDN,
and `font-src 'self' data:` in the Caddyfile is what lets them load at all.

**The clip loops across two stacked `<video>` elements**, not with the `loop`
attribute. It brightens ~17% from first frame to last, so it has no frame that
matches its own start: a plain loop jumped visibly (PSNR 20.5 dB against 44 dB
for an ordinary frame step), and playing it forward-then-reversed read as
exactly that. The idle copy starts underneath and the pair swap opacity over the
last 0.9s. Replacing the clip means re-checking that, not just dropping in a
file.

Media files are served by their own route (`/media/<name>`), an allowlist in
`dashboard.py` — the page route next to it only publishes the legal pages.
A new asset therefore needs an entry in `LANDING_MEDIA`, or it 404s.

The three generated pages come from the Markdown, which stays the source of
truth. After editing any of it:

```bash
python3 deploy/web/build-legal.py
```

Then upload. Editing the HTML directly means the two drift, and a privacy policy
that contradicts its own source is worse than none.

`impressum.html` is written by hand: it is German-language boilerplate with no
Markdown counterpart, and its content is legally prescribed rather than derived.
That makes it the page every shared change has to be applied to twice — the cat
mark in the topbar lives in `MARK` in the generator and again, verbatim, here.

Two more assertions guard the generated pages, both from bugs that shipped: no
internal link may carry `.html`, and no entity may be double-escaped. The second
one is the subtler failure — a subtitle written as `&mdash;` gets escaped on the
way out and reaches the reader as the literal text `&mdash;`, which greps clean
and only shows up in a screenshot. Write literal characters in `PAGES`.

## Why HTML pages and not just the Markdown in the repo

Both the GDPR (art. 12(1): "easily accessible") and §5 DDG want these reachable
without effort. A file in a git directory is not that. The linking rule of thumb:
every page of the site carries a footer link to `/impressum` and `/privacy`,
reachable in one click from anywhere.

## Before publishing

The generator strips HTML comments, which is where the operator TODOs live, and
asserts no `TODO` survives into the output. That keeps a "fill in your address"
note off a live page — but it also means **an unresolved TODO becomes an invisible
omission** rather than a visible marker. Check the Markdown for open TODOs before
you upload, not the HTML:

```bash
grep -rn "TODO" deploy/PRIVACY.md deploy/DPA.md
```

At the time of writing, one is open: verifying the sub-processor agreements
(netcup signature, Scaleway acceptance) actually exist. The privacy policy claims
they do — make that true before the page goes up.

## How these are actually served

Not by a file server. Caddy proxies `catknows.app` to the dashboard app, and
`dashboard.py` publishes them by name from an allowlist: `/privacy`, `/dpa`,
`/legal`, `/impressum` — **extensionless**. `/privacy.html` is a 404.

That bit once: `build-legal.py` wrote `privacy.html` into every cross-reference,
so each link between the legal pages dead-ended on the live site while working
fine when opened from disk. The generator now asserts that no internal link
carries `.html` and that every page links home, so it cannot come back quietly.

Two consequences when adding a page:

- a new legal page needs its name in the page allowlist in `dashboard.py`, and
  a new asset needs its filename in `LANDING_MEDIA` — otherwise it 404s;
- `catknows/*.py` changes need `systemctl restart catknows-dashboard` on the
  box. Changing only files in this directory needs just a `git pull`.

The CSP lives in `deploy/Caddyfile`, and it is not incidental: the landing
embeds its fonts as `data:` URIs, which `font-src 'self' data:` is what permits.
Editing that file means copying it to `/etc/caddy/`, `caddy validate`, and
`systemctl reload caddy` — a `git pull` alone changes nothing.
