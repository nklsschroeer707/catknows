# Skool quirks — what bites you if you don't know it

- **Timestamps** come in nanoseconds or microseconds depending on the field.
  Sanity-check every date you print: if a "date" lands before 2010 or after
  2100, you scaled wrong.
- **Never fetch skool.com yourself** (no requests/curl/fetch). AWS-WAF
  fingerprints the TLS handshake; only the catknows tools get through.
- **Classroom depth:** `get_classroom` returns the course *list* only. Module
  and lesson trees need one request per course following a `?md=` redirect —
  that's what `classroom-research`'s pull script does. Lesson video links are
  in the tree; lesson *text* would be one extra request per lesson.
- **Access flags:** courses carry `hasAccess` and `privacy` (1 = paid/locked,
  2 = level-locked). Locked content is simply not available — say so instead
  of retrying.
- **Membership matters:** posts, members, classroom need you to be a member
  of the community. Only `get_community_about` and `get_discovery` work from
  the outside.
- **Be polite:** the client already paces paginated pulls (~0.8 s/page). Big
  pulls (all members + all comments) take minutes — that's normal, don't
  parallelize harder to "fix" it.
- **Handles vs names:** `get_member_profile` wants the handle (e.g.
  `schaad`), not the display name ("Dan Schaad"). Handles are in
  `list_members` output.
- **@-mentions in post/comment content** are
  `[@Display Name](obj://user/{userId})` — plain `@name` text does NOT
  notify anyone. Niklas: handle `niklas`, userId
  `a97f89358e2e49cca94ff677385830f3`.
- **Post categories ("labels")** are ids, not names. To find one, pull an
  existing post of that category with `list_posts(raw=true)` and reuse its
  label id.
