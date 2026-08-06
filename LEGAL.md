# Legal & responsible-use notes

**Read this before using or forking.**

`skoolapi` is an independent, unofficial tool. It is **not affiliated with,
endorsed by, or connected to Skool** in any way. "Skool" is a trademark of its
respective owner.

## What this tool does

It automates *your own logged-in browser session* against Skool's private
internal endpoints — the same requests skool.com's website makes when you use
it. It does not bypass authentication, break encryption, or access anything your
account cannot already see in the browser. It only reads data from communities
**you are a member of or own**.

## Terms of Service

Skool's Terms of Service may restrict automated access, scraping, or use of
their non-public endpoints. **You are responsible for ensuring your use complies
with Skool's ToS and any applicable law.** Using this tool may violate those
Terms and could put your account at risk. The maintainers make no warranty and
accept no liability.

## Responsible use

- Only pull communities you have legitimate access to.
- Only export data you have a lawful basis to process (members' names, emails,
  etc. are **personal data** — GDPR/CCPA and similar laws apply to what you do
  with them).
- Don't hammer Skool's servers. The client sleeps between requests and backs off
  on rate-limit signals — don't remove those safeguards.
- Don't use exported member data for spam, harassment, or resale.

## Stability

These endpoints are undocumented and can change or break at any time without
notice. This is not a supported integration.

## No warranty

Provided "as is", without warranty of any kind. See [LICENSE](LICENSE).
