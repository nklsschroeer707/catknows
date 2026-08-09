# Stage 01 — document every bug  [gate]

## Inputs
- (parameter) the evidence: error messages, tracebacks, a failed workspace
  run, or the human's description of what went wrong
- (reference) `../../references/bug-report-template.md`

## Process
1. Split the evidence into distinct bugs (same root symptom = one bug).
2. One bug → write the report yourself. Several → spawn one subagent per
   bug; each gets THIS contract plus its bug's evidence, nothing else.
3. Every report follows the template. Quote errors verbatim; trim noise.
4. Write `output/summary.md`: one line per bug (title, severity) — the list
   the human approves.

## Outputs
- `bugs/<nn>-<slug>.md` → `output/` (one per bug)
- `summary.md` → `output/`

## Completion — HUMAN GATE
STOP. The human reads the summary (and reports as needed), may strike or
edit any of them. Only approved reports go to stage 02.
