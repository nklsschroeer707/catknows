"""Live proof for the poll write path (docs/API.md §5.5).

Creates a poll, attaches it to a post, re-reads the feed to verify the poll
made it onto the post, then deletes the post again.

Opt-in — it writes to a real community, so it only runs with:

    CATKNOWS_E2E_SLUG=<community you own>  python test_poll_live.py
"""

import json
import os
import sys
import time

from catknows import SkoolClient, login

SLUG = os.environ.get("CATKNOWS_E2E_SLUG", "")
if not SLUG:
    print("Set CATKNOWS_E2E_SLUG to a community YOU OWN to run this. Skipped.")
    sys.exit(0)

client = SkoolClient(login(headless=True, timeout_ms=30_000))
step = 0


def check(label, ok):
    global step
    step += 1
    print(f"{step:2}. {'ok' if ok else 'FAIL'}  {label}")
    assert ok, label


poll = client.create_poll(SLUG, ["Ja", "Nein", "  "])  # blank option is dropped
print("   raw poll response:", json.dumps(poll, default=str)[:500])
poll_id = poll.get("poll_id") or ""
check("create_poll returns an id", bool(poll_id))

try:
    client.create_poll(SLUG, ["nur eine"])
    check("create_poll rejects <2 options", False)
except ValueError:
    check("create_poll rejects <2 options", True)

# Some communities require a category — borrow a label id from any existing post.
label = next((((t.get("post") or {}).get("metadata") or {}).get("labels", "")
              for t in client.posts(SLUG, all_pages=False)
              if ((t.get("post") or {}).get("metadata") or {}).get("labels")), "")
print("   borrowed label:", label or "(none needed)")

post = client.create_post(
    SLUG, "ZZ catknows Poll-Beweis (bitte ignorieren)",
    "Wegwerf-Post des Poll-Livetests.", poll=poll_id, labels=label,
)
pid = post.get("id") or (post.get("post") or {}).get("id") or ""
check("create_post with poll", bool(pid))
print("   raw post response:", json.dumps(post, default=str)[:800])

time.sleep(1)
client.http._cache.clear()
trees = client.posts(SLUG, all_pages=False)
mine = next((t for t in trees if (t.get("post") or {}).get("id") == pid), None)
check("post visible in feed", mine is not None)
meta = ((mine or {}).get("post") or {}).get("metadata") or {}
check("feed post carries the poll id", meta.get("poll") == poll_id)
print("   poll as seen by readers:", json.dumps(
    {k: v for k, v in (mine or {}).items() if "poll" in str(k).lower()}
    or {"metadata.poll": meta.get("poll"), "pollData": meta.get("pollData")},
    default=str)[:800])

client.http.delete_api2(f"/posts/{pid}")
client.http._cache.clear()
time.sleep(1)
trees = client.posts(SLUG, all_pages=False)
check("post deleted again",
      all((t.get("post") or {}).get("id") != pid for t in trees))

print("poll live proof OK")
