# Stage 01 — build the catalog

## Inputs
- (parameter) `community_slug` — ask if not given

## Process
1. `get_classroom(community_slug)`.
2. Table: Course · Modules · Access (open / paid 🔒 / level-locked) · What
   it's about (one sentence from the description, your words).
3. Close with one line: how much of the classroom is actually accessible to
   the logged-in account.

## Outputs
- `latest.md` → `output/`

## Completion
Done when every course appears, including locked ones (marked honestly).
No further stages.
