# Stage 01 — draft the post  [gate]

## Inputs
- (parameter) `community_slug` — ask if not given
- (parameter) what the post should say — the human's idea, notes, or bullet
  points

## Process
1. Write the draft in the human's voice, not yours: title + body. Short
   beats long; no AI-sounding filler.
2. Save it and show it to the human in full.
3. Iterate until they say it's right. Every change → new draft in the file,
   old version stays visible below.

## Outputs
- `draft.md` → `output/` — the approved title + body, marked "APPROVED by
  <human> on <date>" at the top

## Completion — HUMAN GATE
STOP. Only an explicitly approved draft (the exact text) goes to stage 02.
No approval, no stage 02 — ever.
