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
