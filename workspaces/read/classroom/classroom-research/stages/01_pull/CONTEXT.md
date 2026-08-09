# Stage 01 — pull the course trees (script, no AI)

## Inputs
- (parameter) one or more `community_slug`s — ask if not given

## Process
1. From the repo root, run:
   `.venv/Scripts/python.exe workspaces/read/classroom/classroom-research/stages/01_pull/scripts/pull_courses.py <slug> [<slug> …]`
2. The script writes one JSON per community into `output/` and prints each
   course tree. Locked courses appear with `hasAccess: false` — that is
   data, not an error.

## Outputs
- `<slug>-classroom.json` → `output/` (one per community)

## Completion
One JSON per requested community exists. Hand to stage 02.
