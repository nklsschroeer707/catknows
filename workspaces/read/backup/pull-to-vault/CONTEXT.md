# pull-to-vault — routing

**Purpose:** full community backup as Markdown, verified and summarized.
**Stages:** `01_backup` (only stage)

## Load
| Resource | When | Why |
|---|---|---|
| `../../../_shared/references/mcp-tools.md` | always | pull_to_vault args |
| `../../../_shared/references/skool-quirks.md` | always | big pulls take minutes — normal |

## Do NOT load
| Resource | Why not |
|---|---|
| `docs/API.md`, `catknows/` source | the tool wraps the whole flow |
| other workspaces | not your job |
