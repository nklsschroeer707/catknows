# Deploying the catknows MCP server

Phase 0/1 of [docs/HOSTED_MCP_PLAN.md](../docs/HOSTED_MCP_PLAN.md): one VPS,
Caddy in front, the MCP server on loopback behind it.

**Target:** netcup VPS 500 (2 vCore, 4 GB, 128 GB NVMe), location **Nürnberg**,
Ubuntu 24.04. Domain **catknows.app**, MCP endpoint on `mcp.catknows.app`.

Why 128 GB and not a 40 GB plan: each user keeps a persistent Chromium profile
(plan §2a). A real one measures ~130 MB, plus ~700 MB for the Chromium install
itself — 40 GB runs out at a few hundred users, and moving a box full of
encrypted sessions later is the migration you don't want.

> **Read this before you start.** Everything below assumes single-user
> operation — your own Skool account, your own use. **The HTTP transport has no
> authentication yet.** Anyone who reaches port 8000 gets your Skool session.
> That is why the server binds `127.0.0.1` and Caddy is the only way in. Do not
> open the port in the firewall, and do not put this in front of other people
> before Phase 2 (OAuth) lands.

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
