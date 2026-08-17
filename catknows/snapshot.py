"""Append-only trend snapshots: numbers today, growth curves tomorrow.

    python -m catknows.snapshot --vault ./vault skoolers my-community --discovery

Per community slug, appends one JSON line (date + the public About numbers)
to `<vault>/trends/<slug>.jsonl`. With --discovery, also snapshots page 1 of
Skool's ranked board into `<vault>/trends/discovery.jsonl`.

No AI involved and strictly headless: safe to run from a scheduler. If the
persisted login has expired it exits with code 2 instead of opening a
browser window — run any catknows tool interactively once to re-login.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import SkoolClient, login, normalize


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
    args = parser.parse_args(argv)

    if not args.slugs and not args.discovery:
        parser.error("nothing to do: pass slugs and/or --discovery")

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
        except Exception as e:  # one broken slug must not kill the series of the rest
            print(f"!! {slug}: {e}", file=sys.stderr)
            failed += 1
            continue
        _append(trends / f"{slug}.jsonl", line)
        print(f"{slug}: members={line['members']} online={line['online']}")

    if args.discovery:
        try:
            _append(trends / "discovery.jsonl", {"date": now, "top": _discovery_top(client)})
            print("discovery: page 1 snapshotted")
        except Exception as e:
            print(f"!! discovery: {e}", file=sys.stderr)
            failed += 1

    # Exit non-zero if nothing was collected, so the scheduler shows a failed run
    # instead of a green tick on a snapshot that silently gathered nothing.
    return 1 if failed and failed == len(args.slugs) + int(args.discovery) else 0


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

    print("selfcheck ok")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        _selfcheck()
        sys.exit(0)
    sys.exit(main())
