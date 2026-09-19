# Stage 01 — pull the feed and the facts

## Inputs
- (parameter) one or more `community_slug`s — the shortlist; ask if not given
- (parameter) `window` — how far back to look, default **30 days**

## Process

**1. Can you even read it?** `list_my_communities` once, up front. Posts and
members need membership (skool-quirks.md) — a foreign community answers 401
"action not permitted", which is not a broken login.

For every slug you are NOT in, call `get_community_about` and sort it:
- **free / freemium** → readable as soon as the human joins. Put it on a
  "join to read" list with its price model and member count. Do not join
  anything yourself, and do not treat the 401 as a failure to retry.
- **paid / one_time / tiers** → the feed stays closed. Record it as
  *no pulse readable — price-gated at <price>*, keep the About facts and the
  calendar, and let stage 02 work with that much. An unreadable community is
  a documented outcome, not a gap to paper over.

**2. Per readable slug, pull:**
- `list_posts(slug, limit=50)`. Look at the OLDEST `created_at` you got back:
  still inside the window → raise the limit (100, then 200 max) until it
  falls outside, or until 200 comes back. If 200 posts still don't reach past
  the window, every rate you derive is a **floor** — write `floor: true` in
  the file and say why.
- `list_members(slug, limit=25)` — you need the owner/admin/moderator handles
  to tell staff posts from member posts. Grab more only if the community is
  big and the top 25 are all staff.
- `get_calendar(slug)` — what's scheduled.
- `get_community_about(slug)` — members, price model, owner, the pitch.

**3. Write one file per community, `<slug>-pulse-data.md`:**
- **Facts** — members, price model, owner, your role there, pull timestamp.
- **Staff** — handles with role, from `list_members`.
- **Pinned** — every post with `pinned: true`, with its full text. The pinned
  shelf is where the offer, the rules and the funnel live; it's the one place
  worth keeping verbatim.
- **Feed table** — one row per post: `created_at` (date) · title · author
  handle · staff? · upvotes · comments · `last_comment_at` (date). Post
  bodies: keep them for the 10 posts with the most comments in the window,
  drop the rest. Bodies are what blows this file up, and stage 02 only needs
  the busy ones.
- **Events** — upcoming entries from the calendar.
- **Coverage** — window asked for, oldest post actually retrieved, post count
  retrieved, `floor: true/false`.

`pinned`, `last_comment_at` and `label_id` missing from every record? The
install predates them — run `update_catknows` and say so, rather than
reporting a community with no pinned posts.

## Outputs
- `<slug>-pulse-data.md` → `output/` (one per readable community)
- `join-to-read.md` → `output/` (only if something landed on that list)

## Completion
Every requested slug is accounted for: pulled, "join to read", or
"price-gated". No slug silently disappeared. Hand to stage 02.
