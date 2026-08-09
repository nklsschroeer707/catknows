# Bug report template

One file per bug, `output/bugs/<nn>-<slug>.md`:

```markdown
# <what breaks> when <doing what>

**Seen:** <date, during which workspace/tool run>
**Severity:** high (data loss/crash/wrong data) · medium (broken, workaround
exists) · low (cosmetic)

## What happened
<2–5 sentences, plain language>

## How to reproduce
1. <exact steps — the tool call or workspace stage with its inputs>

## Expected vs actual
- Expected: …
- Actual: … (quote the exact error/traceback lines, trimmed to the
  relevant part)

## Where it probably lives
<one pointer max, e.g. "normalize.py timestamp handling" — from the
traceback, not from reading code>
```

Rules: one bug per report — "and also…" means a second report. A report
someone can't reproduce from is not finished.
