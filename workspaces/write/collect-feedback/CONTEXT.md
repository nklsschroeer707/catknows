# collect-feedback — routing

**Purpose:** tester thread → sorted feedback → GitHub issues (approved ones only).
**Stages:** `01_fetch` → `02_classify` `[gate]` → `03_dispatch`

## Load
| Resource | When | Why |
|---|---|---|
| `../../_shared/references/mcp-tools.md` | stage 01 | tool names & args |
| `references/classification-guide.md` | stage 02 | the sorting rules |
| `../../_shared/references/output-style.md` | before writing | result format |

## Do NOT load
| Resource | Why not |
|---|---|
| `docs/API.md` | you use tools, not code — EXCEPTION: a bug report may quote it; then load only the quoted section |
| `catknows/` source | triage decides WHAT is broken, not WHY |
| other workspaces | not your job |
