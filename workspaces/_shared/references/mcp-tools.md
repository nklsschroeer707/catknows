# catknows MCP tools — cheatsheet

The only way workspaces touch Skool. Never import the Python client directly.
`community_slug` is the part after `skool.com/` in the URL.

## Read tools (always available)

| Tool | Args | Returns |
|---|---|---|
| `login_to_skool` | – | call once if other tools report auth errors |
| `list_my_communities` | – | every community YOUR account is in: slug, display name, your role (owner/admin/moderator/member), member count. **Start here when the job has no slug yet** — every other tool needs one |
| `list_members` | slug, limit=25, offset=0 | name, handle, role, points, level, last-active (most recently active first) |
| `list_posts` | slug, limit=25, sort=activity\|new\|top_day\|top_week\|top_month\|top_year\|top_all, unread_only=false, category="", author="", offset=0 | title, author, likes, comment count, content, **post id**, `category`, `has_new_comments`. **Filter here instead of reading everything:** `unread_only` = only posts you haven't opened, `category` by name ("feedback"), `author` = every post one member created (@handle or full name) — use this, not `search_community`, for "all posts by X" |
| `search_community` | slug, query, kind=posts\|members, page=1 | Skool's own search in one community: posts (10 per page, `pages`) or members (same fields as list_members + `member_id`; matches name or bio). `search_members` is the quick name lookup |
| `search_members` | slug, query, limit=7, raw=false | find people by name with Skool's `@mention` search: the same fields as `list_members` (id, handle, name, points, level, bio), **no email or other contact data**. `query` is required (a person search, not a member list). catknows asks Skool for 7 hits and there is no paging (it sends only the request Skool's own page sends); `limit` (1 to 7) only shortens the answer, a larger value is lowered to 7 and a `limit_capped` entry says so, so **narrow the query** to find someone further down. `has_more` comes back only if Skool sends it. For a member's id on a membership (approve, role) use `search_community kind=members`; `role` and `group_id` here are passed on as delivered, and an empty one means "not delivered", not "no role". Names and bios are personal data: use them for the task, do not collect them. Not run live yet (docs/API.md §5.13) |
| `get_file_download_url` | file_id, expire=0 | a download link for ONE uploaded file (document, image) by its file id, as `download_url`. **It is an access link:** fetch what you need, never paste it into a post, DM or note, never keep it; catknows writes it nowhere (no audit line, no error text). Try `get_post`'s `files[].url` first. Only one `https://` text without white space counts as a link; anything else (HTML, `in-progress`, `http://`) is an error that does not show the answer. `expire` 0 = not sent; Skool's name for it is `expireSecs` but the unit and the range are **not verified**. Files only, no video (admin right in Skool, not offered). Not run live yet (docs/API.md §5.13) |
| `get_post` | slug, post_name | ONE post incl. file attachments (name, type, download URL) + video ids. The post LIST carries only attachment ids — the downloadable URL exists **only here** |
| `get_post_comments` | slug, post_id | full nested comment thread (post_id from `list_posts`) |
| `get_post_likes` | slug, post_id, limit=10, cursor="", tab="upvotes" | ONE page of the users who liked the post: `likes` (handle, first/last name), `has_more`, `cursor`, `total_upvotes`, `total_downvotes`. **Everyone, not just 10:** call again with `cursor` = the `cursor` of the last answer until `has_more` is false. The website asks for 10; the highest `limit` Skool accepts is not measured |
| `get_video_transcript` | slug, post_name | spoken transcript of a post's Skool-hosted video(s): full text + timestamped cues. Silent videos return has_transcript=false; embedded YouTube/Loom/Vimeo is NOT covered (see skool-quirks.md) |
| `get_member_profile` | user_name, slug | bio, socials, stats (user_name = Skool handle) |
| `get_follows` | user_name, direction=followers\|following, limit=1000, offset=0 | who follows that user / whom they follow: name, handle, bio, location, `you_follow`. Works on any profile, no slug needed. `total` = Skool's count |
| `get_community_about` | slug | public profile incl. pricing model + `tiers` — works WITHOUT membership (see skool-quirks.md) |
| `get_community_rules` | slug | the community's rules as Skool sends them: **raw** (through the secret scrub, nothing renamed or filled in). The page reads a `rules` list with a `title` and a `content` per rule, but the answer's shape was **not run live**. It is what every member sees. Archived communities stay readable. Writing rules is not available. Not run live yet (docs/API.md §1.11) |
| `get_community_levels` | slug | what the community calls its levels, as Skool sends it: **raw**, through the scrub, not reshaped (the Leaderboards page shows `[{number, title, …}]` to every member; that this call answers the same is **not verified**). `get_leaderboard` carries level names with each level's share of members and unlocks too. Renaming a level is not available. Not run live yet (docs/API.md §1.11) |
| `get_discovery` | page=1 | one page (~30) of Skool's top-1000 board; pages 1–34; filter locally |
| `get_revenue_leaderboard` | category="", limit=100 | Skool's top-earning communities (the Skoolers "games" board): top 100 overall or top 50 of a category (name like "hobbies"). Global + category rank, community, owner with revenue badge, `mrr_usd`, `mrr_growth_usd`, traffic. Nothing beyond those |
| `get_leaderboard` | slug, period="all"\|"30d"\|"7d" | a community's points leaderboard as on its Leaderboards page: top 10 (all Skool shows) with rank, handle, name, points in the period, level; `me` = your rank, level, points to the next level (rank null for owners/admins); `levels` = level names, share of members, unlocks. Private communities: members only |
| `get_discovery_rank` | slug | YOUR community's true overall + category rank (owner only, works beyond top 1000) |
| `get_classroom` | slug | compact course list: title, description, module count, access (no module detail — see skool-quirks.md) |
| `get_course_tree` | course_id, community_slug="" | one course's full structure: folders + pages with ids, titles, order, draft state. `course_id` from `get_classroom`. Page bodies live here, not in the course list; child order = display order. **Pass `community_slug` for a community you have not joined** — Skool's API refuses those, and only the classroom page shows what the community publishes (see skool-quirks.md) |
| `get_calendar` | slug, cal_date=0 | events; cal_date = unix ts for a future month |
| `get_admin_metrics` | slug, range="30d" | growth/engagement (members, active, activity series) — owner/admin only; visitors/conversion/sources are in `get_growth` |
| `list_join_requests` | slug, query="", page=1, reveal_email=false | pending join requests (owner/moderator): name, bio, when, location, source, Skool's `risk_flag`, every answered join question, `member_id`. Email addresses (`email_answer`, email-type answers, any address in an answer) come out **masked** (`a***@example.com`); `reveal_email=true` prints them in full — only when the owner asked for the addresses. `query` searches names and answers as printed, so a masked call finds no address |
| `get_growth` | slug, charts="signups_by_source,signups_by_day,members" | the admin dashboard, last 30 days: visitors, signups, `conversion_rate`, new MRR, member count + charts (sources, per day, members per month; also retention_members, retention_cohorts, mrr, mrr_cashflow, mrr_unit). Owner/admin only |
| `list_chat_channels` | offset, limit=30, unread_only=false | your DM channels: participants, last message, unread (limit above 30 is refused). `unread_only=true` = only channels with unread messages; none unread gives an empty `channels` list |
| `read_dms` | channel_id, count=30 | full message history of one channel, oldest→newest: who wrote what, when, plus attachment file names + urls. Pages back through the whole conversation — Skool caps a single read at 50, this walks past it |
| `get_notifications` | limit=30, category="", community_slug="", cursor="" | your bell, newest first: kind, who, when, community slug, text preview, `target` (post_id, comment_id, post_name, url). **"What just happened?" starts here**, then read each target with `get_post` / `get_post_comments`. Does not mark anything read. limit max 30, older via `cursor`; category = mentions\|comments\|following\|requests\|new_posts\|recordings |
| `pull_to_vault` | slug, vault_dir, include_comments | full community → Obsidian Markdown vault. Stops with an error (in the comments loop: how far it got) on HTTP 401/403/429, a repeated 202 or Skool's security question and requests nothing more: tell the user, don't restart it |
| `update_catknows` | confirm=false | update the local install from GitHub — draft-first: without confirm it only reports what's new; after an update the human must reconnect their AI client |

On the **hosted** server (mcp.catknows.app) one more read tool appears:
`forget_skool_session` deletes YOUR stored Skool session — tools stop working
until a new one is stored. It does not exist in a local install.

## Write tools (exist only when the server runs with CATKNOWS_ALLOW_WRITE=1)

`create_post`, `create_comment`, `send_dm`, `review_join_request` (approve/decline one `member_id` from `list_join_requests`) — all draft-first: the first call
returns a preview, nothing is sent until called again with `confirm=true` after
the human approved the exact text. Never set `notify_members` (it emails
everyone) unless the human explicitly asked for that.

`create_comment(slug, post_id, content, parent_comment_id="")` covers both
cases: leave `parent_comment_id` empty to comment under the post, set it to a
comment id (from `get_post_comments`) to reply beneath that comment. `post_id`
stays the post either way.

`create_post`, `create_comment` and `send_dm` take `attachments`: a
comma-separated list of **local file paths** (images, PDFs, …). Nothing is
uploaded while drafting — the preview just lists each file's name, type and
size, and a path that doesn't exist fails there rather than halfway through a
confirmed post.

### Changing and removing what exists

Same draft-first rule throughout: `confirm=false` changes nothing and returns
the current text next to the proposed one.

| Tool | Args | Does |
|---|---|---|
| `edit_post` | slug, post_id, title, content | edit title and/or body. Empty string = **unchanged**, not "blank it"; category and attachments survive |
| `edit_comment` | slug, comment_id, content | edit one comment/reply (`comment_id` from `get_post_comments`) |
| `delete_post` | slug, post_id, confirm | PERMANENT. The preview shows title + text so the human sees what disappears. Deleting someone else's post needs moderator rights and is untested |
| `delete_comment` | slug, comment_id, confirm | PERMANENT, same preview rule |

### Classroom

Ids come from `get_classroom` (courses) and `get_course_tree` (folders, pages).

| Tool | Args | Does |
|---|---|---|
| `create_course` | slug, title, description, privacy=0, min_access_level, min_tier, draft=true | new top-level course tile. privacy: 0 all members, 1 unlocks at level `min_access_level`, 2 private/tier-gated. `draft=true` keeps it INVISIBLE until `publish_course` |
| `create_course_item` | course_id, title, parent_id, folder=false, content, video_link | folder (`folder=true`) or page. `parent_id` empty = course root. `content` takes plain text; `video_link` embeds YouTube/Loom/Vimeo |
| `update_course_item` | item_id, title, content, privacy, … | rename/rewrite. For PAGES pass title AND content together — Skool drops omitted page fields, so read current values from `get_course_tree` first |
| `publish_course` | course_id, published=true | publish (**visible to all members**) or pull back to hidden draft |
| `move_course_item` | item_id, position | reorder among siblings (0-based). Does not change the parent: to move a page into a folder or out of it use `move_course_to_folder` (below; read from Skool's page code, **not run live yet**). Until that is tried, recreating the page in the target folder is the proven way |
| `delete_course_item` | item_id, client_id, confirm | PERMANENT. Deleting a COURSE needs `client_id` (32-hex, email-code check, docs/API.md §7.6). Deleting a FOLDER does not delete its pages — they move to the course root |

### Course settings, share key and import

Same draft-first rule: `confirm=false` changes nothing **and does not contact
Skool**, and every draft carries a fixed `not_verified` text: **show it to the human
with the draft.** Read from Skool's page code, **not yet run live** — try a throwaway
course in a private community first. Ids must be real text, not only dots.

| Tool | Args | Does |
|---|---|---|
| `set_course_drip` | course_id, enabled, days=0, confirm | drip (timed release) on or off for ONE page: with drip on, a member sees the page `days` days after joining. Skool's web client offers it in a **page's** menu, so `course_id` is a page id (from `get_course_tree`). `enabled=true` needs `days` 1 to 999, `enabled=false` needs none (Skool demands a number there too, catknows sends 1). No default direction. The draft shows the id, not the current setting |
| `move_course_to_folder` | course_id, parent_id, confirm | move a page into a folder, or back to the course root. `parent_id` = the folder's id (type `set` in `get_course_tree`), or the id of the course itself for "no folder". Sets the parent only: **where the page lands among its new siblings is not known**, `move_course_item` reorders afterwards. The web client moves pages only, never a folder into a folder; the two ids must differ |
| `create_course_share_key` | course_id, confirm | Skool's "Share course key" (owner only in the web client): returns the key as `code`. **The key is a credential**: whoever holds it can duplicate the whole course into any community. Give it only to the person the human names; never post, store or paste it. catknows keeps it out of its audit log and error texts; the answer of this tool is the one place it appears. Whether a second call gives the same key is not known |
| `import_course` | course_id, slug, clone_code, confirm | import a course INTO the community with a key its owner gave to the human. `clone_code` = the 64-character key; `course_id` = the **source** course = the first 32 characters of the key, and it must match (Skool's own form derives it that way). The draft shows the key's length, never the key. Skool copies on its side (1 import at a time); **whether the copy arrives as a draft or already visible to members is not known**: check `get_classroom` afterwards and unpublish it with `publish_course` if needed. Archived communities are refused |

Open: whether a
course or folder id works for the drip as well as a page id, where a moved page lands,
and whether importing needs a paid plan. Details: docs/API.md §7.8.

### Calendar

Same draft-first rule: `confirm=false` changes nothing **and does not contact
Skool**; the draft shows what would be sent and carries a fixed `not_verified`
text: **show it to the human with the draft.** Read from Skool's page code, **not
yet run live** — try a throwaway event in a private community first. The web
client offers these to community admins only. `event_id` must be real text, not
only dots; `from_occurrence_id`, when given, non-blank text.

| Tool | Args | Does |
|---|---|---|
| `create_calendar_event` | slug, title, start_time, end_time, description, timezone, reminder_disabled=-1, location_type=0, location_info, privacy, recurrence, confirm | new event. title ≤ 30 characters, description ≤ 300. Empty / 0 / -1 = not sent. `reminder_disabled`: 0 = reminder email 1 day before, 1 = none. `location_type`: 1 Zoom, 2 Meet, 3 Address, 4 Link (`location_info` = link or address), 5 Skool call, 6 Skool webinar (**5 and 6 take no `location_info`**). `privacy` and `recurrence` are JSON text (keys in the tool's description). `start_time`, `end_time`, `timezone` go to Skool exactly as passed: their format is not documented, nothing is converted. The draft's `event` is the request body, with `group_id` as a placeholder (looked up from the slug only on confirm) |
| `update_calendar_event` | slug, event_id, the same fields, from_occurrence_id, confirm | change an event. Only what you set is sent, and Skool's own form always sends everything: **whether an omitted field is kept is not verified**, so pass every field the event should keep. `from_occurrence_id` empty = the whole event, set = that occurrence and all later ones of the series. The draft shows ids and, as `event`, the request body (the new values; `group_id` is a placeholder, looked up from the slug only on confirm), not the current event |
| `delete_calendar_event` | slug, event_id, from_occurrence_id, confirm | PERMANENT. `from_occurrence_id` empty = the whole event, every occurrence; set = that occurrence and all later ones. The draft shows the id and the scope, not the title — make sure the human knows which event it is. One occurrence alone cannot be changed or deleted (not built) |

Open: which fields Skool requires, the time format, and where `event_id` /
`from_occurrence_id` come from (`get_calendar` lists neither in its compact
form; whether `raw=true` carries them is not checked). Details: docs/API.md §5.11.

### Post actions, categories and the About text

Same draft-first rule: `confirm=false` changes nothing **and does not contact
Skool**; the draft shows what would be sent. For every action with a side effect
nobody has checked, the draft also carries a fixed `not_verified` text: **show it
to the human with the draft.** Read from Skool's page code, **not yet run live**
(the vote, pin, category and About calls also run in the author's own autopilot).
Every id must be real text, not only dots. The drafts show ids and the new text,
not the post's title or the current name, so make sure the human knows which post
or category it is.

| Tool | Args | Does |
|---|---|---|
| `vote_post` | slug, post_id, old="", new="up", confirm | like a post (`old=""`, `new="up"`) or take the like back (`old="up"`, `new=""`). Those are the only two values; no downvote; `old` and `new` must differ |
| `pin_post` | slug, post_id, pinned=true, confirm | pin a post to the top of the feed, `pinned=false` unpins it. Group admins in the web client. Pinning to a course page is not built |
| `create_category` | slug, display_name, default_sort, post_write_role, confirm | new feed category. Name ≤ 30 characters and 10 categories are the category form's limits (the server's are not known); the name is public. `default_sort`: `newest-cm` (Default), `newest`, `best-1w`, `best-1m`. `post_write_role`: 4 members and admins may post, 3 admins only. **No defaults, pick them with the human.** The answer carries the new id |
| `rename_category` | slug, category_id, display_name, confirm | rename one category. `category_id` is the `category_id` of a `list_posts` row. Only the name is sent; **whether Skool keeps the sort and write role is not verified.** Re-sorting a category is not built |
| `delete_category` | slug, category_id, migrate_to="", confirm | **PERMANENTLY** delete one category. `migrate_to` is the id of another category: its posts move there first. Leave it empty only for a category without posts (Skool's form makes you move them first). The draft shows ids only, not the name: make sure the human knows which category it is |
| `update_about_description` | slug, description, confirm | **REPLACES** the public About text with exactly `description` (a blank text is refused). The format Skool wants (plain text or `[v2]` TipTap) is not documented; Skool's editor stops at 1000 plain characters. The draft shows the new text only, so **read the current About text first and show both to the human**: the compact `get_community_about` does not carry it, `get_community_about` with `raw=true` does (`pageProps.currentGroup.metadata.lpDescription`) but is large and may exceed the tool-result size limit |
| `set_post_comments` | slug, post_id, enabled, confirm | comments off (`enabled=false`) or on for one post. No default. At least admins and moderators in the web client (the post's author: not known) |
| `pin_comment` | slug, post_id, comment_id, pinned=true, confirm | pin a comment under its post (`comment_id` from `get_post_comments`), `pinned=false` unpins. The web client allows one pinned comment per post; **what a second pin does is not verified** |
| `follow_post` | slug, post_id, follow=true, confirm | follow a post (the bell), `follow=false` stops |

Open: why the autopilot's `pinPost` got HTTP 400 once in 8 days (catknows sends
`{}` like the autopilot, Skool's own client sends no body), what Skool does with a
vote whose `old` is wrong, who the server really lets pin or switch comments,
and what each call answers. Details: docs/API.md §5.12.

### Chat read marks

Same draft-first rule: `confirm=false` changes nothing **and does not contact
Skool**, and the draft carries a fixed `not_verified` text: **show it to the human
with the draft.** Read from Skool's page code, **not yet run live**. Both change
what looks *unread*: anything that polls unread chats (`list_chat_channels` with
`unread_only`) stops seeing them, so do not use them inside a flow that waits for
unread chats. Ids must be real text, not only dots.

| Tool | Args | Does |
|---|---|---|
| `mark_chat_read` | channel_id, last="", confirm | mark ONE chat as read (`channel_id` from `list_chat_channels`). `last` is optional and sent as given: Skool's page sends the id of a chat message there, and nothing for the whole chat; **what the server does with it is not verified.** The draft shows the ids, not the participants |
| `mark_all_chats_read` | confirm | marks **EVERY chat of the account** as read at once, in all communities: no channel, no community can be named. Say that in plain words to the human before confirming. Afterwards nothing looks unread |

Open: what `last` does, whether the other person sees a read mark, and what each call
answers. Details: docs/API.md §5.13.

## Rules of thumb

- `post_name` is NOT `post_id`. `get_post` and `get_video_transcript` want the
  URL slug (the `name` field from `list_posts`); every other post tool wants the
  Skool id. Both come from `list_posts` — pick the right column.
- Avoid `raw=true` — raw payloads are huge and blow the tool-result cap.
- Big result got saved to a file instead? Query it with a script, don't read
  it whole.
- Keep limits small first; raise only when the job needs more.
- Reads are cached ~10 min in the server: calling the same tool twice is
  free — don't hoard results "to save requests". Chat channels are always
  live, and any write clears the cache. Guaranteed-fresh data →
  `CATKNOWS_CACHE_TTL=0` in the server env.
