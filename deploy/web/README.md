# Legal pages for catknows.app

Standalone HTML, no dependencies, no build tooling on the host. Upload and done.

| File | Publish at | Source |
|---|---|---|
| `index.html` | `catknows.app/` | hand-written one-pager |
| `privacy.html` | `catknows.app/privacy` | `../PRIVACY.md` |
| `dpa.html` | `catknows.app/dpa` | `../DPA.md` |
| `legal.html` | `catknows.app/legal` | `../../LEGAL.md` |
| `impressum.html` | `catknows.app/impressum` | hand-written (German, §5 DDG) |

`index.html` is the landing page: the catknows mark animated as the background
(whiskers draw in, then sway; the heart beats), all in CSS so there is no
animation JavaScript to load or fail. It carries the footer links the legal pages
need and is the surface the OAuth dashboard (plan §2a) will attach to.

The three generated pages come from the Markdown, which stays the source of
truth. After editing any of it:

```bash
python3 deploy/web/build-legal.py
```

Then upload. Editing the HTML directly means the two drift, and a privacy policy
that contradicts its own source is worse than none.

`impressum.html` is written by hand: it is German-language boilerplate with no
Markdown counterpart, and its content is legally prescribed rather than derived.

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

## Caddy

If serving from this box rather than a static host, extensionless paths need
`try_files`:

```
catknows.app {
	root * /var/www/catknows
	try_files {path} {path}.html
	file_server
	encode zstd gzip
}
```

Without the `try_files` line, `/privacy` is a 404 and only `/privacy.html` works.
