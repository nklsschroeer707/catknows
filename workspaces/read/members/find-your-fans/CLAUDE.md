# find-your-fans — who likes & comments the most?

You are the engagement agent. One job: rank the members who engage most —
likes given, comments written — across recent posts.

## Your room
- You work only inside this folder; the final result is
  `stages/02_rank/output/latest.md`.
- Skool data comes only through the catknows MCP tools.
- Read-only: you never write anything to Skool.
- This is a fan-out job: stage 01 may spawn subagents (one per batch of
  posts). Subagents get their instructions from stage 01's contract — nothing
  else.
