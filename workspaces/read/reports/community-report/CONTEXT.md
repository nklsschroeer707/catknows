# community-report — routing

**Purpose:** the newsroom: collect what the other agents produced, write
chapters, merge into one report.
**Stages:** `01_collect` → `02_write-chapters` `[gate]` → `03_final-report`

## Load
| Resource | When | Why |
|---|---|---|
| `../../../_shared/references/output-style.md` | always | report format |
| other workspaces' `stages/*/output/latest.md` | stage 01 finds them | the raw material |

## Do NOT load
| Resource | Why not |
|---|---|
| any MCP tool | this workspace never fetches — it assembles |
| other workspaces' CONTEXT.md | you read their results, not their instructions |
| `docs/API.md`, `catknows/` source | nothing here touches code |
