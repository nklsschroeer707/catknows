# Skool quirks — what bites you if you don't know it

- **Timestamps** come in nanoseconds or microseconds depending on the field.
  Sanity-check every date you print: if a "date" lands before 2010 or after
  2100, you scaled wrong.
- **Never fetch skool.com yourself** (no requests/curl/fetch). AWS-WAF
  fingerprints the TLS handshake; only the catknows tools get through.
- **Classroom depth:** `get_classroom` returns the course *list* only. Module
  and lesson trees come from `get_course_tree`, one call per course. Lesson
  video links are in the tree; lesson *text* would be one extra request per
  lesson.
- **Courses outside your communities:** `get_course_tree` reads a course two
  ways, and they don't grant the same thing. Skool's API needs you to be a
  member and refuses *every* course otherwise, open ones included. The
  classroom page has no such gate, which is why you can open a course in a
  browser without joining. Pass `community_slug` and the tool falls back to
  that page; leave it out and a non-member gets a 401 (measured 2026-08-19).
- **Access flags:** courses carry `hasAccess` and `privacy` (1 = paid/locked,
  2 = level-locked). `hasAccess: 1` describes the COURSE ("open to members"),
  not you — a non-member sees it on courses they cannot read. Locked content
  is simply not available — say so instead of retrying.
- **The post feed is sorted by last activity, not by date.** A two-year-old
  thread with a fresh comment sits above today's post. So "the newest 25
  posts" is really "the 25 most recently *touched* posts" — for anything
  time-based (posts per day, what happened last month) read `created_at` off
  each record and count yourself. `last_comment_at` says when the
  conversation last moved, `pinned` marks the posts the owner parked at the
  top, `label_id` groups by category (ids only — no names anywhere in the
  feed). Missing those three fields in a result means the install predates
  them → `update_catknows`.
- **Activity is a lower bound, never a total.** One `list_posts` call returns
  at most 200 posts. If the oldest post you got back is inside your window,
  the window is full and your per-day number is a floor, not the rate. Say
  which it is.
- **Money signals, and what each one proves.** `get_community_about` works
  from the outside and carries most of them: `price` (what one member pays),
  `free_trial`, `affiliate_percent`, `plan` (the owner's own Skool bill —
  `pro` is $99/mo), the owner's `mrr_badge`, and `ads`. Each says something
  narrow, so don't stretch it:
  - `price` × `total_members` is **not** revenue. On freemium and tiers the
    member count includes everyone who paid nothing, `price` is the entry
    tier, and nobody's churn is in there. State it as a ceiling or not at all.
  - `mrr_badge` is the **owner's** revenue floor across everything they run
    (🍀 $3k → 🚀 $10k → 👑 $30k → 💎 $100k → ♦️ $300k → 🐐 $1m), not this
    community's, and not the members' money. The separate 🔥 (`actStatus`)
    is activity, not revenue.
  - `ads` reports whether a Meta pixel, Meta Conversions API, Google Ads tag
    or Hyros is wired up. That says they **buy traffic and measure it** —
    Hyros in particular is a paid-media tool nobody installs by accident. It
    says nothing about how much they spend or whether it works.
  - A **free** community with a full ad stack is a front end, not a business
    — the money is behind it. Look for what the pinned posts sell.
- **What members pay is a different question from what a community costs.**
  `list_members` carries `pays` per member (cents, with tier and interval) —
  proven spend, not a list price. Skool sends it on your own membership
  record everywhere; for other members it depends on what your role may see,
  so absent means "not shown to you", never "pays nothing". Free community =
  nobody in it has demonstrated they will pay for anything.
- **`location` is free text, `time_zone` is not.** Members type whatever they
  like into location ("You choose your self worth" is a real value). When the
  question is geography — and purchasing power hypotheses usually are — count
  `time_zone`, which Skool sets itself, and treat location as colour.
- **A member who runs their own communities is a different buyer.**
  `get_member_profile` returns `groups_created_by_user` and `mrr_badge`;
  owners with revenue badges sitting in someone else's community are the
  clearest audience-side money signal there is. It costs one call per member,
  so sample the ten most active, don't walk the whole list.
- **Membership matters:** posts and members need you to be a member of the
  community. `get_community_about` and `get_discovery` work from the outside,
  and so does the classroom (list always, course content via the page
  fallback above).
- **401 "action not permitted" is not a broken session.** Skool sends it when
  the login is fine and the account simply isn't in that community. Telling
  the user to reconnect will not help; joining, or connecting the account
  that IS in it, will.
- **Pricing models:** `get_community_about` reports `membership_model` as
  free / paid / freemium / tiers / one_time (Skool's five pricing options);
  older groups may have none set → `null`. Paid, tiers and one_time carry a
  `price` (for tiers it's the *entry* price; one_time has
  `recurring_interval: "one_time"`); freemium always has `price: null` —
  joining is free and Skool's About payload carries no tier amounts at all.
  Tier names + benefits are in the `tiers` field. Don't report a freemium
  community as "no pricing found" — the tiers ARE the pricing.
- **Discovery rank ≠ top-1000 board:** `get_discovery` only sees the top
  1000 — absent from the board ≠ not listed. For a community you OWN,
  `get_discovery_rank` returns the true overall rank (can be 20000+) plus
  the category rank; Skool's Entdecken settings UI shows the *category*
  rank, so don't mix the two. Foreign communities: board only (401).
- **Admin dashboard ≠ admin API:** `get_admin_metrics` covers member growth,
  active members and the activity series only. Visitors, conversion rate,
  signup sources, MRR and churn/retention exist only in Skool's dashboard UI
  — say "not available via catknows" instead of guessing.
- **Be polite:** the client already paces paginated pulls (~0.8 s/page). Big
  pulls (all members + all comments) take minutes — that's normal, don't
  parallelize harder to "fix" it.
- **Handles vs names:** `get_member_profile` wants the handle (e.g.
  `janedoe`), not the display name ("Jane Doe"). Handles are in
  `list_members` output.
- **@-mentions in post/comment content** are
  `[@Display Name](obj://user/{userId})` — plain `@name` text does NOT
  notify anyone. A userId is in `get_member_profile(..., raw=true)`; your
  own is in `workspaces/_config/me.md` once you've set it up.
- **Post categories ("labels")** are ids, not names. To find one, pull an
  existing post of that category with `list_posts(raw=true)` and reuse its
  label id.
- **Post attachments (PDFs etc.):** the post list only carries attachment
  *ids* in `metadata.attachments`. The download link lives in the post-detail
  page (`/{group}/{post-name}.json` → `metadata.attachmentsData`, a JSON
  string with `file_name` + `read_url` on assets.skool.com). The asset CDN
  is not WAF-gated like the API.
