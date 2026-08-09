# Stage 01 — pull the thread

## Inputs
- (parameter) `community_slug` — ask if not given
- (parameter) which post — a title (or part of one) or a post id; a title is
  resolved via `list_posts` (raise `limit` if it's an older post)

## Process
1. Resolve the post id, then `get_post_comments(community_slug, post_id)`.
2. Render the conversation with indentation for replies:
   `**Name** (date): text` — nested replies indented under their parent.
3. Top the file with the post itself (title, author, content in brief) and a
   3-bullet "what this thread is about".

## Outputs
- `latest.md` → `output/`

## Completion
Done when the whole thread reads like a transcript and no reply is orphaned.
No further stages.
