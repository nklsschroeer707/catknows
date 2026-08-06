"""Write normalized Skool records as Obsidian-friendly Markdown notes.

One note per member/post, with YAML frontmatter so Obsidian's Dataview and any
downstream AI ("librarian") can query and cross-link them. The body is human
readable; the frontmatter is machine readable. Nothing here is Skool-specific
beyond the field names — swap this module out to target a different CRM.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def _yaml_value(v) -> str:
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return "[" + ", ".join(_yaml_scalar(x) for x in v) + "]"
    return _yaml_scalar(v)


def _yaml_scalar(v) -> str:
    s = str(v)
    # Quote anything that could confuse YAML.
    if s == "" or any(c in s for c in ':#[]{}"\n') or s.strip() != s:
        return '"' + s.replace('"', '\\"') + '"'
    return s


def _frontmatter(fields: dict) -> str:
    lines = ["---"]
    for k, v in fields.items():
        if v is None or v == "":
            continue
        lines.append(f"{k}: {_yaml_value(v)}")
    lines.append("---")
    return "\n".join(lines)


def _safe_filename(name: str, fallback: str) -> str:
    cleaned = "".join(c for c in name if c.isalnum() or c in " -_").strip()
    return (cleaned or fallback)[:120]


def write_member(vault_dir: Path, community: str, rec: dict) -> Path:
    """Write a member as `<vault>/<community>/members/<name>.md`."""
    folder = vault_dir / community / "members"
    folder.mkdir(parents=True, exist_ok=True)
    fname = _safe_filename(rec.get("name", ""), rec.get("skool_id", "member"))
    path = folder / f"{fname}.md"

    fm = _frontmatter({
        "type": "skool-member",
        "community": community,
        "skool_id": rec.get("skool_id"),
        "name": rec.get("name"),
        "email": rec.get("email"),
        "role": rec.get("role"),
        "points": rec.get("points"),
        "is_online": rec.get("is_online"),
        "last_active": rec.get("last_active"),
        "tags": ["skool/member", f"community/{community}"],
    })
    body = f"\n# {rec.get('name', 'Member')}\n"
    if rec.get("bio"):
        body += f"\n{rec['bio']}\n"
    path.write_text(fm + "\n" + body, encoding="utf-8")
    return path


def write_post(vault_dir: Path, community: str, rec: dict, comments: list[dict] | None = None) -> Path:
    """Write a post (with its comments inline) as a note under `posts/`."""
    folder = vault_dir / community / "posts"
    folder.mkdir(parents=True, exist_ok=True)
    fname = _safe_filename(rec.get("name", "")[:60], rec.get("skool_id", "post"))
    path = folder / f"{fname}.md"

    fm = _frontmatter({
        "type": "skool-post",
        "community": community,
        "skool_id": rec.get("skool_id"),
        "author": rec.get("user_name"),
        "post_type": rec.get("post_type"),
        "comments": rec.get("comments"),
        "upvotes": rec.get("upvotes"),
        "created_at": rec.get("created_at"),
        "tags": ["skool/post", f"community/{community}"],
    })
    body = f"\n# {rec.get('name', 'Post')}\n\nby [[{rec.get('user_name', 'unknown')}]]\n"
    if comments:
        body += f"\n## Comments ({len(comments)})\n\n"
        for c in comments:
            body += f"- **[[{c.get('user_name', '?')}]]**: {c.get('text', '')}\n"
    path.write_text(fm + "\n" + body, encoding="utf-8")
    return path
