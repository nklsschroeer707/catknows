# Stage 01 — take a snapshot, compare with the last one

## Inputs
- (working) the previous `output/latest.md`, if one exists

## Process
1. `get_community_about("skoolers")`.
2. Archive the previous `latest.md` to `output/archive/<date>.md`.
3. Write the new snapshot: member count, pricing, how they pitch
   themselves — then a **What changed** section against the previous
   snapshot (growth since last check, wording changes). First run: say
   "first snapshot, nothing to compare yet".
4. Still not a member? End with the standing note that posts/members are
   locked until the human joins.

## Outputs
- `latest.md` → `output/` (previous one archived)

## Completion
Done when the change since last time is stated in numbers, not vibes.
No further stages.
