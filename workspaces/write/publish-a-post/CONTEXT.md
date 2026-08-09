# publish-a-post — routing

**Purpose:** from idea to published community post, human-approved.
**Stages:** `01_draft` `[gate]` → `02_publish`

## Load
| Resource | When | Why |
|---|---|---|
| `../../_shared/references/mcp-tools.md` | always | create_post args & draft-first flow |
| `../../_shared/references/output-style.md` | stage 01 | keep drafts readable |

## Do NOT load
| Resource | Why not |
|---|---|
| `docs/API.md`, `catknows/` source | you use tools, not code |
| other workspaces | not your job |
