# Classification guide — sorting tester feedback

| Category | It is… | Example |
|---|---|---|
| **Bug** | something behaved wrong or broke | "pull crashed on the 3rd page" |
| **Question** | wants an answer, nothing is broken | "does this work for private groups?" |
| **Idea** | a wish for something new | "could it also export CSV?" |
| **Praise** | kind words, no action | "works great now!" |
| **Noise** | off-topic, reactions, chit-chat | "☕" |

## Severity (bugs only)
- **high** — data loss, crash, wrong data written
- **medium** — feature doesn't work, workaround exists
- **low** — cosmetic, typo, confusing message

## A good issue title
`<what breaks> when <doing what>` — e.g. "Comment timestamps off by years
when pulling old posts". Never "Bug from Dan" or "Feedback #3".

## Judgment calls
- A reply that says "same here" attaches to the parent report, it is not new.
- A bug report that later says "fixed in latest version" → still classify,
  mark as *possibly resolved* — the human decides at the gate.
