# Stage 02 — send it

## Inputs
- (working) `../01_draft/output/draft.md` — must carry the APPROVED mark

## Process
1. `send_dm(channel_id, content)` WITHOUT confirm → the tool returns a
   preview, nothing is sent yet.
2. Preview matches the approved draft exactly (text AND channel)? Call
   again with `confirm=true`. Any mismatch → back to the human, do not
   send.
3. Record the outcome: to whom, when, the sent text.

## Outputs
- `latest.md` → `output/` (previous runs to `output/archive/<date>.md`)

## Completion
Done when the sent message equals the approved draft and `latest.md` says
so.
