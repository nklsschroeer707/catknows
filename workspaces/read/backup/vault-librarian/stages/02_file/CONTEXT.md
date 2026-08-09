# Stage 02 — file into the vault

## Inputs
- (working) `../01_sweep/output/plan.md` — approved
- (config) the vault path from the workspace CONTEXT.md

## Process
1. Per approved entry, write the vault note: frontmatter in vault.py style —
   ```yaml
   type: skool-research
   workspace: <e.g. whats-new>
   community: <slug>
   date: <result date>
   tags: [skool/research, community/<slug>]
   ```
   Body = the result content, with names turned into `[[wikilinks]]` where a
   matching member note exists in `<community>/members/`.
2. Maintain `<vault>/<community>/research/_index.md`: append one line per
   filing (date · workspace · note link). Append-only — this list IS the
   growing history; never rewrite old lines.
3. Update `output/ledger.md`: per filed source — path, its modification
   time, the vault note it became.

## Outputs
- vault notes + `_index.md` update → the vault
- `ledger.md` and `latest.md` (what was filed this run) → `output/`

## Completion
Done when every approved entry has a vault note, the index grew by exactly
that many lines, and the ledger would make the next sweep skip all of them.
