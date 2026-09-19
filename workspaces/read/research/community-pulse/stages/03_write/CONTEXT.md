# Stage 03 — write the pulse report

## Inputs
- (working) `../02_pulse/output/chapters/*.md` — approved by the human
- (working, if any) `../01_pull/output/join-to-read.md`

## Process
1. Open with the ranking table — the whole point of the job, readable in one
   glance: Community (slug) · Members · Price · Posts/day · Member-written
   share · Last post · Verdict.
2. Sort by verdict, then by posts per day. Mark floors with `≥`.
3. Then the chapters, shortest verdict last — a reader who stops after the
   table has still got the answer.
4. Close with three short lists: **worth joining** (and what to look for
   inside), **worth watching** (re-run in N days for the real delta), and
   **dropped** (one reason each). Communities that were price-gated or need
   joining go here, not into a footnote.
5. Before overwriting, move the old `latest.md` to
   `output/archive/<YYYY-MM-DD>.md` — stage 02 needs it next time to compute
   the true delta.
6. Vault copy only if asked, per `output-style.md`.

## Outputs
- `latest.md` → `output/`
- previous run → `output/archive/<YYYY-MM-DD>.md`

## Completion
Done when someone can decide, per community, "join / watch / drop" from the
table alone — and when every number in it is either measured or marked as a
floor.
