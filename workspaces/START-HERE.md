# Start here — the Skool job catalog

Every folder here is one agent with one job. Two wings:

- **[read/](read/)** 👀 — only looks. These agents can never change anything
  on Skool or anywhere else. Safe to run anytime.
- **[write/](write/)** ✍️ — acts in your name (posts, DMs, GitHub issues).
  Every write agent lives under **the write rule: nothing is ever actually
  sent, posted, or created until you have explicitly approved the exact
  content. No approval = no write.** Each one drafts first and stops at a
  human gate.

Inside each agent's folder, `CLAUDE.md` says who it is, `stages/` says what
it does, step by step.

## read/ — pick your folder

| Folder | Question it answers |
|---|---|
| [read/members/list-everyone/](read/members/list-everyone/) | Who is in my community? |
| [read/members/know-one-member/](read/members/know-one-member/) | Who is this person? |
| [read/members/find-your-fans/](read/members/find-your-fans/) | Who likes & comments the most? |
| [read/posts/whats-new/](read/posts/whats-new/) | What's being posted right now? |
| [read/posts/read-the-comments/](read/posts/read-the-comments/) | What are people saying under a post? |
| [read/posts/who-liked-it/](read/posts/who-liked-it/) | Who liked a post? |
| [read/messages/check-inbox/](read/messages/check-inbox/) | Who is writing to me? |
| [read/classroom/course-overview/](read/classroom/course-overview/) | What courses does a community have? |
| [read/classroom/classroom-research/](read/classroom/classroom-research/) | What's inside the courses — across communities? |
| [read/research/discover-communities/](read/research/discover-communities/) | Which Skool communities are out there? |
| [read/research/community-profile/](read/research/community-profile/) | What is community X about? (no membership needed) |
| [read/research/watch-the-skoolers/](read/research/watch-the-skoolers/) | What's happening in the official Skoolers community? |
| [read/calendar/whats-coming-up/](read/calendar/whats-coming-up/) | What events are coming up? |
| [read/admin/health-report/](read/admin/health-report/) | How healthy is my community? (owners only) |
| [read/backup/pull-to-vault/](read/backup/pull-to-vault/) | Save everything as Markdown. |
| [read/backup/vault-librarian/](read/backup/vault-librarian/) | File session results into the vault — the knowledge store that grows. * |
| [read/reports/community-report/](read/reports/community-report/) | One report out of all of the above. |

\* The librarian is the one read/ agent that writes locally — into YOUR
vault (never to Skool, never outward), and only after you approved its
filing plan.

## write/ — these act in your name

| Folder | What it does |
|---|---|
| [write/collect-feedback/](write/collect-feedback/) | Tester thread → sorted feedback → GitHub issues |
| [write/publish-a-post/](write/publish-a-post/) | Draft a community post with you, then publish it |
| [write/send-a-message/](write/send-a-message/) | Draft a DM with you, then send it |
| [write/report-a-bug/](write/report-a-bug/) | Document errors → GitHub issues, or a Skool post tagging the maintainer |

Skool write tools only exist when the MCP server runs with
`CATKNOWS_ALLOW_WRITE=1` — without it, `publish-a-post` and `send-a-message`
will tell you so and stop.

## Three rules that make everything work

1. **One folder, one job.** An agent works only inside its folder and runs
   its numbered stages in order (`01_…`, `02_…`). Stage outputs land in that
   stage's `output/` folder.
2. **Every job ends in `latest.md`.** The final stage writes its result to
   `stages/<last>/output/latest.md`. This one convention is what lets
   `reports/` assemble everything without any wiring. (Our addition on top
   of the ICM paper — the file-based handoff it enables is pure ICM.)
3. **You review between stages.** Stages marked `[gate]` stop and wait for
   you — in `write/`, every agent has one. If a result is repeatedly wrong,
   fix that stage's `CONTEXT.md` — never hand-edit the output and move on
   (ICM §6.3).

Shared knowledge (tool cheatsheet, Skool quirks, output style) lives in
[_shared/references/](_shared/references/) — each workspace's CONTEXT.md
says when to load what.

**Make it yours:** copy [_config/me.example.md](_config/me.example.md) to
`_config/me.md` (gitignored) and fill in your communities, your vault path,
your maintainer handle. The standard ships neutral — everything personal
lives in that one local file.
