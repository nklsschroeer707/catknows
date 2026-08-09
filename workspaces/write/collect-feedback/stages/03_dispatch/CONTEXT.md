# Stage 03 — create the issues

## Inputs
- (working) `../02_classify/output/triage.md` — approved by the human

## Process
1. `gh issue list --limit 100` → skip anything already filed (match by
   title similarity, not exact string).
2. Per approved bug: `gh issue create` with the title and body from
   triage.md. Body ends with `Reported by <name> in the Skool test thread.`
3. Questions and ideas are NOT issues — list them in the summary for the
   human to answer on Skool themselves.

## Outputs
- `latest.md` → `output/` — issue links created, duplicates skipped, open
  questions for the human

## Completion
Done when every approved bug has exactly one issue link in `latest.md`.
