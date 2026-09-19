# Stage 02 — follow the trail

## Inputs
- (working) `../01_targets/output/targets.md` — approved by the human

## Tools
Use what the running client actually has, in this order of preference. Check
first, don't assume: a workspace that hard-codes one tool breaks on every
other client.

1. **A fetch tool** (WebFetch or equivalent) — reads a page a visitor gets.
   The workhorse; most of what matters is on the creator's own site.
2. **A search tool** (WebSearch or equivalent) — finds what they announced
   somewhere you weren't looking: a podcast appearance, an affiliate's
   promo page, an event listing, a summit line-up.
3. **A local CLI, only if it's already installed** — `yt-dlp` for YouTube
   metadata (uploads with dates, and the `/streams` tab, where *scheduled*
   livestreams are the single best source of "what's coming up"), or `curl`
   for a feed. Don't install anything to make a lane work; note the lane as
   unavailable and go on.

No tool at all for a lane → write `not available in this client` next to it.
An empty finding and a missing tool must never look the same in the output.

## Per target, per open lane

**Their own site.** Fetch the linked homepage, then the pages it points at
that matter: offer/pricing, a webinar or cohort page, an "apply"/waitlist
form, a blog index. Pull out: what's being sold, at what price, the promise
in the headline, and **any date** — cohort start, doors close, countdown,
"next call", a year in a testimonial. Dates are the payload of this job.

**YouTube.** The channel feed `https://www.youtube.com/feeds/videos.xml?channel_id=UC…`
returns the recent uploads with publish dates and needs nothing but a fetch;
when you only have a `@handle` URL, the channel page's HTML carries the
`channelId`. Take: the last ~15 titles with dates (cadence + themes), and
the `/streams` tab for anything scheduled but not yet live.

**Anything else indexed** — podcast RSS, a newsletter archive, a linktree, a
Substack, an event page. Same treatment: titles, dates, the offer.

**Walled lanes.** Record `walled — needs an account` and move on. That is a
finding, not a failure: a creator whose entire public trail is inside
Instagram is genuinely harder to read from outside, and the report should
say so rather than pretending the lane was empty.

**Meta Ad Library**, if the human asked for the ad side: the library is a
public transparency tool and its web UI is the honest route — open it and
read it. Its open API has historically been limited to ads about politics
and social issues (broader in the EU under the DSA), so check what today's
terms actually allow before automating anything against it, and never build
a covert scraper for it. Whatever you take from it, cite the ad's own
library URL.

## Manners
One target at a time. A pause between requests to the same host. If a site
answers 403, rate-limits you, or shows a robots-style "don't" — stop with
that host and note it. Nothing here is urgent enough to be rude.

## Outputs
- `trail/<handle>.md` → `output/` (one per target): raw findings only, every
  entry with its **source URL and the date you read it**, plus a short
  `lanes` header saying which were open, walled, or unavailable.

## Completion
Every approved target has a file, and every file's claims carry a source.
Nothing was fetched that stage 01 didn't approve. Hand to stage 03.
