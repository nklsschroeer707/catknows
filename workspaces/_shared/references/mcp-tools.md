# catknows MCP tools — cheatsheet

The only way workspaces touch Skool. Never import the Python client directly.
`community_slug` is the part after `skool.com/` in the URL.

## Read tools (always available)

| Tool | Args | Returns |
|---|---|---|
| `login_to_skool` | – | call once if other tools report auth errors |
| `list_my_communities` | – | every community YOUR account is in: slug, display name, your role (owner/admin/moderator/member), member count. **Start here when the job has no slug yet** — every other tool needs one |
| `list_members` | slug, limit=25 | name, handle, role, points, level, last-active (most recently active first) |
| `list_posts` | slug, limit=25 | title, author, likes, comment count, content, **post id** |
| `get_post_comments` | slug, post_id | full nested comment thread (post_id from `list_posts`) |
| `get_post_likes` | slug, post_id | users who liked the post |
| `get_member_profile` | user_name, slug | bio, socials, stats (user_name = Skool handle) |
| `get_community_about` | slug | public profile incl. pricing model + `tiers` — works WITHOUT membership (see skool-quirks.md) |
| `get_discovery` | page=1 | one page (~30) of Skool's top-1000 board; pages 1–34; filter locally |
| `get_discovery_rank` | slug | YOUR community's true overall + category rank (owner only, works beyond top 1000) |
| `get_classroom` | slug | compact course list: title, description, module count, access (no module detail — see skool-quirks.md) |
| `get_calendar` | slug, cal_date=0 | events; cal_date = unix ts for a future month |
| `get_admin_metrics` | slug, range="30d" | growth/engagement (members, active, activity series) — owner/admin only; no visitors/conversion/MRR (see skool-quirks.md) |
| `list_chat_channels` | offset, limit=30 | your DM channels: participants, last message, unread (limit above 30 is refused) |
| `read_dms` | channel_id, count=30 | full message history of one channel, oldest→newest: who wrote what, when, plus attachment file names + urls. Pages back through the whole conversation — Skool caps a single read at 50, this walks past it |
| `pull_to_vault` | slug, vault_dir, include_comments | full community → Obsidian Markdown vault |
| `update_catknows` | confirm=false | update the local install from GitHub — draft-first: without confirm it only reports what's new; after an update the human must reconnect their AI client |

## Write tools (exist only when the server runs with CATKNOWS_ALLOW_WRITE=1)

`create_post`, `create_comment`, `send_dm` — all draft-first: the first call
returns a preview, nothing is sent until called again with `confirm=true` after
the human approved the exact text. Never set `notify_members` (it emails
everyone) unless the human explicitly asked for that.

`create_comment(slug, post_id, content, parent_comment_id="")` covers both
cases: leave `parent_comment_id` empty to comment under the post, set it to a
comment id (from `get_post_comments`) to reply beneath that comment. `post_id`
stays the post either way.

All three take `attachments`: a comma-separated list of **local file paths**
(images, PDFs, …). Nothing is uploaded while drafting — the preview just lists
each file's name, type and size, and a path that doesn't exist fails there
rather than halfway through a confirmed post.

## Rules of thumb

- Avoid `raw=true` — raw payloads are huge and blow the tool-result cap.
- Big result got saved to a file instead? Query it with a script, don't read
  it whole.
- Keep limits small first; raise only when the job needs more.
- Reads are cached ~10 min in the server: calling the same tool twice is
  free — don't hoard results "to save requests". Chat channels are always
  live, and any write clears the cache. Guaranteed-fresh data →
  `CATKNOWS_CACHE_TTL=0` in the server env.
