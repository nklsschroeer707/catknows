# Stage 01 — draft the message  [gate]

## Inputs
- (parameter) who — a name; resolve to a channel id via `list_chat_channels`
  (no channel with that person → stop and say so)
- (parameter) what the message should say

## Process
1. Confirm the recipient with the human — right person, right channel
   (show the channel's last message as context so nobody DMs the wrong Dan).
2. Write the draft in the human's voice. DMs are short.
3. Show it in full; iterate until they say it's right.

## Outputs
- `draft.md` → `output/` — recipient, channel id, the approved text, marked
  "APPROVED by <human> on <date>"

## Completion — HUMAN GATE
STOP. Only an explicitly approved draft (the exact text, the exact
recipient) goes to stage 02. No approval, no stage 02 — ever.
