# Stage 02 — study each classroom  [gate]

## Inputs
- (working) `../01_pull/output/<slug>-classroom.json` — one per community

## Process
1. One community → analyze it yourself. Several → spawn one subagent per
   community; each gets THIS contract and its JSON, nothing else.
2. Per community write one chapter (`chapters/<slug>.md`):
   - **How it's built** — course line-up, how deep, how much is locked
   - **What's actually taught** — the themes behind the course titles
   - **Worth watching** — up to 5 concrete lessons with video links and why
   - **Character** — one honest paragraph: who is this classroom for?
3. Judge only what's in the JSON. Locked courses: name them, don't guess
   their content.

## Outputs
- `chapters/<slug>.md` → `output/` (one per community)

## Completion — HUMAN GATE
STOP. The human reads the chapters (and may strike communities or ask for a
deeper look) before stage 03 merges anything.
