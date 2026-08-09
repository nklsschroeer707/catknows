# Stage 01 — collect the material

## Inputs
- (parameter) which community the report is about — chapters from other
  communities' runs don't belong in it
- (working) every `workspaces/**/output/latest.md` in the repo (read/ and
  write/ alike — a filed bug list is report material too)

## Process
1. Glob for the `latest.md` files (pattern above, own workspace excluded).
   No wiring, no registry — whatever exists is a candidate.
2. Check each candidate: does it belong to this community, and how old is it
   (file date)?
3. Write the manifest — three lists:
   **Fresh** (usable), **Stale** (usable but old — say how old),
   **Missing** (workspaces that never ran for this community).
4. STOP if everything is missing: tell the human which workspaces to run
   first. An empty report helps nobody.

## Outputs
- `manifest.md` → `output/`

## Completion
The manifest honestly states what the report can and cannot cover.
Hand to stage 02.
