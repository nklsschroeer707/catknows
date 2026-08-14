#!/usr/bin/env python3
"""Render the legal Markdown into standalone HTML pages for catknows.app.

    python3 deploy/web/build-legal.py        # writes deploy/web/*.html

The Markdown files stay the source of truth — editing HTML by hand means the
two drift, and a privacy policy that contradicts itself is worse than none.
Re-run this after touching PRIVACY.md or DPA.md, then upload the HTML.

No dependencies: the subset of Markdown these documents use is small enough to
convert directly, and a build step that needs pip on a web host is a build step
that stops getting run.
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEPLOY = HERE.parent

# (source, output, page title, subtitle)
PAGES = [
    ("PRIVACY.md", "privacy.html", "Privacy Policy", "catknows hosted MCP server"),
    ("DPA.md", "dpa.html", "Data Processing Agreement", "GDPR art. 28 · catknows hosted"),
    # Lives in the repo root, not deploy/ — it's about the tool, not the service,
    # but users following a link from the privacy policy still need to land on it.
    ("../LEGAL.md", "legal.html", "Legal & Responsible Use", "catknows &mdash; the tool"),
]

STYLE = """
:root {
  --bg: #0F0E0C; --card: #1A1816; --line: #2A2724;
  --ink: #F5F0EB; --dim: #9C9690; --muted: #6B6560;
  --brand: #FF5C8A; --brand-deep: #E04570; --coral: #F0786A;
  --mono: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: var(--bg); color: var(--ink); min-height: 100vh;
       padding: 28px 16px 40px; -webkit-font-smoothing: antialiased; }
.container { max-width: 820px; margin: 0 auto; }
.topbar { display: flex; align-items: center; justify-content: space-between;
          gap: 16px; margin-bottom: 26px; }
.wordmark { font-weight: 800; font-size: 1.15rem; color: var(--ink);
            text-decoration: none; letter-spacing: -0.01em; }
.wordmark b, .wordmark i { font-weight: 800; font-style: normal; color: var(--brand-deep); }
.home { font-family: var(--mono); font-size: 0.78rem; color: var(--dim);
        text-decoration: none; border: 1px solid var(--line);
        padding: 7px 13px; border-radius: 999px; white-space: nowrap; }
.home:hover { color: var(--ink); border-color: var(--brand); }
.header { margin-bottom: 26px; }
.header h1 { font-size: 1.7em; color: var(--ink); margin-bottom: 6px;
             letter-spacing: -0.02em; }
.header p { color: var(--dim); font-size: 0.92em; font-family: var(--mono); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
        padding: 26px 30px; line-height: 1.75; margin-bottom: 20px; }
h2 { color: var(--brand); font-size: 1.18em; margin: 30px 0 12px;
     letter-spacing: -0.01em; }
h2:first-child { margin-top: 0; }
h3 { color: var(--coral); font-size: 1.02em; margin: 22px 0 8px; }
h4 { color: var(--ink); font-size: 0.97em; margin: 16px 0 6px; }
p, li { margin-bottom: 10px; font-size: 0.95em; color: var(--ink); }
ul, ol { margin-left: 22px; margin-bottom: 12px; }
a { color: var(--brand); text-underline-offset: 3px; }
a:hover { color: var(--ink); }
code { background: #221F1C; padding: 1px 6px; border-radius: 4px;
       font-size: 0.88em; font-family: var(--mono); color: var(--ink); }
pre { background: #131110; border: 1px solid var(--line); border-radius: 8px;
      padding: 14px 16px; margin: 12px 0; overflow-x: auto; }
pre code { background: none; padding: 0; font-size: 0.86em; line-height: 1.55; }
blockquote { border-left: 3px solid var(--coral); padding: 4px 0 4px 16px;
             margin: 14px 0; color: var(--dim); }
blockquote p:last-child { margin-bottom: 0; }
hr { border: none; border-top: 1px solid var(--line); margin: 28px 0; }
.tablewrap { overflow-x: auto; margin: 14px 0; }
table { border-collapse: collapse; width: 100%; font-size: 0.92em; }
th, td { border: 1px solid var(--line); padding: 9px 12px; text-align: left;
         vertical-align: top; }
th { background: #221F1C; color: var(--ink); font-weight: 600; }
.back { display: inline-block; margin-top: 4px; color: var(--brand);
        text-decoration: none; font-size: 0.9em; font-family: var(--mono); }
.back:hover { text-decoration: underline; }
.foot { display: flex; flex-wrap: wrap; gap: 6px 16px; align-items: center;
        justify-content: center; margin-top: 26px; padding-top: 18px;
        border-top: 1px solid var(--line);
        font-family: var(--mono); font-size: 0.74rem; }
.foot a { color: var(--dim); text-decoration: none; }
.foot a:hover { color: var(--ink); text-decoration: underline; }
"""

# Inline: code first, so ** and _ inside backticks stay literal.
_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*([^*]+)\*(?!\*)")


def _inline(text: str) -> str:
    """Escape, then apply inline markup. Code spans are protected from both."""
    slots: list[str] = []

    def stash(m: re.Match) -> str:
        slots.append(f"<code>{html.escape(m.group(1))}</code>")
        return f"\x00{len(slots) - 1}\x00"

    text = _CODE.sub(stash, text)
    text = html.escape(text)

    def link(m: re.Match) -> str:
        label, href = m.group(1), m.group(2)
        # Cross-document links point at the sibling page, not the .md file.
        target = href.split("#")[0].rsplit("/", 1)[-1]
        for src, out, title, _ in PAGES:
            if target and target == src.rsplit("/", 1)[-1]:
                # Extensionless: the server publishes /legal, not /legal.html,
                # so writing the filename here was a 404 on every page.
                href = "/" + out[:-5] + ("#" + href.split("#", 1)[1] if "#" in href else "")
                # A filename is not a label a reader can act on.
                if label.endswith(".md"):
                    label = title
                break
        else:
            # A repo-relative target with nothing published for it would be a
            # dead link on the site; keep the words, drop the link.
            if not href.startswith(("http://", "https://", "mailto:", "#")):
                return label
        return f'<a href="{href}">{label}</a>'

    text = _LINK.sub(link, text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _ITALIC.sub(r"<em>\1</em>", text)
    text = text.replace("—", "&mdash;").replace("§", "&sect;")
    return re.sub(r"\x00(\d+)\x00", lambda m: slots[int(m.group(1))], text)


def _table(rows: list[str]) -> str:
    """A GFM table: header, separator, body. Separator row is dropped."""
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    head, body = cells[0], cells[2:]
    out = ['<div class="tablewrap"><table>', "<tr>"]
    out += [f"<th>{_inline(c)}</th>" for c in head]
    out.append("</tr>")
    for row in body:
        out.append("<tr>")
        out += [f"<td>{_inline(c)}</td>" for c in row]
        out.append("</tr>")
    out.append("</table></div>")
    return "".join(out)


def convert(md: str) -> str:
    """Markdown subset -> HTML. Blocks are decided line by line."""
    lines = md.split("\n")
    out: list[str] = []
    i = 0
    # An HTML comment in the source is a note to the operator (a TODO), never
    # something to publish — a "fill in your address" marker on a live privacy
    # page is worse than the omission it flags.
    in_comment = False

    while i < len(lines):
        line = lines[i]

        if in_comment:
            in_comment = "-->" not in line
            i += 1
            continue
        if line.lstrip().startswith("<!--"):
            in_comment = "-->" not in line
            i += 1
            continue

        if not line.strip():
            i += 1
            continue

        # A 4-space indented block is preformatted (the address block, mostly).
        # Without this it collapses into one run-on line.
        if line.startswith("    ") and line.strip():
            block = []
            while i < len(lines) and (lines[i].startswith("    ") or not lines[i].strip()):
                if not lines[i].strip() and not (
                    i + 1 < len(lines) and lines[i + 1].startswith("    ")
                ):
                    break  # blank line ends it unless the block continues
                block.append(lines[i][4:])
                i += 1
            out.append(f"<pre>{html.escape(chr(10).join(block).strip(chr(10)))}</pre>")
            continue

        if line.startswith("```"):
            block: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1
            out.append(f"<pre><code>{html.escape(chr(10).join(block))}</code></pre>")
            continue

        if line.startswith("|"):
            rows: list[str] = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(lines[i])
                i += 1
            out.append(_table(rows) if len(rows) >= 3 else "")
            continue

        if re.match(r"^#{1,6} ", line):
            level = len(line) - len(line.lstrip("#"))
            text = line[level:].strip()
            if level == 1:  # the document title is already in the page header
                i += 1
                continue
            out.append(f"<h{level}>{_inline(text)}</h{level}>")
            i += 1
            continue

        if re.match(r"^(-{3,}|\*{3,})$", line.strip()):
            out.append("<hr>")
            i += 1
            continue

        if line.startswith(">"):
            block = []
            while i < len(lines) and (lines[i].startswith(">") or
                                      (block and lines[i].strip() and
                                       not lines[i].startswith(("#", "|", "-", ">")))):
                block.append(re.sub(r"^>\s?", "", lines[i]))
                i += 1
            paras = [p for p in " ".join(block).split("  ") if p.strip()]
            inner = "".join(f"<p>{_inline(p.strip())}</p>" for p in paras)
            out.append(f"<blockquote>{inner}</blockquote>")
            continue

        m = re.match(r"^(\s*)([-*]|\d+\.) ", line)
        if m:
            ordered = m.group(2)[0].isdigit()
            tag = "ol" if ordered else "ul"
            items: list[str] = []
            while i < len(lines):
                mi = re.match(r"^(\s*)([-*]|\d+\.) (.*)$", lines[i])
                if mi:
                    items.append(mi.group(3))
                    i += 1
                elif lines[i].strip() and lines[i].startswith((" ", "\t")) and items:
                    items[-1] += " " + lines[i].strip()  # continuation line
                    i += 1
                else:
                    break
            body = "".join(f"<li>{_inline(x)}</li>" for x in items)
            out.append(f"<{tag}>{body}</{tag}>")
            continue

        para = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(
            r"^(#{1,6} |\||>|```|\s*([-*]|\d+\.) |-{3,}$)", lines[i]
        ):
            para.append(lines[i])
            i += 1
        out.append(f"<p>{_inline(' '.join(para))}</p>")

    return "\n".join(x for x in out if x)


def page(title: str, subtitle: str, body: str, out: str) -> str:
    """One page. The wordmark and the pill both go home — a reader who lands
    here from a search result needs a way back that is visible without
    scrolling to the bottom."""
    nav = "".join(
        f'<a href="/{o[:-5]}">{html.escape(t)}</a>'
        for _, o, t, _ in PAGES
        if o != out
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>catknows. {html.escape(title)}</title>
<style>{STYLE}</style>
</head>
<body>
<div class="container">
  <div class="topbar">
    <a class="wordmark" href="/"><b>cat</b>knows<i>.</i></a>
    <a class="home" href="/">&larr; Back to catknows.app</a>
  </div>
  <div class="header">
    <h1>{html.escape(title)}</h1>
    <p>{html.escape(subtitle)}</p>
  </div>
  <div class="card">
{body}
  </div>
  <a class="back" href="/">&larr; Back to catknows.app</a>
  <div class="foot">
    {nav}<a href="/impressum">Imprint</a>
  </div>
</div>
</body>
</html>
"""


def main() -> int:
    for src, out, title, subtitle in PAGES:
        md = (DEPLOY / src).read_text(encoding="utf-8")
        (HERE / out).write_text(
            page(title, subtitle, convert(md), out), encoding="utf-8"
        )
        print(f"{src} -> deploy/web/{out}")

    # Operator TODOs must not reach a published page; they're notes to Niklas.
    for _, out, _, _ in PAGES:
        text = (HERE / out).read_text(encoding="utf-8")
        assert "TODO" not in text, f"{out} still carries a TODO — check the comment stripper"
    print("no TODOs leaked into the HTML")

    # The server publishes /privacy, not /privacy.html: an internal link that
    # keeps the extension is a 404 for every visitor. This shipped once.
    dead = re.findall(r'href="(?!https?:|mailto:|#)([^"]*\.html[^"]*)"',
                      "".join((HERE / out).read_text(encoding="utf-8")
                              for _, out, _, _ in PAGES))
    assert not dead, f"internal links still carry .html: {sorted(set(dead))}"

    # And a reader who lands here from a search result needs a way home.
    for _, out, _, _ in PAGES:
        text = (HERE / out).read_text(encoding="utf-8")
        assert 'href="/"' in text, f"{out} has no link back to the landing page"
    print("internal links extensionless, every page links home")
    return 0


if __name__ == "__main__":
    sys.exit(main())
