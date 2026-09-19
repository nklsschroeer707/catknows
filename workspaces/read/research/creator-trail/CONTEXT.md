# creator-trail — routing

**Purpose:** per creator, the public picture outside Skool — current offer,
scheduled events, publishing cadence and direction — cross-checked against
what their community shows from the inside.
**Stages:** `01_targets` `[gate]` → `02_trail` → `03_read`

## Load
| Resource | When | Why |
|---|---|---|
| `../../../_shared/references/mcp-tools.md` | stage 01 | the Skool side: owner handle, `links`, calendar |
| `../../../../LEGAL.md` | stage 01 + 02 | the access rule this workspace runs on — what a visitor is served is fair, a wall is a wall |
| `../../../_shared/references/output-style.md` | stage 03 | result format, archive rule |

## Do NOT load
| Resource | Why not |
|---|---|
| `docs/API.md`, `catknows/` source | you use tools, not code |
| `../community-pulse/` | it hands you the targets; its chapters are an input, not something to redo |

## Where this sits
`discover-communities` → `community-pulse` (is it alive, can they pay) →
**creator-trail** (what is the person behind it doing, and what's coming).

Run it after a pulse report, not instead of one: the inside/outside
cross-check in stage 03 is the point of the whole job, and it needs the
pulse chapters to compare against.

## What this workspace is not
It is not a social-media harvester. It looks up a handful of named people
whose communities already earned a look, reads what they publish publicly,
and stops. If the job ever turns into "pull every creator in a category",
that is a different thing wearing this folder's name — say so instead of
running it.
