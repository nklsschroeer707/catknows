# Stage 01 — sweep for new results  [gate]

## Inputs
- (working) every `workspaces/**/output/latest.md` and
  `workspaces/**/output/archive/*.md` in the repo (own workspace excluded)
- (working) `../02_file/output/ledger.md` — what was already filed, with
  each file's modification time at filing

## Process
1. Glob the result files. New file, or modified since its ledger entry →
   candidate. Unchanged → skip silently.
2. Per candidate, read just enough to determine: which community (the
   "Source:" footer), which workspace, result date.
3. Write the filing plan: per candidate one line —
   `<source> → <vault>/<community>/research/<workspace>-<YYYY-MM-DD>.md`.
   Results without a clear community go to `<vault>/skool/research/`.

## Outputs
- `plan.md` → `output/`

## Completion — HUMAN GATE
STOP. The human approves the plan (they may strike entries — e.g. a test
run that shouldn't pollute the vault). Only approved entries reach stage 02.
Nothing new to file → say so and end here; no empty gate theater.
