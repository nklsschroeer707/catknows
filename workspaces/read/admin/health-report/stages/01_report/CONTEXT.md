# Stage 01 — write the health report

## Inputs
- (parameter) `community_slug` — ask if not given
- (parameter) period — default 30d; 7d and 90d exist too

## Process
1. `get_admin_metrics(community_slug, range=…)`.
2. Report in three sections: **Growth** (members in/out, trend), **Life**
   (active members, posts, comments), **Money** (if the metrics include it).
   Every number gets a plain-language sentence — "34 joined, 5 left" beats
   a naked figure.
3. One closing paragraph: the single most important thing these numbers say.
   No advice unless asked — findings, not coaching.

## Outputs
- `latest.md` → `output/` (previous one to `output/archive/<date>.md` —
  trends need history)

## Completion
Done when an owner gets the state of their community in 90 seconds.
No further stages.
