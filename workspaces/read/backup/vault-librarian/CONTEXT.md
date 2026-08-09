# vault-librarian — routing

**Purpose:** sweep new workspace results, file them into the vault, keep the
research index growing — session after session.
**Stages:** `01_sweep` `[gate]` → `02_file`

## Config
- Vault path: the "Vault" section of `workspaces/_config/me.md`; not set →
  ask the human once and record it there

## Load
| Resource | When | Why |
|---|---|---|
| `../../../_shared/references/output-style.md` | always | the "Source:" footer tells you each result's community |
| other workspaces' `output/latest.md` + `output/archive/*.md` | stage 01 | the material to file |

## Do NOT load
| Resource | Why not |
|---|---|
| any MCP tool | the librarian files, it never fetches |
| `catknows/` source, `docs/API.md` | vault conventions are summarized here; the code isn't needed |
| other workspaces' CONTEXT.md | you file their results, not their instructions |
