# Stage 02 — publish it

## Inputs
- (working) `../01_draft/output/draft.md` — must carry the APPROVED mark

## Process
1. `create_post(community_slug, title, content)` WITHOUT confirm → the tool
   returns a preview, nothing is posted yet.
2. Preview matches the approved draft exactly? Call again with
   `confirm=true`. Any mismatch → back to the human, do not publish.
3. Record the outcome: link/id of the live post, when, where.

## Outputs
- `latest.md` → `output/` — what went live, where, when (previous runs to
  `output/archive/<date>.md`)

## Completion
Done when the live post's text equals the approved draft, and `latest.md`
links to it.
