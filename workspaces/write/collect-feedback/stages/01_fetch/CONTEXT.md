# Stage 01 — fetch the tester thread

## Inputs
- (parameter) `community_slug` and thread — defaults come from the
  "Feedback thread" section of `workspaces/_config/me.md`; no config and no
  parameter → ask the human

## Process
1. `list_posts(community_slug)` → find the thread by title, note its post id.
2. `get_post_comments(community_slug, post_id)` → the full thread.
3. Write every comment verbatim (author, date, text, indented replies) —
   no interpretation yet, that's stage 02's job.

## Outputs
- `thread.md` → `output/`

## Completion
All comments captured, in order, nothing paraphrased. Hand to stage 02.
