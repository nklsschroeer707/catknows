# Stage 01 — check the inbox

## Inputs
- none (your own inbox — no community parameter)

## Process
1. `list_chat_channels(limit=30)`.
2. Write two sections: **Waiting for you** (unread — who, last line, when)
   and **Recent conversations** (read — one line each).
3. Suggest nothing, send nothing. If a message clearly needs a reply, flag
   it with "needs an answer" and leave it to the human.

## Outputs
- `latest.md` → `output/`

## Completion
Done when every unread channel appears under "Waiting for you".
No further stages.
