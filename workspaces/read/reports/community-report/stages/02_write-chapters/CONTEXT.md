# Stage 02 — write the chapters  [gate]

## Inputs
- (working) `../01_collect/output/manifest.md`
- (working) the source `latest.md` files the manifest marked Fresh or Stale

## Process
1. Chapter map (only where material exists):
   - Members (list-everyone, know-one-member, find-your-fans) → **The people**
   - Posts (whats-new, read-the-comments, collect-feedback) → **The conversations**
   - Classroom (course-overview, classroom-research) → **The learning**
   - Calendar → **What's ahead** · Admin → **The numbers** · Research → **The outside view**
2. One subagent per chapter; each gets THIS contract plus its source files,
   nothing else. Chapters DISTILL — 10–20 lines of what matters now, never
   a copy of the source. Stale sources get one line: "as of <date>".
3. Collect the chapters into `output/chapters/`.

## Outputs
- `chapters/<name>.md` → `output/`

## Completion — HUMAN GATE
STOP. The human reads the chapters and may edit or strike any of them.
Only approved chapters reach stage 03.
