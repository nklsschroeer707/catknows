# Privacy Policy — catknows hosted MCP server

**Applies to:** `https://mcp.catknows.app` and `https://auth.catknows.app`.

This is the privacy policy for the **hosted service**. If you run catknows
yourself from the source (locally over stdio, or on your own server), none of
this applies — no data reaches me, and you are your own controller. For the
tool's terms of use and Skool-related caveats, see [LEGAL.md](../LEGAL.md).

Last updated: 2026-08-17.

---

## 1. Who is responsible

    Niklas Schröer
    Am Pickerweg 32
    49401 Damme
    Germany

    nklsschroeer@gmail.com

<!-- This block doubles as the Impressum data (§5 DDG, formerly TMG). Publish it
     as a separate /impressum page too when the landing page goes up — the law
     wants it directly reachable, not only inside another document. -->

Reachable by email at the address above. This is a service run by an individual,
not a company — there is no commercial register entry or VAT ID to state.

I am the controller (GDPR art. 4(7)) for the data described here. There is no
data protection officer — the service is below the thresholds in GDPR art. 37.

## 2. What is processed, and why

### 2.1 Your account

| Data | Why | Legal basis |
|---|---|---|
| Email address (also your username) | Identifies your account, delivers verification and password-reset links | Art. 6(1)(b) — performing the contract you asked for |
| Password (salted hash, never plaintext) | Authentication | Art. 6(1)(b) |
| Failed-login counters and timestamps | Locks an account temporarily after repeated wrong passwords | Art. 6(1)(f) — my legitimate interest in not letting your account be guessed |
| Sessions and OAuth tokens issued to you | Keeps you logged in without re-entering the password | Art. 6(1)(b) |

Stored in a PostgreSQL database on the server described in §4, reachable only
from the machine itself.

### 2.2 Your Skool session

To act on your behalf against Skool, the server stores the Skool cookie you
provide. It is **encrypted at rest** (Fernet / AES-128-CBC with an HMAC), in a
file named after a hash of your account id, readable only by the service user.
It is stored under your account alone and is never used to serve anyone else's
request — a request without a verified identity is refused rather than served
from someone else's session.

Legal basis: art. 6(1)(b). Without it the service cannot do the one thing it
exists for.

**What this cookie is:** a bearer token for your whole Skool account, typically
valid for a year, with no password or 2FA required to use it. Treat it
accordingly. Never send it through a chat with an AI assistant, email, or a
ticket — anything typed to a language model is written to a conversation log
held by that provider. Store it only via the server-side command documented in
[deploy/README.md](README.md) §5, which reads it from a hidden prompt.

You can delete it at any time — see §6.

### 2.3 Skool data passing through

When you call a tool, the server fetches from Skool exactly what you asked for
(members, posts, comments, metrics …) and returns it to your AI client. This
data is **not stored** — it is held in memory for the duration of the request
and, for at most a few minutes, in an in-process read cache.

Two consequences worth stating plainly:

- **This data reaches your AI provider.** Whatever a tool returns goes into your
  conversation with Anthropic (or whichever client you connected). Their privacy
  terms govern it from there, not mine.
- **It contains other people's personal data** — your members' names, handles,
  activity, sometimes emails. For that processing *you* are the controller and I
  am your processor: see the [data processing agreement](DPA.md), which applies
  as soon as you store a Skool session. Only pull data you have a lawful basis to
  process, and see [LEGAL.md](../LEGAL.md) on Skool's terms.

### 2.4 Server logs

The reverse proxy logs request metadata: timestamp, IP address, HTTP method,
path, status code, user agent. `Authorization` and `Cookie` headers are
**stripped before writing** — deliberately, so an access log cannot be replayed
as your session. Request and response bodies are not logged.

Legal basis: art. 6(1)(f) — operating the service, diagnosing faults, spotting
abuse. Rotated at 10 MiB with 5 files kept, so logs age out in normal operation
rather than accumulating indefinitely.

### 2.5 Usage records

The proxy log in §2.4 cannot say *who* made a request: your identity sits in the
bearer token, and that header is stripped before writing. So the application
itself records one line per accepted request, to the server's system journal:

| Data | Why |
|---|---|
| Your account id (the internal identifier, not your email) | Distinguishes your requests from someone else's |
| Timestamp | When |
| Which AI client the token was issued to (e.g. Claude, ChatGPT) | Tells apart your own clients when something misbehaves |

What this is **not**: it does not record which tool you called, what you asked
for, or anything that came back from Skool. The component writing it runs before
any of that is known — it checks the token and nothing else. So it answers "how
much does this account use the service", never "what did they look at".

Legal basis: art. 6(1)(f) — knowing how much capacity is used and by whom, which
is what makes it possible to keep a shared server running and to spot one account
consuming it all. Once the service is paid for, the same figure becomes the basis
for billing, then art. 6(1)(b).

Kept for **30 days**, then deleted by the journal's retention limit. No profiles
are built from it and it is not combined with the Skool data in §2.3.

## 3. What is *not* done

- No analytics, tracking pixels, or advertising.
- No cookies beyond those needed to log you in.
- No sale or sharing of your data, and none passed to third parties beyond the
  processors named in §4.
- No use of your data or your community's data to train AI models.
- No profiling and no automated decision-making with legal effect (art. 22).

## 4. Where it runs, and who else touches it

| Processor | What they do | Where |
|---|---|---|
| **netcup GmbH** | The VPS this all runs on | Nuremberg, Germany |
| **Scaleway SAS** | Sends verification and password-reset mail (Transactional Email) | France / EU |
| **Let's Encrypt (ISRG)** | TLS certificates. Sees the domain name, no user data | USA |

Everything is inside the EU/EEA except certificate issuance, which involves no
personal data. Data processing agreements under GDPR art. 28 are in place with
both processors — netcup's concluded in writing, Scaleway's as the
highest-ranking document of their terms, accepted on sign-up. Both publish a
sub-processor list; both bind their own sub-processors, all inside the EU.


Your AI client (e.g. Anthropic) is **not** my processor — it is the other party
in your own relationship, and receives data because you asked it to call these
tools.

## 5. How long

| Data | Retention |
|---|---|
| Account (email, password hash) | Until you delete it, or 12 months after last login |
| Your stored Skool session | Until you delete it, or 12 months after last use |
| OAuth tokens / sessions | Minutes to days, per token lifetime; then gone |
| Proxy logs | Until rotated out (see §2.4) |
| Usage records (§2.5) | 30 days |
| Failed-login counters | Reset on success, or after 12 hours |

An account that is never confirmed by email is removed after 30 days.

## 6. Your rights

Under GDPR you may request access (art. 15), correction (16), erasure (17),
restriction (18), portability (20), and object to processing based on
legitimate interests (21). Where processing rests on consent, you may withdraw
it at any time.

Two things you can do yourself, immediately:

- **Delete your Skool session** — call the `forget_skool_session` tool from your
  AI client. It is gone from disk when that returns.
- **Delete your account** — email me and it is removed, along with your stored
  session.

For anything else, write to the address in §1. I will answer within one month
(art. 12(3)). No fee, no reason required, and exercising a right will not get
your account degraded.

You may also complain to a supervisory authority (art. 77) — for me that is the
data protection authority of your state of residence or, for my establishment,
the relevant German authority.

## 7. Security

- TLS only, HSTS enabled; the application ports are bound to loopback and
  reachable only through the proxy.
- Your Skool session is encrypted at rest with a key held outside the store.
- Every request is authenticated against an OAuth token this service issued, and
  answered strictly from the session belonging to that identity. A request whose
  identity cannot be verified is refused, not served from a shared session.
- Credential-class fields are stripped from Skool payloads before they leave the
  server, so account secrets cannot end up in an AI context or a log.
- Passwords are stored only as salted hashes; repeated wrong passwords lock an
  account temporarily.

No system is beyond compromise. If yours is affected by a breach, I will notify
the supervisory authority within 72 hours (art. 33) and you directly where the
risk to you is high (art. 34).

## 8. Changes

Material changes will be announced to registered users by email before taking
effect. The date at the top says when this text last changed.
