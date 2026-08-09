# Output style — how every `latest.md` should read

Written for a smart human who is NOT technical. No JSON dumps, no field
names, no tool talk.

## Shape

```markdown
# <Job name>: <community or subject>
*Pulled <YYYY-MM-DD HH:MM> — <one line: what this is>*

<the result: short sections, small tables, plain sentences>

---
*Source: <community slug> via catknows · <N> items*
```

## Rules

- Lead with the answer, not the method. Nobody cares which tool ran.
- Tables for enumerable facts (names, counts, dates) — max ~7 columns.
  Explanations go in prose around the table, not inside cells.
- Real dates ("2026-08-09"), not raw timestamps. Real names with @handles.
- If something was locked, missing, or partial: one honest line at the end,
  not a wall of caveats.
- Dated snapshots: when a workspace tracks change over time, move the old
  `latest.md` to `output/archive/<YYYY-MM-DD>.md` before overwriting.
- Vault copies (only when asked): match the style `pull_to_vault` writes —
  YAML frontmatter, `[[wikilinks]]` for people and communities.
