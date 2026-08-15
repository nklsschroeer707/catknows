"""Live proof for the classroom write path (docs/API.md §7).

Creates a DRAFT course (invisible to members), builds a folder + two pages,
edits a page, reorders, publishes and unpublishes, then deletes everything.
Verifies each step by re-reading the course tree.

Opt-in — it writes to a real community, so it only runs with:

    CATKNOWS_E2E_SLUG=<community you own>  python test_classroom_live.py

Deleting the course itself needs an email-verified client id (§7.6); pass it
via CATKNOWS_E2E_CLIENT_ID. Without one, the script deletes the children and
tells you to remove the empty draft course by hand.
"""

import os
import sys
import time

from catknows import SkoolClient, login

SLUG = os.environ.get("CATKNOWS_E2E_SLUG", "")
CLIENT_ID = os.environ.get("CATKNOWS_E2E_CLIENT_ID", "")

if not SLUG:
    print("Set CATKNOWS_E2E_SLUG to a community YOU OWN to run this. Skipped.")
    sys.exit(0)

client = SkoolClient(login(headless=True))
step = 0


def check(label, ok):
    global step
    step += 1
    print(f"{step:2}. {'ok' if ok else 'FAIL'}  {label}")
    assert ok, label


def titles(course_id):
    time.sleep(0.8)
    client.http._cache.clear()
    tree = client.course_tree(course_id)
    out = []

    def walk(node, depth):
        unit = node.get("course") or {}
        out.append((depth, (unit.get("metadata") or {}).get("title"), unit.get("state")))
        for ch in node.get("children") or []:
            walk(ch, depth + 1)

    walk(tree, 0)
    return out


course = client.create_course(SLUG, "ZZ catknows Beweis (bitte ignorieren)",
                              desc="Wegwerf-Kurs des Classroom-Livetests.",
                              privacy=2, draft=True)
cid = course["id"]
check("create_course -> draft", course["state"] == 1 and course["unit_type"] == "course")

folder = client.create_course_item(cid, "Ordner", folder=True)
check("create folder (set)", folder["unit_type"] == "set" and folder["parent_id"] == cid)

p1 = client.create_course_item(cid, "Seite A", parent_id=folder["id"], content="Test 1")
check("create page in folder", p1["parent_id"] == folder["id"]
      and p1["metadata"]["desc"].startswith("[v2]"))

p2 = client.create_course_item(cid, "Seite B", content="Test 2")
check("create page at course root", p2["parent_id"] == cid)

upd = client.update_course_item(p1["id"], {"title": "Seite A neu", "desc": "Test 3",
                                           "transcript": None, "video_id": ""})
check("update page (flat PUT)", upd["metadata"]["title"] == "Seite A neu"
      and "Test 3" in upd["metadata"]["desc"])

renamed = client.update_course_item(cid, {"title": "ZZ catknows Beweis umbenannt"})
check("update course keeps privacy (reset bug guarded)",
      renamed["metadata"]["privacy"] == 2)

client.set_course_state(cid, True)
check("publish", titles(cid)[0][2] == 2)
client.set_course_state(cid, False)
check("unpublish", titles(cid)[0][2] == 1)

client.move_course_item(p2["id"], 0)
check("reorder: Seite B first", titles(cid)[1][1] == "Seite B")

client.delete_course_item(folder["id"])  # children get LIFTED to the course root
after = titles(cid)
check("delete folder lifts children to root",
      "Ordner" not in [t for _, t, _ in after]
      and (1, "Seite A neu") in [(d, t) for d, t, _ in after])

client.delete_course_item(p1["id"])
client.delete_course_item(p2["id"])
if CLIENT_ID:
    client.delete_course_item(cid, client_id=CLIENT_ID)
    left = [(c.get("metadata") or {}).get("title")
            for c in client.classroom_courses(SLUG).get("courses", [])]
    check("delete course", "ZZ catknows Beweis umbenannt" not in left)
else:
    print("    (no CATKNOWS_E2E_CLIENT_ID — delete the empty draft course by hand)")

print("classroom live proof OK")
