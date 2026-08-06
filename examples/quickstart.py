"""Minimal end-to-end example: log in, pull members, print them.

Run from the repo root after `pip install -e .` and `playwright install chromium`:

    python examples/quickstart.py my-community-slug
"""

import sys

from skoolapi import SkoolClient, login
from skoolapi import normalize

if len(sys.argv) < 2:
    print("usage: python examples/quickstart.py <community-slug>")
    raise SystemExit(1)

community = sys.argv[1]

# Opens a browser the first time so you can log in; reuses the session after.
client = SkoolClient(login())

print(f"Members of {community}:\n")
for user in client.members(community):
    m = normalize.member(user)
    status = "online" if m["is_online"] else "offline"
    print(f"  {m['name']:<30} {m['role'] or 'member':<10} {m['points']:>5} pts  ({status})")
