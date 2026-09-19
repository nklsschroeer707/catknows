# community-pulse — routing

**Purpose:** per community, a measured liveliness reading plus what's
actually being talked about — so a shortlist can be cut down to the ones
worth real attention (joining, watching, checking their ads).
**Stages:** `01_pull` → `02_pulse` `[gate]` → `03_write`

## Load
| Resource | When | Why |
|---|---|---|
| `../../../_shared/references/mcp-tools.md` | always | tool names & args |
| `../../../_shared/references/skool-quirks.md` | stage 01 + 02 | feed sorts by activity not date, the 200-post ceiling, membership gate |
| `../../../_shared/references/output-style.md` | stage 03 | result format, archive rule |

## Do NOT load
| Resource | Why not |
|---|---|
| `docs/API.md`, `catknows/` source | you use tools, not code |
| `../discover-communities/` | it hands you the shortlist; it doesn't do depth |
| `../community-profile/` | the one-page "what is this" — this workspace is the "is it alive" |

## Where this sits
`discover-communities` (or any outside screen: ad library, a list someone
sent you) → **community-pulse** → the few that earn a deeper look.

`community-profile` answers *what a community claims to be*, from the
outside. This one answers *what it actually does*, from the inside — which
is why it needs membership (see stage 01).

## Run it twice
One run gives the rate the feed can reconstruct. The honest **delta** — how
many posts really came in since last time — needs a second run a few days
later against the archived snapshot. Stage 02 does that comparison whenever
an archive exists, so runs are worth repeating rather than starting fresh.
