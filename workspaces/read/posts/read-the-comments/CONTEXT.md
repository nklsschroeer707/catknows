# read-the-comments — routing

**Purpose:** one post's full comment thread, readable top to bottom.
**Stages:** `01_pull` (only stage)

## Load
| Resource | When | Why |
|---|---|---|
| `../../../_shared/references/mcp-tools.md` | always | post_id comes from list_posts |
| `../../../_shared/references/output-style.md` | before writing | result format |
| `../../../_shared/references/skool-quirks.md` | if data looks odd | timestamps |

## Do NOT load
| Resource | Why not |
|---|---|
| `docs/API.md`, `catknows/` source | you use tools, not code |
| other workspaces | not your job |
