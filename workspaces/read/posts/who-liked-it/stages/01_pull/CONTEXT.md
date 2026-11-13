# Stage 01 — pull the likes

## Inputs
- (parameter) `community_slug` — ask if not given
- (parameter) which post — title (or part) or post id; titles resolve via
  `list_posts`

## Process
1. Resolve the post id, then `get_post_likes(community_slug, post_id)`. One call
   returns ONE page of up to 10 likers (`likes`), plus `has_more` and `cursor`.
2. Page on: while `has_more` is true, call again with `cursor` set to the
   `cursor` of the last answer. Collect the likers of every page.
3. Write: the post (title, author, date), then the likers as
   Name (@handle) — one per line, count at the top.

## Outputs
- `latest.md` → `output/`

## Completion
Done when the count in the file matches the number of names listed, and `has_more`
was false on the last page you read (`total_upvotes` in the answer is Skool's own
count: if the list is shorter, say so). No further stages.
