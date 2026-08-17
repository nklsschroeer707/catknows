# Legal & responsible-use notes

**Read this before using or forking.**

`catknows` is an independent, unofficial tool. It is **not affiliated with,
endorsed by, or connected to Skool** in any way. "Skool" is a trademark of its
respective owner.

## What this tool does

It automates *your own logged-in browser session* against Skool's private
internal endpoints — the same requests skool.com's website makes when you use
it. It does not bypass authentication, break encryption, or access anything your
account cannot already see in the browser. It only reads data from communities
**you are a member of or own**.

## Terms of Service

Skool's Terms of Service may restrict automated access, scraping, or use of
their non-public endpoints. **You are responsible for ensuring your use complies
with Skool's ToS and any applicable law.** Using this tool may violate those
Terms and could put your account at risk. The maintainers make no warranty and
accept no liability.

## Responsible use

- Only pull communities you have legitimate access to.
- The discovery/leaderboard endpoints (docs/API.md §6) expose public data about
  *other* communities and their owners. Reading Skool's public directory is one
  thing; systematically harvesting it, rebuilding their directory, or profiling
  owners/members is another — scrape gently, and don't do anything you couldn't
  defend under Skool's ToS and applicable law.
- Only export data you have a lawful basis to process (members' names, emails,
  etc. are **personal data** — GDPR/CCPA and similar laws apply to what you do
  with them).
- Don't hammer Skool's servers. The client sleeps between requests and backs off
  on rate-limit signals — don't remove those safeguards.
- Don't use exported member data for spam, harassment, or resale.

## Access policy: what catknows will read, and what it won't

This is the rule for deciding whether a new read tool gets built. It is **not**
"admins may, members may not". It is:

> Can the logged-in user see this in normal use of Skool? Then catknows may read
> it. If not, we leave it alone, even when a technical route exists.

The useful phrasing of that test is to ask what the *user* is allowed to do, not
what the *tool* is allowed to fetch:

- **Video transcripts.** The question is not "may a member pull the transcript?"
  but "may a member switch subtitles on?". They may. The CC button is there for
  every viewer, and those captions are the source `get_video_transcript` reads
  (`client.video_transcript` walks the player's own HLS manifest to its
  `SUBTITLES` track, the same one the player offers, which is why Mux rejects
  the request without a Skool `Referer`). Allowed.
- **Member lists.** Can a member see the complete member list of a community
  they merely belong to? No. Skool deliberately serves non-owners the first page
  only. So we respect that, even though we found a route that returns more.

The difference is between *summarising what the user already gets* and
*working around a server-side limit*.

### The five boundaries

1. **Video download stays out.** Downloading the video file really is an admin
   privilege in Skool. Reading captions is not. The two were confused early on,
   so the line is stated explicitly: captions yes, file no.

2. **Loom, Wistia, Vimeo and other embedded players are not touched.** Community
   owners choose that embedding precisely to prevent this. That is a line
   somebody drew on purpose, and it is not ours to step over. (It is also why
   `video_transcript` covers Skool-hosted video only.)

3. **Public YouTube videos are fair game**, but not because "it's public anyway".
   They pass the same test as everything else: the viewer is served those
   captions in the player regardless.

4. **A workaround is not a warrant.** Concretely: on the members list's
   `t=active` parameter, Skool appears to have moved server-side during our own
   testing (404 for everyone on 2026-08-12, 200 again on 2026-08-15, see
   AGENTS.md). We went looking for another way round. That is exactly the point
   to stop: once a limit is recognisably deliberate, the next detour around it
   is not a bugfix task.

5. **A local fork is the user's own risk.** The licence permits changes. What we
   will not do is ship a documented switch that turns the boundary off. A switch
   is an invitation; a fork is a decision.

### Where the boundary actually sits, and what is still unmeasured

Membership is the gate, and Skool enforces it one level higher than the tools
do. Measured 2026-08-17 against three paid communities the account does not
belong to: five out of five calls (`list_posts`, `get_post`,
`get_video_transcript`) end in a redirect to the About page, which the client
reports as missing access. The transcript path never reaches the caption
question at all, because the permission check fires before the post slug is
even resolved.

So the load-bearing sentence is about the mechanism, not about any one tool:
**Skool does not hand out members-only feeds, and catknows never sees more than
the logged-in account does.** That holds for every read tool and does not need
re-proving per tool.

One case remains unmeasured: a community with a **public** feed but
member-restricted videos. There the post would be readable and the caption
would be the only barrier. Whether Skool even offers that combination is
unknown. Note that a fully public community is worthless as a control, a
caption hit there only proves that public things are public.

## Stability

These endpoints are undocumented and can change or break at any time without
notice. This is not a supported integration.

## No warranty

Provided "as is", without warranty of any kind. See [LICENSE](LICENSE).
