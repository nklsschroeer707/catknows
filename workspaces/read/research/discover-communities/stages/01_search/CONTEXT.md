# Stage 01 — search the board

## Inputs
- (parameter) what they're looking for — topic, price ("free only"), size;
  ask if not given
- (parameter) how deep — default pages 1–3 (~90 communities); "search
  everything" → all 34 pages (takes a while, that's fine)

## Process
1. `get_discovery(page=…)` for each page in scope.
2. Filter locally against the human's criteria (Skool ignores query
   filters — you do the filtering).
3. Table of matches: Rank · Name (slug) · Members · Price · Category ·
   Why it matches (your one-liner).
4. End with: "want a full profile of one of these? → community-profile".

## Outputs
- `latest.md` → `output/`

## Completion
Done when every listed community actually matches the criteria and the
search scope (pages covered) is stated in the file. No further stages.
