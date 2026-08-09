# Stage 01 — write the digest

## Inputs
- (parameter) `community_slug` — ask if not given
- (parameter) window — default: the 25 most recent posts

## Process
1. `list_posts(community_slug, limit=25)`.
2. Group by theme, not by date. Per post: **Title** (author, likes,
   comments) + a one-sentence summary in your own words.
3. Open the digest with "the 3 things worth knowing" — the posts with real
   traction or real news.

## Outputs
- `latest.md` → `output/` (move the old one to `output/archive/<date>.md`
  first — digests are worth keeping)

## Completion
Done when someone who skipped a week of the community is caught up in two
minutes. No further stages.
