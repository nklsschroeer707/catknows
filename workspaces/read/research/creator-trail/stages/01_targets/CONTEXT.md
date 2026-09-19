# Stage 01 — build the target list  [gate]

## Inputs
- (parameter) communities to cover — slugs, or a `community-pulse` report
- (working, if it exists) `../../../community-pulse/stages/03_write/output/latest.md`
  and its chapters — the verdicts and the pinned shelves you'll compare against

## Process
1. Per community, `get_community_about(slug)` → the `owner` handle, plus the
   money block you already have (price, `owner_mrr_badge`, `ads`). An owner
   whose community runs a Meta pixel or Hyros is running campaigns somewhere;
   their public trail is where you find out what those campaigns point at.
2. `get_member_profile(owner_handle, slug)` → `links` (website, youtube,
   instagram, facebook, linkedin, twitter), `bio`, `location`, `time_zone`,
   `groups_created_by_user`. Those links are the whole target list — you are
   following what the person themselves published as their public presence.
3. Optional, when a pulse chapter names them: the 2–3 loudest **non-staff**
   creators in that community, same lookup. Members who post constantly are
   often operators with their own funnels.
4. Sort each target's lanes into **open** and **walled**:
   - *Open* — their own website and landing pages, YouTube, a podcast feed, a
     newsletter archive, a linktree, anything a search engine already indexes.
   - *Walled* — anything that answers with a login wall: Instagram, TikTok,
     Facebook, X to varying degrees, plus any "private" or members-only page.
     Mark it walled and leave it. Do not reach for a scraper that logs in,
     borrows cookies or poses as a phone app; that is exactly the line
     LEGAL.md draws, and it does not move because a repo exists that crosses
     it.
5. Write `targets.md`: one row per person — name · handle · their
   community(ies) · badge · open lanes (with URLs) · walled lanes · why
   they're on the list.

## Outputs
- `targets.md` → `output/`

## Completion — HUMAN GATE
STOP. This is where the job leaves Skool and starts touching other people's
servers from the human's own connection. They approve **who** gets looked up
and **which lanes** get touched, and may strike anyone from the list. Nothing
outside is fetched before that.
