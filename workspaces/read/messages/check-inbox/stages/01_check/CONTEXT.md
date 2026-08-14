# Stage 01 — check the inbox

## Inputs
- none (your own inbox — no community parameter)

## Process
1. `list_chat_channels(limit=30)` (30 is Skool's max — a larger limit is refused).
2. For an unread channel whose last line alone doesn't say what it's about,
   `read_dms(channel_id, count=20)` to see the recent exchange. Don't do this
   for every channel — only where the summary would otherwise be a guess.
3. Write two sections: **Waiting for you** (unread — who, last line, when)
   and **Recent conversations** (read — one line each).
4. Suggest nothing, send nothing. If a message clearly needs a reply, flag
   it with "needs an answer" and leave it to the human.

## Outputs
- `latest.md` → `output/`

## Completion
Done when every unread channel appears under "Waiting for you".
No further stages.
