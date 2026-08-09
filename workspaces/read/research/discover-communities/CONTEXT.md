# discover-communities — routing

**Purpose:** a shortlist of communities worth a look, from Skool's ranked board.
**Stages:** `01_search` (only stage)

## Load
| Resource | When | Why |
|---|---|---|
| `../../../_shared/references/mcp-tools.md` | always | paging works, filters don't — filter locally |
| `../../../_shared/references/output-style.md` | before writing | result format |

## Do NOT load
| Resource | Why not |
|---|---|
| `docs/API.md`, `catknows/` source | you use tools, not code |
| `../community-profile/` | deep-dive is its job — recommend it instead |
