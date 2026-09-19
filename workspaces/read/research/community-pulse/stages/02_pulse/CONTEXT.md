# Stage 02 — read the pulse  [gate]

## Inputs
- (working) `../01_pull/output/<slug>-pulse-data.md` — one per community
- (working, if it exists) `../03_write/output/archive/<date>.md` — the last run

## Process
One community → do it yourself. Several → one subagent per community, each
with THIS contract and its own data file, nothing else.

Per community write `chapters/<slug>.md` with these six sections.

**1. Heartbeat** — counted off `created_at`, never off feed order:
- posts per day in the window (mark it a floor if stage 01 said so)
- first half of the window vs second half — rising, flat or fading
- days since the last post was *written* (not bumped)
- the longest silence inside the window
- comments per post, and how many posts got **zero** comments

**2. Who carries it** — the test that separates alive from staffed:
- share of posts written by staff vs by ordinary members
- how many distinct authors, and what the top author's share is
- reply behaviour: do members answer each other, or does every thread end
  with a staff reply (or nothing)?

One-person show, staff-only feed, or a feed where members post and nobody
answers — each of those is a different kind of dead. Name which one.

**3. The pinned shelf** — what the owner parked at the top, and what it's
for: the offer, the rules, an onboarding path, a live event, a funnel out to
something else. Quote the actual call to action. For paid-ad research this is
the most valuable section in the file — pinned posts are where the promise
the ads make gets repeated inside.

**4. What's going on** — from the window's busiest posts:
- the 5 posts with the most comments: title, author, why it landed
- the recurring themes and the words that keep coming back
- what people ask for, complain about, celebrate
- anything time-bound: launches, challenges, cohorts, a summit, a deadline
- upcoming calendar events, with dates

**5. Buying power — can this audience pay?**

A hypothesis with its evidence, never a number you invented. Grade each
signal by what it actually proves:

*Hard — someone demonstrably paid:*
- the community's own `price` and model (members of a $97/mo group have
  already paid $97/mo; members of a free group have proven nothing)
- `pays` values off the member records you pulled — real amounts, real tiers
- what the pinned shelf sells, and at what price

*Firm — the operator's side:*
- `owner_mrr_badge`: the floor the owner has passed across everything they
  run (💎 = $100k/mo and up). An owner at that level knows what their
  audience converts at.
- `ads`: a Meta pixel, Conversions API, Google Ads tag or Hyros means they
  buy traffic and measure it. Hyros especially — nobody installs that by
  accident. A **free** community with a full ad stack is a front end for
  something paid; say what you think sits behind it.
- `plan: pro`, `free_trial`, `affiliate_percent` — how the funnel is built.

*Soft — the audience's own signals, from the feed and the sampled profiles:*
- members who run their own communities (`groups_created_by_user`) or carry
  their own badge — buyers, not browsers
- paid tools named in posts, revenue and results talk, hiring, budgets
- `time_zone` spread (Skool's own field; `location` is free text people joke
  in) — where the audience sits
- counter-signals, which count just as much: discount begging, "free
  alternative to X?", refund threads, complaints about the price

Close with one sentence in this shape: **"Hypothesis: … . Evidence: … .
What would falsify it: … ."** Then the caveats that always apply — price ×
members is not revenue, member count is not purchasing power, and a free
community's audience has not demonstrated willingness to pay.

**6. Verdict** — one honest paragraph plus a one-word label:
- **alive** — members post, members answer, the rate holds up
- **staffed** — regular posts, but staff writes them and few reply
- **parked** — the shelf is tidy, the feed is not moving
- **unreadable** — price-gated; say what the About page and calendar suggest
  and leave it at that

Close with the practical line: is this worth joining, worth watching, or
worth dropping — and for an ad-arbitrage screen, whether what's running
inside matches what the ads promise outside, and whether the people inside
look like they can afford it.

**Delta, when an archive exists:** compare against the previous snapshot —
posts added since then, whether the rate moved, whether the pinned shelf
changed. That comparison is the only *true* delta; everything else is
reconstructed from one feed read. Say which of the two you're showing.

## Outputs
- `chapters/<slug>.md` → `output/` (one per community)

## Completion — HUMAN GATE
STOP. The human reads the chapters and may strike communities, ask for a
deeper cut, or send you back for a wider window before stage 03 merges
anything.
