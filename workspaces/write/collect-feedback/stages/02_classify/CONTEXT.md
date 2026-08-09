# Stage 02 — sort the feedback  [gate]

## Inputs
- (working) `../01_fetch/output/thread.md`
- (reference) `../../references/classification-guide.md`

## Process
1. Walk the thread comment by comment. Classify each item per the guide:
   Bug / Question / Idea / Praise / Noise.
2. Merge duplicates (same bug reported twice = one entry, both reporters
   named).
3. Write the triage list grouped by category. Per bug: proposed issue title,
   what happened, expected behavior, reporter, severity.

## Outputs
- `triage.md` → `output/`

## Completion — HUMAN GATE
STOP here. Show the human the triage list and wait for their approval
(they may recategorize or strike items). Only an approved list goes to
stage 03.
