# Stage 01 — pull the likes

## Inputs
- (parameter) `community_slug` — ask if not given
- (parameter) which post — title (or part) or post id; titles resolve via
  `list_posts`

## Process
1. Resolve the post id, then `get_post_likes(community_slug, post_id)`.
2. Write: the post (title, author, date), then the likers as
   Name (@handle) — one per line, count at the top.

## Outputs
- `latest.md` → `output/`

## Completion
Done when the count in the file matches the number of names listed.
No further stages.
