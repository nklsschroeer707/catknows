# Deploying the catknows MCP server

Phase 0/1 of [docs/HOSTED_MCP_PLAN.md](../docs/HOSTED_MCP_PLAN.md): one VPS,
Caddy in front, the MCP server on loopback behind it.

**Target:** netcup VPS 500 (2 vCore, 4 GB, 128 GB NVMe), location **Nürnberg**,
Ubuntu 24.04. Domain **catknows.app**, MCP endpoint on `mcp.catknows.app`.

Why 128 GB and not a 40 GB plan: each user keeps a persistent Chromium profile
(plan §2a). A real one measures ~130 MB, plus ~700 MB for the Chromium install
itself — 40 GB runs out at a few hundred users, and moving a box full of
encrypted sessions later is the migration you don't want.

> **Read this before you start.** The MCP transport has no authentication of its
> own — anything reaching port 8000 is trusted. That is why the server binds
> `127.0.0.1` and Caddy is the only way in: never open that port in the firewall.
>
> Requests arriving *through* Caddy are authenticated (Keycloak OAuth, §7) and
> answered per user (§5), so more than one person can use this safely. What still
> has to be true before you hand out an account: registration stays closed,
> [PRIVACY.md](PRIVACY.md) and [DPA.md](DPA.md) are published where users can
> reach them, and the sub-processor agreements named in them are verified as
> actually in place (both files carry a TODO where that is still open).

## 0. Install the OS

netcup ships the VPS without an OS — there is no OS choice during checkout,
and none in the CCP (that panel is for contracts and invoices). It's in the
**[SCP](https://www.servercontrolpanel.de/)**, which arrives as its own mail
with separate credentials: *Media → Images → Ubuntu 24.04 LTS → install*.

Take the plain image, not one with a hosting panel (Plesk/cPanel): those claim
ports 80 and 443, which Caddy needs.

## 1. DNS

Point an A record at the server before installing Caddy — the TLS challenge
needs it:

```
mcp.catknows.app.   A   <server-ip>
```

## 2. Lock down SSH — before anything else

A fresh VPS is reachable from the whole internet the moment it boots, and
password logins get probed within hours. Do this first, from your own machine:

```bash
ssh-copy-id root@<server-ip>          # or paste your pubkey into the panel
ssh root@<server-ip> 'echo key login works'
```

Only once the key works, turn the password off — locking yourself out is the
one mistake here that costs a reinstall:

```bash
sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
# Ubuntu 24.04 splits config into sshd_config.d/ — a leftover file there wins.
grep -rlE '^\s*PasswordAuthentication\s+yes' /etc/ssh/sshd_config.d/ 2>/dev/null
sshd -t && systemctl reload ssh       # sshd -t first: never reload a broken config
```

Keep the current session open and confirm a *new* one still connects before
closing it.

```bash
apt update && apt upgrade -y
apt install -y unattended-upgrades && dpkg-reconfigure -plow unattended-upgrades
```

## 3. Server basics

```bash
adduser --system --group --home /opt/catknows catknows
mkdir -p /var/lib/catknows /etc/catknows
chown -R catknows:catknows /var/lib/catknows

ufw allow OpenSSH && ufw allow 80,443/tcp && ufw --force enable
# Port 8000 stays closed on purpose. Caddy reaches it over loopback.
```

## 4. catknows itself

```bash
apt update && apt install -y python3-venv git
sudo -u catknows git clone https://github.com/nklsschroeer707/catknows.git /opt/catknows
cd /opt/catknows
sudo -u catknows python3 -m venv .venv
sudo -u catknows .venv/bin/pip install -e ".[mcp]"

# Chromium + its system libraries. --with-deps needs root; the browser lands
# in PLAYWRIGHT_BROWSERS_PATH so the service user can read it.
sudo -u catknows PLAYWRIGHT_BROWSERS_PATH=/var/lib/catknows/browsers \
  .venv/bin/playwright install chromium
PLAYWRIGHT_BROWSERS_PATH=/var/lib/catknows/browsers \
  .venv/bin/playwright install-deps chromium
```

## 5. The Skool session

Two modes, and the choice decides whether anyone but you may use this server.

### Per-user sessions (required before anyone else logs in)

With `CATKNOWS_SESSION_DIR` set, each user stores their own Skool session and
every request is served from the one belonging to its OAuth subject. A request
without a verified user is refused — there is no shared session to fall back
to. Sessions are encrypted at rest with a key you generate once:

```bash
install -d -m 700 -o catknows -g catknows /var/lib/catknows/sessions

# Both go in /etc/catknows/env, never in the unit file — `systemctl show`
# prints Environment= lines to any user.
echo 'CATKNOWS_SESSION_DIR=/var/lib/catknows/sessions' >> /etc/catknows/env
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
echo 'CATKNOWS_SESSION_KEY=<that key>' >> /etc/catknows/env

systemctl restart catknows-mcp
```

Losing the key means every stored session becomes unreadable and each user
stores theirs again — annoying, not dangerous. Back it up wherever the rest of
your secrets live.

#### Onboarding a user

Self-signup is off (`registrationAllowed=false`) — see the reasoning in
`keycloak/setup-realm.sh`. Onboarding is two steps, both yours:

1. **Create the account:** admin console → realm `catknows` → Users → Add user
   (email as username, then *Credentials* → set a temporary password, and
   *Email verified* off so they confirm it). Copy the user's `ID` — that UUID is
   the OAuth subject the session store keys on.
2. **Send them to `catknows.app/connect`** — they sign in and connect Skool
   themselves through the streamed browser (§8). Nothing left for you to do.

Until step 2 happens, their tools all refuse: an account with no stored session
reaches no data at all. That's the intended order — an account alone is
harmless.

#### Getting a session in by hand

The streamed login (§8) is the normal route. This is the fallback for when it
can't be used — no browser to hand, a debugging session, or the dashboard is
down. Do it **on the box**, as the service user:

```bash
sudo -u catknows CATKNOWS_SESSION_DIR=/var/lib/catknows/sessions \
  CATKNOWS_SESSION_KEY=<that key> \
  /opt/catknows/.venv/bin/catknows-session store <keycloak-user-id>
```

The prompt is hidden, so the cookie stays out of the shell history and out of
`ps`. It wants the **whole `Cookie:` request header** (DevTools → Network →
any request → Request Headers), not the bare token — the `aws-waf-token` in
there is what keeps Skool's WAF from 403'ing the paginated endpoints.

> **Never paste a Skool cookie into a chat with an AI.** It is a year-long
> bearer token for the whole account: no password, no 2FA. Anything typed to a
> model goes into a conversation log. That is why storing a session is a CLI
> command and **not** an MCP tool — a tool taking a cookie invites exactly that
> paste. A cookie that ever crossed a chat should be killed by logging out of
> all devices in Skool.

`forget_skool_session` (or `catknows-session delete <subject>`) removes a
stored session again — that one is safe as a tool, it carries no secret.

> Leave `CATKNOWS_COOKIE` unset in this mode — with the store on it is ignored,
> and keeping it around only invites confusion about whose data is served.

### Rate limiting and brute force

There is deliberately **no fail2ban and no Caddy rate-limit module** here.

Caddy can't rate-limit without a plugin, which means maintaining a custom build.
What that build would be guarding is already guarded elsewhere:

| Attack | Handled by |
|---|---|
| Guessing a password | Keycloak's own brute-force detection: 5 failures → 60 s, doubling to 900 s, plus a floor on how fast attempts may arrive. Per account, seen directly rather than parsed out of a log. |
| Mass account creation | Registration is closed (above). |
| Reaching data without a session | The session store refuses — no identity, or no stored session, means no request is served. |

fail2ban would add IP-level defence against *distributed* password guessing
across many accounts, at the cost of a regex over Caddy's console-format logs —
which deliberately omit the `Authorization` and `Cookie` headers. Worth adding
when registration opens up; not before.

### Single-session (one operator, your own data only)

Without `CATKNOWS_SESSION_DIR`, one Skool session serves every request. Fine
while you are the only one with an account; **do not** hand out access to
anyone else in this mode — they would see your community, your DMs, your
metrics.

**A server has no display, so the login window can't open** — the server raises
a clear error instead of hanging. Seed the session one of two ways:

- **Cookie (simplest):** put your Skool `Cookie` header in `/etc/catknows/env`:
  ```bash
  install -m 600 -o catknows -g catknows /dev/null /etc/catknows/env
  echo 'CATKNOWS_COOKIE=auth_token=...' >> /etc/catknows/env
  ```
- **Profile:** run the stdio server once on a machine with a display, then copy
  `~/.catknows/skool-profile` to `/var/lib/catknows/skool-profile`
  (`chown -R catknows:catknows` afterwards).

Either way the file holds a live session — `chmod 600`, never in git, never in
a backup that leaves the box.

## 6. Services

```bash
cp deploy/catknows-mcp.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now catknows-mcp
```

Caddy isn't in Ubuntu's archive — add the upstream repo first:

```bash
apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  > /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install -y caddy

# The Caddyfile logs here; the package does not create it, and Caddy refuses
# the config with "permission denied" if it can't open the file.
mkdir -p /var/log/caddy && chown -R caddy:caddy /var/log/caddy

cp deploy/Caddyfile /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl restart caddy
```

## 7. Check it

```bash
systemctl status catknows-mcp
curl -sS -X POST https://mcp.catknows.app/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{
       "protocolVersion":"2025-06-18","capabilities":{},
       "clientInfo":{"name":"curl","version":"0"}}}'
```

A `serverInfo` with `"name":"catknows"` means the whole chain works. Then point
the MCP Inspector at `https://mcp.catknows.app/mcp` (transport: Streamable
HTTP), and after that add it as a connector on claude.ai.

## 8. The dashboard and the streamed Skool login

Plan §2a: a browser opens **on the server**, is streamed to the user, and they
log in to Skool inside it. Their password goes to Skool, never here — which is
also why Google/Apple SSO simply works: from Skool's side it is a real browser.

Why it has to be this way: Skool's `auth_token` is httpOnly and its WAF challenge
only solves in a real browser, and a login on `skool.com` in the *user's own*
browser sets that cookie on Skool's origin where this domain can never read it.
There is no OAuth redirect to borrow.

### Setup

```bash
# 1. Register the dashboard as a Keycloak client (public + PKCE, exact redirect)
cd /opt/catknows/deploy/keycloak
docker compose exec -T -e DASHBOARD_URL=https://catknows.app \
  keycloak bash < setup-dashboard-client.sh

# 2. Its env, alongside the session store's (same file, same key)
cat >> /etc/catknows/env <<'EOF'
CATKNOWS_DASHBOARD_CLIENT_ID=catknows-dashboard
CATKNOWS_DASHBOARD_URL=https://catknows.app
EOF

# 3. The service
cp /opt/catknows/deploy/catknows-dashboard.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now catknows-dashboard
systemctl status catknows-dashboard --no-pager

# 4. Caddy already has the catknows.app block — reload it
systemctl reload caddy
```

`CATKNOWS_OAUTH_ISSUER` is shared with the MCP server and already in that file.
There is **no client secret**: it is a public client and PKCE covers the code
exchange, so there is one less credential to rotate.

### Check it

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://catknows.app/            # 200
curl -s -o /dev/null -w '%{http_code}\n' https://catknows.app/privacy     # 200
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' \
     https://catknows.app/connect                                        # 302 -> /auth/login
```

Then in a browser: `catknows.app/connect` → sign in → *Open Skool login* → the
Skool page appears, streamed. Log in; the page reports the connected account and
stores the cookie encrypted.

### Limits, deliberately

| Knob | Default | Why |
|---|---|---|
| `CATKNOWS_LOGIN_MAX_SESSIONS` | 2 | Each Chromium is ~300–400 MB. This box has 4 GB and **no swap**, with ~600 MB already in Keycloak. Past the cap a login is refused with a clear message rather than the kernel OOM-killing someone mid-password. |
| `CATKNOWS_LOGIN_TTL` | 300 s | A login takes seconds; anything older is an abandoned tab holding a slot. |
| `MemoryMax=1800M` (unit) | — | A runaway browser must not take Keycloak with it. Overshooting kills the dashboard alone, which restarts; the MCP endpoint keeps serving. |

After a RAM upgrade, raise `CATKNOWS_LOGIN_MAX_SESSIONS` and `MemoryMax` — no
code change needed.

The connected Skool account is checked against the catknows account and a
mismatch is **shown, not blocked**: using a different email at Skool than here is
perfectly normal, so refusing would lock out real users. Naming the account lets
the person notice the case that actually matters — signing into the wrong Skool
account.

## Outbound SMTP is blocked

netcup blocks outbound 25, 465 and 587 (anti-spam). Verified on this box:

```bash
timeout 8 bash -c 'cat < /dev/null > /dev/tcp/smtp.tem.scaleway.com/465' \
  && echo open || echo blocked
```

Use a provider's alternate port instead — Scaleway TEM listens on **2465**
for implicit TLS. The failure mode is a bare `SocketTimeoutException`, which
reads like a wrong host or bad credentials rather than a blocked port, so
check reachability first when mail "just doesn't send".

## Testing from the box itself

netcup does no hairpin NAT: the server cannot reach its **own** public IP, so
`curl https://mcp.catknows.app/...` on the box times out even when everything
works. That looks exactly like a broken vhost and isn't.

Test the local path with the real hostname instead:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  --resolve mcp.catknows.app:443:127.0.0.1 https://mcp.catknows.app/mcp
```

For "does the world see it", ask from somewhere else — your laptop, or
`curl` from any other host.

## If Chromium won't start

Ubuntu 23.10+ ships `kernel.apparmor_restrict_unprivileged_userns=1`, which
blocks Chromium's namespace sandbox; the unit's `NoNewPrivileges=true` rules
out the SUID helper as well. If the first login fails with a sandbox error,
grant the exception to our binary only — not to the whole system:

```bash
CHROME=$(ls -d /var/lib/catknows/browsers/chromium-*/chrome-linux*/chrome | head -1)
cat >/etc/apparmor.d/catknows-chromium <<EOF
abi <abi/4.0>,
include <tunables/global>
profile catknows-chromium $CHROME flags=(unconfined) {
  userns,
  include if exists <local/catknows-chromium>
}
EOF
apparmor_parser -r /etc/apparmor.d/catknows-chromium
systemctl restart catknows-mcp
```

The blunt alternative is `sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`,
which lifts the restriction for every process on the box — the profile above
does the same job scoped to one binary. Note the path changes when Playwright
updates Chromium, so re-run this after a version bump.

**Not `--no-sandbox`.** This process renders pages from a site you don't
control while holding a live session; the sandbox is what stands between a
browser exploit and that session.

## Updating

```bash
sudo -u catknows git -C /opt/catknows pull
sudo -u catknows /opt/catknows/.venv/bin/pip install -e ".[mcp]"
systemctl restart catknows-mcp
```

## Writes

Off unless you set `CATKNOWS_ALLOW_WRITE=1` in `/etc/catknows/env`. Leave it
off until you actually want posting from the server — the draft-first confirm
still applies, but a remote server acting as you deserves the extra lock.

## Data protection

The box processes other people's member data (names, emails). It's on German
soil with a German provider on purpose — see [../LEGAL.md](../LEGAL.md).

The Art. 28 GDPR processing agreement (AVV) with netcup is **in place** since
2026-08-11, covering this VPS. It declares no payment data — which is only true
because `normalize.scrub` strips the Stripe/payout fields Skool ships inside
otherwise ordinary payloads. **Weakening the scrub would make a signed contract
inaccurate**, not just leak a field.

Still open before anyone else uses this: a public privacy policy, and the fact
that you become *their* processor in turn. Phase 3.
