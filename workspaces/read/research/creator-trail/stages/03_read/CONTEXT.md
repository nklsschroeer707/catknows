# Stage 03 — read the trail

## Inputs
- (working) `../02_trail/output/trail/*.md`
- (working, if it exists) the `community-pulse` chapters and calendar entries
  for the same communities

## Process
Per creator, a short chapter:

**1. What they're pushing right now** — the live offer, its price, the
promise in their own words. One quote beats a paraphrase.

**2. What's coming** — the dated table, and the reason this workspace
exists: What · When · Where · Source. Merge both sides — the Skool calendar
from the pulse run and everything off-platform (cohort starts, webinars,
scheduled livestreams, doors-close dates, summits they appear at). Undated
announcements go in a separate line marked *no date given*, never with a
guessed one.

**3. Cadence and direction** — how often they publish, and which way it's
moving: quiet for months then three videos this week is a launch ramp;
steady for years is a machine; a dead channel next to a busy community means
the audience lives somewhere else entirely. Say which of those you see.

**4. Inside vs outside** — the cross-check, and the part nobody gets from
one source alone:
- Does the pinned shelf sell what the landing page sells, or is the
  community the front end for something bigger?
- Do the themes match, or is the feed about one thing while the ads and
  videos push another?
- Does a launch outside line up with a live feed inside, or with a parked
  one? Both moving is a different bet from either alone.

**5. What this changes** — one paragraph: does this creator move up or down
the list, and what would you watch next? Name the date to come back on when
there's an event on the calendar.

## Rules
- Every claim keeps its source URL. Claims from a walled or unavailable lane
  don't exist — say the lane was closed instead.
- Separate what you read from what you infer. "Posts weekly since March"
  is a finding; "ramping up for a launch" is a reading, and should look like
  one.
- Before overwriting, move the old `latest.md` to
  `output/archive/<YYYY-MM-DD>.md`. Re-runs are how a cadence becomes visible.

## Outputs
- `latest.md` → `output/`
- previous run → `output/archive/<YYYY-MM-DD>.md`

## Completion
Done when the dated table is something someone can put in a calendar, and
when every creator's chapter says plainly how the outside picture matched
the inside one — including where it didn't.
