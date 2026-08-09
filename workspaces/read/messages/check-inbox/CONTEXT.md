# check-inbox — routing

**Purpose:** one glance at the DM inbox: who wrote, what's waiting.
**Stages:** `01_check` (only stage)

## Load
| Resource | When | Why |
|---|---|---|
| `../../../_shared/references/mcp-tools.md` | always | tool names & args |
| `../../../_shared/references/output-style.md` | before writing | result format |

## Do NOT load
| Resource | Why not |
|---|---|
| `docs/API.md`, `catknows/` source | you use tools, not code |
| other workspaces | not your job |
