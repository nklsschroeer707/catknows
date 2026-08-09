# Stage 01 — run the backup

## Inputs
- (parameter) `community_slug` — ask if not given
- (parameter) `vault_dir` — ask; never assume a location for someone's vault
- (parameter) with comments? — default yes (that's the valuable part)

## Process
1. If `vault_dir` exists and isn't empty: tell the human what's there and
   get their go-ahead first.
2. `pull_to_vault(community_slug, vault_dir, include_comments=…)` — a big
   community takes minutes; that's normal, let it run.
3. Confirm from the returned counts: members, posts, comments, path.

## Outputs
- `latest.md` → `output/` — what was backed up, where, the counts, and the
  date (the backup itself lives in the vault, not here)

## Completion
Done when the counts in `latest.md` match what the tool reported.
No further stages.
