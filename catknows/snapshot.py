"""Append-only trend snapshots: numbers today, growth curves tomorrow.

    python -m catknows.snapshot --vault ./vault skoolers my-community --discovery
    python -m catknows.snapshot --vault ./vault --rank my-community --growth my-community

Per community slug, appends one JSON line (date + the public About numbers)
to `<vault>/trends/<slug>.jsonl`. With --discovery, also snapshots page 1 of
Skool's ranked board into `<vault>/trends/discovery.jsonl`. With --rank SLUG
(owner-only), appends that community's overall + category discovery rank to
`<vault>/trends/rank.jsonl` — the series that shows a penalty or a recovery.
With --growth SLUG (owner-only), appends the dashboard's last-30-days
visitors, signups, conversion and signup sources to `<vault>/trends/growth.jsonl`.

No AI involved and strictly headless: safe to run from a scheduler. If the
persisted login has expired it exits with code 2 instead of opening a
browser window — run any catknows tool interactively once to re-login.
If Skool answers with its security question (AUTH-SEC-5xx) or with HTTP 401,
403 or 429 it stops at once, requests nothing more and exits with code 3; rows
already appended stay.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import SkoolClient, login, normalize
from .http import SkoolHTTPError, SkoolSecurityChallenge, turned_away


def _profile_dir() -> Path:
    # Same profile the MCP server uses, so the scheduler reuses its login.
    return Path(
        os.environ.get("CATKNOWS_PROFILE_DIR", Path.home() / ".catknows" / "skool-profile")
    )


def _about_numbers(client: SkoolClient, slug: str) -> dict:
    data = client.community_about(slug)
    g = ((data.get("pageProps") or {}).get("currentGroup")) or {}
    md = g.get("metadata") or {}
    # A stale session still returns 200 with an empty payload. Writing that as a
    # row of nulls poisons the series with placeholders that look like measurements.
    if md.get("totalMembers") is None:
        raise RuntimeError(
            "empty about payload (no totalMembers) — session likely expired; "
            "run any catknows tool interactively once to refresh it"
        )
    return {
        "members": md.get("totalMembers"),
        "online": md.get("totalOnlineMembers"),
        "admins": md.get("totalAdmins"),
        "posts": md.get("totalPosts"),
        "courses": md.get("numCourses"),
        "plan": md.get("plan"),
        "price": normalize.about_pricing(data)["price"],
    }


def _discovery_top(client: SkoolClient) -> list[dict]:
    data = client.discovery(page=1)
    top = []
    for entry in data.get("groups") or []:
        g = entry.get("group") or {}
        md = g.get("metadata") or {}
        top.append(
            {
                "rank": entry.get("rank"),
                "slug": g.get("name"),
                "members": md.get("totalMembers"),
            }
        )
    return top


def _rank_row(client: SkoolClient, slug: str) -> dict:
    d = client.discovery_rank(client.group_id_for(slug))
    # Same guard as _about_numbers: a null rank is a broken read, not a data point.
    if d.get("rank") is None:
        raise RuntimeError("empty rank payload (no rank) — not the owner, or session expired")
    return {
        "slug": slug,
        "rank": d.get("rank"),
        "category": (d.get("category") or {}).get("name"),
        "category_rank": d.get("category_rank"),
        "is_showing": d.get("is_showing"),
        "boost_enabled": d.get("boost_enabled"),
        "rank_updated_at": d.get("rank_updated_at"),
    }


def _growth_row(client: SkoolClient, slug: str) -> dict:
    """The dashboard's last-30-days tiles plus signup sources (owner only).

    Skool shows these only as "last 30 days"; one row a day is what turns
    them into a history. Same guard as the others: no visitors = broken read.
    """
    gid = client.group_id_for(slug)
    tiles = client.growth_overview(gid)
    if tiles.get("num_visitors") is None:
        raise RuntimeError("empty growth payload (no visitors) — not the owner, or session expired")
    visitors, signups = tiles.get("num_visitors"), tiles.get("num_signups")
    sources = {i.get("attribution"): i.get("total")
               for i in client.analytics_chart(gid, "signups_by_source")}
    return {
        "slug": slug,
        "period": "last_30_days",
        "members": tiles.get("num_members"),
        "visitors": visitors,
        "signups": signups,
        "conversion_rate": round(signups / visitors, 3) if visitors and signups is not None else None,
        "new_mrr": tiles.get("new_mrr"),
        "sources": sources,
    }


def _stopped(e: SkoolHTTPError, what: str) -> int:
    """Security question or 401/403/429: no entry after this one is requested."""
    if isinstance(e, SkoolSecurityChallenge):
        why = f"Skool asked a security question (code {e.code}) on {e.method} {e.path}"
    else:
        why = f"Skool answered HTTP {e.status} ({e})"
    print(f"!! {what}: {why} - snapshot stopped, nothing more was requested. "
          "Rows already appended stay.", file=sys.stderr)
    return 3


def _append(path: Path, line: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="catknows.snapshot",
        description="Append dated trend snapshots to <vault>/trends/*.jsonl.",
    )
    parser.add_argument("slugs", nargs="*", help="Community slugs to snapshot.")
    parser.add_argument("--vault", default="./vault", help="Vault directory.")
    parser.add_argument("--discovery", action="store_true",
                        help="Also snapshot page 1 of the discovery board.")
    parser.add_argument("--rank", nargs="*", default=[], metavar="SLUG",
                        help="Also log the discovery rank of these communities you own.")
    parser.add_argument("--growth", nargs="*", default=[], metavar="SLUG",
                        help="Also log the dashboard's 30-day growth of these communities you own.")
    args = parser.parse_args(argv)

    if not args.slugs and not args.discovery and not args.rank and not args.growth:
        parser.error("nothing to do: pass slugs, --discovery, --rank SLUG and/or --growth SLUG")

    try:
        session = login(profile_dir=_profile_dir(), headless=True, timeout_ms=30_000)
    except Exception as e:
        print(f"login failed (headless-only, no browser opened): {e}\n"
              "Run any catknows tool interactively once to refresh the session.",
              file=sys.stderr)
        return 2

    from . import vault as vault_mod

    client = SkoolClient(session)
    vault_dir = Path(args.vault).expanduser().resolve()
    vault_mod.ensure_scaffold(vault_dir)
    trends = vault_dir / "trends"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    failed = 0
    for slug in args.slugs:
        try:
            line = {"date": now, **_about_numbers(client, slug)}
        except SkoolSecurityChallenge as e:
            return _stopped(e, slug)
        except Exception as e:  # one broken slug must not kill the series of the rest
            if turned_away(e):
                return _stopped(e, slug)
            print(f"!! {slug}: {e}", file=sys.stderr)
            failed += 1
            continue
        _append(trends / f"{slug}.jsonl", line)
        print(f"{slug}: members={line['members']} online={line['online']}")

    if args.discovery:
        try:
            _append(trends / "discovery.jsonl", {"date": now, "top": _discovery_top(client)})
            print("discovery: page 1 snapshotted")
        except SkoolSecurityChallenge as e:
            return _stopped(e, "discovery")
        except Exception as e:
            if turned_away(e):
                return _stopped(e, "discovery")
            print(f"!! discovery: {e}", file=sys.stderr)
            failed += 1

    for slug in args.rank:
        try:
            line = {"date": now, **_rank_row(client, slug)}
        except SkoolSecurityChallenge as e:
            return _stopped(e, f"rank {slug}")
        except Exception as e:
            if turned_away(e):
                return _stopped(e, f"rank {slug}")
            print(f"!! rank {slug}: {e}", file=sys.stderr)
            failed += 1
            continue
        _append(trends / "rank.jsonl", line)
        # Category names carry emoji; a cp1252 console must not kill the run.
        print(f"rank {slug}: overall=#{line['rank']} category=#{line['category_rank']}")

    for slug in args.growth:
        try:
            line = {"date": now, **_growth_row(client, slug)}
        except SkoolSecurityChallenge as e:
            return _stopped(e, f"growth {slug}")
        except Exception as e:
            if turned_away(e):
                return _stopped(e, f"growth {slug}")
            print(f"!! growth {slug}: {e}", file=sys.stderr)
            failed += 1
            continue
        _append(trends / "growth.jsonl", line)
        print(f"growth {slug}: visitors={line['visitors']} signups={line['signups']}")

    # Exit non-zero if nothing was collected, so the scheduler shows a failed run
    # instead of a green tick on a snapshot that silently gathered nothing.
    jobs = len(args.slugs) + int(args.discovery) + len(args.rank) + len(args.growth)
    return 1 if failed and failed == jobs else 0


def _selfcheck() -> None:
    """python -m catknows.snapshot --selfcheck — the empty-payload guard holds."""

    class _Fake:
        def __init__(self, payload):
            self._payload = payload

        def community_about(self, slug):
            return self._payload

    def _wrap(md):
        return {"pageProps": {"currentGroup": {"metadata": md}}}

    row = _about_numbers(_Fake(_wrap({"totalMembers": 36, "totalOnlineMembers": 8})), "x")
    assert row["members"] == 36 and row["online"] == 8, row

    # Every shape a stale session produced must raise, never return nulls.
    for payload in (
        _wrap({}),
        _wrap({"totalMembers": None}),
        {"pageProps": {"currentGroup": {}}},
        {"pageProps": {}},
        {},
    ):
        try:
            _about_numbers(_Fake(payload), "x")
        except RuntimeError:
            continue
        raise AssertionError(f"empty payload silently accepted: {payload}")

    class _FakeRank:
        def __init__(self, payload):
            self._payload = payload

        def group_id_for(self, slug):
            return "gid"

        def discovery_rank(self, gid):
            return self._payload

    row = _rank_row(_FakeRank({"rank": 4891, "category": {"name": "Relationships"},
                               "category_rank": 85}), "x")
    assert row["rank"] == 4891 and row["category_rank"] == 85, row
    for payload in ({}, {"rank": None}):
        try:
            _rank_row(_FakeRank(payload), "x")
        except RuntimeError:
            continue
        raise AssertionError(f"null rank silently accepted: {payload}")

    print("selfcheck ok")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        _selfcheck()
        sys.exit(0)
    sys.exit(main())
