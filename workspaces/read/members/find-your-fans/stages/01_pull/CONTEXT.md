# Stage 01 — collect who engaged where

## Inputs
- (parameter) `community_slug` — ask if not given
- (parameter) depth — default: the 25 most recent posts

## Process
1. `list_posts(community_slug, limit=25)` → post ids and titles.
2. For every post: `get_post_likes` (who liked) and `get_post_comments`
   (who commented, how often).
3. More than ~10 posts? Fan out: spawn subagents, each taking a batch of ~5
   posts and running step 2 exactly as written here. Collect their results.
4. Write one flat list: per post — title, likers (@handles), commenters
   (@handle × count).

## Outputs
- `engagement.md` → `output/`

## Completion
Every post from step 1 appears in `engagement.md` (posts with zero
engagement listed too — that's signal). Then hand to stage 02.
