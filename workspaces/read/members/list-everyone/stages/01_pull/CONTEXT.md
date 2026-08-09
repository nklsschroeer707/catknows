# Stage 01 — pull the member list

## Inputs
- (parameter) `community_slug` — ask if not given
- (parameter) how many — default: all members (`limit` high); "just the recent
  ones" → default limit 25 (Skool sorts by last-active)

## Process
1. `list_members(community_slug, limit=…)`.
2. Build one table: Name (@handle) · Role · Level · Points · Last active.
   Owner/admins first, then by last-active.
3. Close with two lines of color: total count, how many were active in the
   last 7 days.

## Outputs
- `latest.md` → `output/`

## Completion
Done when `latest.md` exists and every date in it is a real calendar date.
No further stages.
