"""CLI: pull a whole Skool community into an Obsidian vault as Markdown.

    python -m catknows pull my-community --vault ./MyVault

Runs the full flow: browser login (first time only) -> fetch members, posts,
comments, likes -> normalize -> write Markdown notes with frontmatter.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import SkoolClient, login, session_from_cookie
from . import normalize, vault
from .http import SkoolHTTPError, SkoolSecurityChallenge, turned_away


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="catknows",
        description="Pull a Skool community into an Obsidian vault as Markdown.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    pull = sub.add_parser("pull", help="Fetch a community and write it to a vault.")
    pull.add_argument("community", help="The community slug (from skool.com/<slug>).")
    pull.add_argument("--vault", default="./vault", help="Output vault directory.")
    pull.add_argument("--profile-dir", default=".skool-profile",
                      help="Where to persist the browser login between runs.")
    pull.add_argument("--cookie", default="",
                      help="Skip the browser: pass a Cookie header copied from DevTools.")
    pull.add_argument("--no-comments", action="store_true",
                      help="Skip fetching comments (much faster).")
    pull.add_argument("--headless", action="store_true",
                      help="Run the browser headless (only works once logged in).")

    args = parser.parse_args(argv)
    if args.cmd == "pull":
        return _pull(args)
    return 1


def _stops(e: BaseException) -> bool:
    """Skool's security question, 401/403/429 or a repeated 202: the pull ends here."""
    return isinstance(e, SkoolSecurityChallenge) or turned_away(e)


def _pull_stopped(e: SkoolHTTPError, how_far: str, vault_dir: Path) -> int:
    """Say that the pull stopped and why, on stderr; the exit code is 1."""
    if isinstance(e, SkoolSecurityChallenge):
        why = f"Skool asked a security question (code {e.code}) on {e.method} {e.path}"
        tail = ""
    else:
        why, tail = f"Skool answered HTTP {e.status or 202}", f" {e}"
    print(f"error: pull stopped {how_far}: {why}, so nothing more was requested. "
          f"The notes already written stay in {vault_dir}.{tail}", file=sys.stderr)
    return 1


def _pull(args) -> int:
    if args.cookie:
        session = session_from_cookie(args.cookie)
        if not session.is_valid:
            print("error: --cookie has no auth_token in it.", file=sys.stderr)
            return 1
    else:
        session = login(profile_dir=args.profile_dir, headless=args.headless)

    client = SkoolClient(session)
    vault_dir = Path(args.vault).expanduser().resolve()
    vault.ensure_scaffold(vault_dir)
    community = args.community

    print(f"→ Fetching members of '{community}' ...")
    try:
        members = client.members(community)
    except SkoolHTTPError as e:
        if not _stops(e):
            raise
        return _pull_stopped(e, "while the members were read", vault_dir)
    for u in members:
        vault.write_member(vault_dir, community, normalize.member(u))
    print(f"  {len(members)} members written.")
    if members.capped_by_role:
        # A count on its own reads as "that was everyone".
        print(f"  NOTE: you are a regular member here, so this is the first page "
              f"only, not the full roster (Skool counts {members.total_members}).")

    print(f"→ Fetching posts of '{community}' ...")
    try:
        trees = client.posts(community)
    except SkoolHTTPError as e:
        if not _stops(e):
            raise
        return _pull_stopped(e, "while the list of posts was read", vault_dir)
    group_id = ""
    for tree in trees:
        gid = (tree.get("post") or {}).get("groupId")
        if gid:
            group_id = gid
            break

    written_posts = 0
    for tree in trees:
        prec = normalize.post(tree)
        comment_recs = None
        if not args.no_comments and group_id and prec["comments"] > 0:
            try:
                merged = client.comments(prec["skool_id"], group_id)
                comment_recs = normalize.comments(merged)
            except SkoolSecurityChallenge as e:
                # Not "one bad post": after Skool's security question every further
                # request would be an automaton ignoring it. Stop, exit non-zero.
                return _pull_stopped(e, f"after {written_posts} of {len(trees)} posts", vault_dir)
            except Exception as e:  # one bad post shouldn't kill the run
                if turned_away(e):
                    # 401/403/429 or a repeated 202: turned away. Same stop.
                    return _pull_stopped(e, f"after {written_posts} of {len(trees)} posts",
                                         vault_dir)
                print(f"  ! comments failed for {prec['skool_id']}: {e}", file=sys.stderr)
        vault.write_post(vault_dir, community, prec, comment_recs)
        written_posts += 1
    print(f"  {written_posts} posts written.")

    print(f"\n✓ Done. Vault at: {vault_dir}")
    print("\nᓚᘏᗢ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
