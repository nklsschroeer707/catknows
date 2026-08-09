# Stage 01 — build the portrait

## Inputs
- (parameter) `community_slug` — ask if not given
- (parameter) who — a handle (`janedoe`) or a display name; a display name
  must first be resolved to a handle via `list_members`

## Process
1. `get_member_profile(user_name, community_slug)` → bio, socials, stats.
2. `list_posts(community_slug, limit=25)` → collect this person's recent
   posts (author match) and posts they're clearly active under.
3. Write the portrait: who they are (2–3 sentences), the facts (joined,
   level, points, socials), what they've been doing lately (bullets with
   post titles). Plain language, no field names.

## Outputs
- `latest.md` → `output/`

## Completion
Done when someone who never met this person gets a fair picture in one page.
No further stages.
