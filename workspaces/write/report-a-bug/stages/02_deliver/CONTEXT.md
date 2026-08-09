# Stage 02 — deliver the reports

## Inputs
- (working) `../01_document/output/bugs/*.md` — approved at the gate
- (working) `../01_document/output/summary.md`

## Process
1. Route check (mechanical): `gh auth status`. Works → **GitHub path**.
   Fails or no `gh` → **Skool path**.

**GitHub path**
2. `gh issue list --limit 100` → skip already-filed bugs (title similarity).
3. Per approved report: `gh issue create` — title from the report, body =
   the report itself.

**Skool path** (needs `CATKNOWS_ALLOW_WRITE=1`; tool missing → stop after
writing latest.md and tell the human the reports are local-only)
2. Build ONE post: title "catknows bug report <date>", body = the summary
   plus the reports, opening line tags Niklas:
   `[@Niklas](obj://user/a97f89358e2e49cca94ff677385830f3)` — so he can fix
   and push to GitHub himself.
3. Resolve the "Feedback" category id of the target community (default
   `catnose`): pull one existing Feedback-category post with
   `list_posts(raw=true)` and reuse its label id; can't resolve → ask the
   human, never guess.
4. `create_post` WITHOUT confirm → show the human the exact draft.
   Approved? → `confirm=true`.

## Outputs
- `latest.md` → `output/` — per bug: issue link OR the Skool post link, plus
  which path ran and why

## Completion
Done when every approved bug has exactly one destination link in
`latest.md` (or an honest "local only: <reason>" line).
