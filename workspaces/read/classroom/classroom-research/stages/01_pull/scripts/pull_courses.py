"""Pull full course trees (modules, lessons, video links) for Skool communities.

Mechanical work — no AI needed (ICM: scripts do the fetching).

Usage:  .venv/Scripts/python.exe pull_courses.py <slug> [<slug> ...]
Writes: ../output/<slug>-classroom.json  and prints each course tree.

# ponytail: pulls course trees + video links; lesson TEXT is one extra
# request per lesson (?md=<lesson id>) — extend here if research ever needs it.
"""

import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from catknows import SkoolClient, login

OUT = Path(__file__).resolve().parent.parent / "output"


def pull(client: SkoolClient, slug: str) -> dict:
    overview = client.classroom(slug)
    courses = overview["pageProps"]["allCourses"]
    result = {"slug": slug, "courses": []}

    for c in courses:
        md = c["metadata"]
        entry = {
            "title": md.get("title", "?"),
            "desc": md.get("desc", ""),
            "numModules": md.get("numModules"),
            "hasAccess": bool(md.get("hasAccess")),
            "privacy": md.get("privacy"),
            "tree": None,
        }
        result["courses"].append(entry)
        if not entry["hasAccess"]:
            continue

        # Course detail sits behind a ?md= redirect (see skool-quirks.md).
        detail = client.http.get_next(
            f"/{slug}/classroom/{c['name']}.json?group={slug}", slug
        )
        redirect = detail.get("pageProps", {}).get("__N_REDIRECT")
        if redirect:
            path, _, q = redirect.partition("?")
            detail = client.http.get_next(f"{path}.json?{q}&group={slug}", slug)

        course = detail.get("pageProps", {}).get("course") or {}
        entry["tree"] = _strip(course.get("children") or [])
        time.sleep(0.8)  # be polite between course pulls
    return result


def _strip(nodes: list) -> list:
    """Keep only what research needs: titles, video links, nesting."""
    out = []
    for n in nodes:
        cm = (n.get("course") or {}).get("metadata", {})
        out.append(
            {
                "title": cm.get("title", "?"),
                "videoLink": cm.get("videoLink"),
                "videoLenMs": cm.get("videoLenMs"),
                "children": _strip(n.get("children") or []),
            }
        )
    return out


def _print_tree(nodes: list, depth: int = 1) -> None:
    for n in nodes:
        video = "  🎬" if n["videoLink"] else ""
        print("  " * depth + "- " + n["title"] + video)
        _print_tree(n["children"], depth + 1)


def main() -> None:
    slugs = sys.argv[1:]
    if not slugs:
        sys.exit("usage: pull_courses.py <community-slug> [<slug> ...]")

    OUT.mkdir(parents=True, exist_ok=True)
    client = SkoolClient(login())
    for slug in slugs:
        data = pull(client, slug)
        dest = OUT / f"{slug}-classroom.json"
        dest.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n=== {slug} ===")
        for course in data["courses"]:
            lock = "" if course["hasAccess"] else "  🔒 locked"
            print(f"\n## {course['title']} ({course['numModules']} modules){lock}")
            if course["tree"]:
                _print_tree(course["tree"])
        print(f"\n-> {dest}")


if __name__ == "__main__":
    main()
