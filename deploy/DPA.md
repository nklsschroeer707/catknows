# Data Processing Agreement — catknows hosted MCP server

**Auftragsverarbeitungsvertrag nach Art. 28 DSGVO**

Between

**you, the customer** ("controller") — the person or organisation holding a
catknows account

and

    Niklas Schröer
    Am Pickerweg 32
    49401 Damme
    Germany
    nklsschroeer@gmail.com

("processor", "I", "me").

Version 1.0, 2026-08-11. This agreement supplements the
[privacy policy](PRIVACY.md) and takes effect when you first store a Skool
session on the hosted service. It governs only the data described in §2 — for
your own account data (your email address, your password) I am the controller,
not your processor, and the privacy policy applies instead.

---

## 1. Why this exists

When you use catknows against your Skool community, the service reads personal
data about **other people** — your members' names, handles, activity, sometimes
their email addresses. You decide that this happens and for what purpose, so
under the GDPR you are the controller for that data. I only process it because
you instructed me to, which makes me your processor and this contract mandatory
(art. 28(3)).

If you never store a Skool session, no such processing occurs and this agreement
stays dormant.

## 2. Subject matter, scope, duration

| | |
|---|---|
| **Subject matter** | Retrieving data from Skool on your behalf via Skool's own endpoints, and returning it to the AI client you connected |
| **Purpose** | Solely to execute the tool calls you (or your AI assistant acting for you) make. No other use. |
| **Categories of data** | Names, Skool handles, profile data, membership and activity data, points/levels, posts, comments, chat messages, calendar and course data, and — where Skool exposes it to your account — member email addresses |
| **Categories of data subjects** | Members, admins and moderators of the Skool communities you access; authors of posts and comments visible to your account |
| **Duration** | For as long as you keep a Skool session stored. It ends when you delete that session or your account. |
| **Nature of processing** | Retrieval, transient caching, format conversion, transmission to your AI client. No storage of member data (§3). |

**Special categories** (art. 9) are not intentionally processed. Skool profiles
are free text, so a member may of course write something revealing about
themselves in a bio or a post; that content passes through unread and
unclassified. Do not use this service to deliberately compile art. 9 data.

## 3. What I do and do not store

This is the core technical fact of this agreement:

- **Member data is not stored.** It is held in memory for the duration of your
  request and, for at most a few minutes, in an in-process read cache. It is not
  written to a database, not written to disk, and not in any backup.
- **Your Skool session is stored**, encrypted at rest, until you delete it.
- **Proxy logs** record request metadata (time, IP, path, status). Request and
  response bodies are not logged, so member data does not enter the logs.
  `Authorization` and `Cookie` headers are stripped before writing.

Consequence worth being explicit about: because nothing is retained, I usually
**cannot** help you answer a data subject's access or erasure request from my
systems — there is nothing there to search. The data lives in Skool, and that is
where such requests have to be served. What I can do is delete your stored
session, which stops all further processing immediately.

## 4. My obligations

I will:

1. Process the data **only on your documented instructions** (art. 28(3)(a)).
   Your tool calls are those instructions. I will not process it for my own
   purposes, will not sell or share it, and **will not use it to train AI
   models**. If I ever believe an instruction breaks data protection law, I will
   say so instead of quietly executing it.
2. Keep it **confidential** (art. 28(3)(b)). I am currently the only person with
   access to the systems. Anyone I ever grant access to will be bound in writing
   before they get it.
3. Maintain the **security measures** in §5 (art. 32).
4. Engage sub-processors only under §6.
5. **Assist you** with data subject requests, with your own art. 32–36 duties,
   and with supervisory-authority enquiries (art. 28(3)(e),(f)) — within the
   hard limit of §3: I can only assist with what exists.
6. **Notify you without undue delay** of any personal data breach affecting your
   data, with what I know, so you can meet your art. 33 deadline. Realistically
   the breach that matters here is disclosure of your stored Skool session, which
   would put your entire Skool account at risk — you would hear about it from me
   as fast as I can type.
7. **Delete or return** the data at the end (art. 28(3)(g)): deleting your
   session removes everything of yours that persists. There is no member data to
   return, per §3.
8. Provide the **information and audit access** in §8 (art. 28(3)(h)).

## 5. Technical and organisational measures (art. 32)

Current state, honestly described:

- **Transport:** TLS only, HSTS. Application ports bound to loopback; a reverse
  proxy is the only route in.
- **Authentication:** OAuth 2.1 against my own Keycloak. Every request is
  answered strictly from the Skool session belonging to the verified identity in
  the token. A request whose identity cannot be verified is **refused**, never
  served from a shared session.
- **Tenant separation:** one encrypted session file per account, named after a
  hash of the account id, readable only by the service user.
- **Encryption at rest:** your Skool session is encrypted (AES-128-CBC with
  HMAC); the key is held outside the store.
- **Secret scrubbing:** credential-class fields (payment, payout, API keys) are
  stripped from Skool payloads before they leave the server, so they cannot reach
  an AI context or a log.
- **Data minimisation by design:** member data is never persisted (§3). The most
  effective measure here is having nothing to lose.
- **Hardening:** unprivileged service user, systemd sandboxing, no inbound
  access to the application ports, brute-force protection on the login.
- **Logging:** metadata only, credentials stripped, rotated.

Honest limitations, so you can judge the risk yourself:

- This is a **single VPS run by one individual**, not an ISO-27001 operation.
  There is no 24/7 on-call, no formal change management, no penetration test.
- There is **no high availability**. An outage means the service is unavailable,
  not that data is lost.
- The **AI client you connect is outside my control** (§7).

## 6. Sub-processors

| Sub-processor | Role | Location |
|---|---|---|
| netcup GmbH | Hosting (the VPS) | Nuremberg, Germany |
| Scaleway SAS | Transactional email (account verification, password reset) | France |

You give general authorisation for these (art. 28(2)). Both are bound by their
own art. 28 agreements. **Neither receives member data:** netcup hosts the
machine on which nothing member-related is persisted, and Scaleway only ever
sees *your* email address, for messages to you.

I will inform you at least **30 days** before adding or replacing a
sub-processor, where I have that much notice myself — both of mine grant me the
same 30 days, and I pass it on. You may object on reasonable data protection
grounds; if the change is unavoidable and you object, you may terminate and
delete your session.

Both sub-processors are established in the EU and host the data concerned there.
Where either of them engages further sub-processors of its own, its own art. 28
agreement governs that, including the standard contractual clauses required for
any transfer outside the EEA.

## 7. Your AI client is not my sub-processor

Read this twice, because it is the part people get wrong.

Whatever a tool returns goes **straight into your conversation** with your AI
provider (Anthropic, or whichever client you connected). That transfer is
initiated by you, to a party you chose, under your own agreement with them. They
are **not** my sub-processor and I have no contract with them about your data.

For that leg you are responsible for having a lawful basis and, where needed,
your own processing agreement with that provider. If you route member data into
a chat interface, that data is in your provider's conversation logs — I cannot
retract it. Pull what you need, not everything you can.

## 8. Documentation and audits (art. 28(3)(h))

Ask, and I will provide the information needed to demonstrate compliance —
answers about the setup, this document, the privacy policy, and the relevant
source code, which is public and can be read rather than taken on trust.

For an on-site or third-party audit: I will cooperate reasonably, with notice,
during working hours, without disrupting operations, and against confidentiality
undertakings. Given the size of this operation, my own documentation plus the
public source code will usually get you further than an inspection would.

## 9. Liability, term, law

- **Liability** follows the GDPR (art. 82) and applicable law. Nothing here
  limits a data subject's rights or the statutory allocation of liability
  between controller and processor.
- **Term:** as long as you have a session stored. Terminate by deleting your
  session (`forget_skool_session`) or your account.
- **Post-termination:** obligations of confidentiality survive.
- **Law and venue:** German law; my place of business, unless mandatory law says
  otherwise.
- **Form:** this document in its current version applies. Material changes will
  be announced by email before they take effect; if you do not accept them, you
  may terminate as above.
- If any clause is invalid, the rest stays in force and the invalid one is read
  as the closest valid equivalent.

---

## Requesting a signed copy

If your compliance process needs a signed instrument rather than a published
document, write to the address above and I will send this text as a PDF, signed.
State the controller's legal name and address; it will be filled in on the
counterparty side.
