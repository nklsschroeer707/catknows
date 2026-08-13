# Product

## Register

brand

## Users

Skool community owners and admins (non-technical to semi-technical), arriving
as prospective beta testers. They run a Skool community and already use an AI
assistant (mostly Claude). Context: they land on catknows.app from a DM, a
Skool post, or a demo video, and decide within thirty seconds whether to sign
up. Job to be done: ask questions about their own community (members, posts,
engagement) without exporting spreadsheets first.

## Product Purpose

catknows connects a Skool community to the AI the owner already uses — as a
hosted MCP server (mcp.catknows.app) or self-hosted open source. The landing
page has a single job: answer four questions in thirty seconds — what does it
do, what does it cost (today: nothing), how do I start (register → confirm
email → connect Skool), what happens to my data (German hosting, legal pages)
— and route the visitor into sign-up. Success = a stranger finds the way in
without help.

## Brand Personality

Warm, clear, honest. First-contact voice per BRAND.md: no hype, typo-free,
"Inge-safe". Playful cat identity (the whisker mark with the heart nose) over
sober German-hosted engineering. The canonical one-liner is used verbatim on
first-contact surfaces: "catknows. — Your Skool data, in your AI." Message
hierarchy: benefit → free (always anchored, never free-floating) → local &
ownership → open source as the proof.

## Anti-references

- Award-style scroll choreography. The page is one screen — a deliberate
  decision against long-scroll "One Page" award winners (researched 2026-08-13:
  no awarded no-scroll pages exist; bearblog.dev is the only real reference).
- Motion as a substitute for a claim (runaway buttons, 8-bit sound gimmicks).
- AI-slop grammar: purple gradients, three identical cards, Inter-as-default,
  centered-hero boilerplate, eyebrow labels over every section.
- SaaS pricing tropes. There is no pricing today; promise nothing that does
  not exist in shipped code.

## Design Principles

1. **One screen, one decision.** Everything on the page serves the path to
   sign-up; anything that doesn't gets cut, and cuts are named, not silent.
2. **Mobile is the real single screen.** 667 px height is the constraint;
   desktop is the easy case.
3. **Show, don't describe.** The typed demo (a real question, a real answer)
   is the proof element — not testimonials, not feature grids.
4. **Honesty over polish.** Every promise is covered by a shipped code path;
   when in doubt, say less (BRAND.md rule).
5. **The mark is the magic.** The animated whisker face carries the brand;
   everything around it stays quiet.

## Accessibility & Inclusion

WCAG AA contrast (body ≥ 4.5:1 — current tokens are already tuned for this,
e.g. --ink-dim at 5.9:1), full prefers-reduced-motion alternatives for every
animation, semantic HTML, keyboard-reachable CTA. English copy; German legal
pages (Impressum per §5 DDG).
