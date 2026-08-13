#!/usr/bin/env bash
# Configure the catknows realm for MCP OAuth. Idempotent — safe to re-run.
#
#   docker compose exec -T keycloak bash < setup-realm.sh
#
# Everything Keycloak needs to serve https://mcp.catknows.app/mcp as an OAuth
# 2.1 resource, per Keycloak's own MCP guide:
#   https://www.keycloak.org/securing-apps/mcp-authz-server
set -euo pipefail

REALM=catknows
MCP_URL=https://mcp.catknows.app/mcp
KCADM=/opt/keycloak/bin/kcadm.sh

# The admin password is only in the container's env, never on the command line
# (ps is world-readable).
"$KCADM" config credentials --server http://127.0.0.1:8080 \
  --realm master --user "$KC_BOOTSTRAP_ADMIN_USERNAME" \
  --password "$KC_BOOTSTRAP_ADMIN_PASSWORD" >/dev/null

have() { "$KCADM" get "$1" >/dev/null 2>&1; }

# -- realm ---------------------------------------------------------------------
# Not `master`: that realm administers Keycloak itself. Mixing product accounts
# into it means every user sits next to the admin console.
if have "realms/$REALM"; then
	echo "realm $REALM exists"
else
	"$KCADM" create realms -s "realm=$REALM" -s enabled=true
	echo "realm $REALM created"
fi

# Registration is OPEN since 2026-08-13. The two reasons it was closed are gone:
# the streamed login (§2a) lets a signup complete without the operator, and a
# signup no longer buys anything on its own — the `service` role below does.
#
# Signing up is therefore cheap to allow and worth nothing to abuse: an account
# with a confirmed address and no `service` role is refused at every tool call
# (see may_use_service in catknows/auth_oauth.py). What it still costs is one
# Scaleway verification mail per signup, which is why verifyEmail stays on —
# it is the only thing standing between a script and that quota.
#
# registrationEmailAsUsername/verifyEmail: the address *is* the username and
# must be confirmed before a reset link is ever sent.
# Brute force detection is Keycloak's own; it locks an account temporarily
# after repeated failures rather than letting a password be guessed.
"$KCADM" update "realms/$REALM" \
  -s registrationAllowed=true \
  -s registrationEmailAsUsername=true \
  -s verifyEmail=true \
  -s resetPasswordAllowed=true \
  -s loginWithEmailAllowed=true \
  -s duplicateEmailsAllowed=false \
  -s bruteForceProtected=true \
  -s permanentLockout=false \
  -s failureFactor=5 \
  -s waitIncrementSeconds=60 \
  -s maxFailureWaitSeconds=900 \
  -s quickLoginCheckMilliSeconds=1000 \
  -s minimumQuickLoginWaitSeconds=60 \
  -s maxDeltaTimeSeconds=43200 \
  -s sslRequired=all
echo "realm settings applied (registration OPEN + email verification + brute force)"

# -- the service role ----------------------------------------------------------
# What separates "has an account" from "may use the hosted service". Anyone can
# sign up; only accounts carrying this role get past may_use_service() in
# catknows/auth_oauth.py.
#
# A realm role rather than a user attribute on purpose: roles land in the access
# token by themselves (realm_access.roles), an attribute would need its own
# protocol mapper to get there at all. One less moving part in the token.
#
# Granting it is the operator's one manual step:
#   Users -> <the account> -> Role mapping -> Assign role -> service
# Revoking it is the kill switch for a single user, effective on their next
# token (minutes, not hours — access tokens are short-lived).
ROLE=service
if "$KCADM" get "roles/$ROLE" -r "$REALM" >/dev/null 2>&1; then
	echo "realm role $ROLE exists"
else
	"$KCADM" create roles -r "$REALM" \
		-s "name=$ROLE" \
		-s "description=May use the hosted catknows MCP service. Granted by hand today; later this is where paid membership is checked."
	echo "realm role $ROLE created"
fi

# NOT a default role: if new users got it automatically, opening registration
# would hand the service to everyone with an email address, which is the exact
# thing this role exists to prevent.
echo "  (deliberately NOT in the realm's default roles — grant it per user)"

# -- client scope with audience mapper -----------------------------------------
# Keycloak has no RFC 8707 (resource indicators) yet, so the MCP audience rides
# on a scope instead — their documented workaround. Without the `aud` claim the
# MCP server cannot tell that a token was minted for *it* and not for some
# other client of this realm.
SCOPE=mcp:tools
scope_id=$("$KCADM" get client-scopes -r "$REALM" --fields id,name 2>/dev/null \
	| grep -B1 "\"name\" : \"$SCOPE\"" | grep '"id"' | cut -d'"' -f4 | head -1 || true)

if [ -z "$scope_id" ]; then
	scope_id=$("$KCADM" create client-scopes -r "$REALM" -i \
		-s "name=$SCOPE" \
		-s protocol=openid-connect \
		-s 'attributes."include.in.token.scope"=true' \
		-s 'attributes."display.on.consent.screen"=true')
	echo "client scope $SCOPE created"

	"$KCADM" create "client-scopes/$scope_id/protocol-mappers/models" -r "$REALM" \
		-s name=mcp-audience \
		-s protocol=openid-connect \
		-s protocolMapper=oidc-audience-mapper \
		-s 'config."included.custom.audience"='"$MCP_URL" \
		-s 'config."access.token.claim"=true'
	echo "audience mapper -> $MCP_URL"
else
	echo "client scope $SCOPE exists"
fi

# Optional, not default: a client gets this audience only by asking for the
# scope, so tokens minted for anything else stay unusable against the MCP server.
"$KCADM" update "realms/$REALM" -s "defaultOptionalClientScopes+=$SCOPE" 2>/dev/null \
	|| echo "  (scope already in optional list)"

# -- claims the entitlement check needs ----------------------------------------
# A client registered through DCR comes out with `basic` as its only default
# scope — no `email`, no `roles`. Tokens minted for it therefore carry neither
# `email_verified` nor `realm_access.roles`, and may_use_service() refuses every
# request with "email not verified" while the account is perfectly fine. That
# cost an hour on 2026-08-13; the log line naming the failed check is what found
# it.
#
# Two separate fixes, because they cover different clients:
#   1. the realm default, inherited by every FUTURE DCR registration
#   2. the clients already registered, which inherit nothing retroactively
#
# Assignment goes through the default-client-scopes sub-resource. Setting the
# field with `-s defaultClientScopes+=email` is accepted, reports success, and
# changes nothing — verified on the box. Always read it back.
scope_id_by_name() {
	"$KCADM" get client-scopes -r "$REALM" --fields id,name --format csv --noquotes \
		2>/dev/null | grep ",$1\$" | cut -d, -f1 | head -1
}

for want in email roles; do
	sid=$(scope_id_by_name "$want")
	if [ -z "$sid" ]; then
		echo "  WARNING: built-in scope '$want' not found — entitlement checks will fail"
		continue
	fi

	# 1. future DCR clients
	"$KCADM" update "realms/$REALM" -s "defaultDefaultClientScopes+=$want" 2>/dev/null \
		&& echo "realm default scope += $want" \
		|| echo "  ($want already a realm default)"

	# 2. clients that already exist. The DCR ones are named by their UUID.
	for cid in $("$KCADM" get clients -r "$REALM" --fields clientId --format csv \
	             --noquotes 2>/dev/null | grep -E '^[0-9a-f]{8}-' || true); do
		"$KCADM" update "clients/$cid/default-client-scopes/$sid" -r "$REALM" \
			2>/dev/null && echo "  $cid += $want" || true
	done
done

# Read back, because the failure mode above is a silent no-op.
echo "default scopes per DCR client (must include email and roles):"
for cid in $("$KCADM" get clients -r "$REALM" --fields clientId --format csv --noquotes \
             2>/dev/null | grep -E '^[0-9a-f]{8}-' || true); do
	printf '  %s: ' "$cid"
	"$KCADM" get "clients/$cid/default-client-scopes" -r "$REALM" --fields name \
		--format csv --noquotes 2>/dev/null | tr '\n' ' '
	echo
done

# -- dynamic client registration -----------------------------------------------
# claude.ai has never seen this server before, so it must register itself
# (RFC 7591). Keycloak blocks anonymous registration by default; the policies
# below are what keep it from becoming an open client factory.
echo
echo "NOTE: anonymous DCR policies are NOT set by this script."
echo "  Keycloak ships restrictive defaults, and loosening them from a script"
echo "  is how you accidentally publish an open registration endpoint."
echo "  Set them deliberately in the admin console:"
echo "    Clients -> Client registration -> Anonymous access policies"
echo "      - Allowed Client Scopes: add $SCOPE"
echo "      - Trusted Hosts: the callers you expect (claude.ai infra)"
echo "      - Max Clients: keep a ceiling"
echo
echo "Realm $REALM configured. Discovery:"
echo "  https://auth.catknows.app/realms/$REALM/.well-known/openid-configuration"
