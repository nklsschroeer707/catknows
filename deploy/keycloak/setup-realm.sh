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

# Registration is CLOSED: accounts are created here, in the admin console.
#
# Self-signup would be an open account factory long before it is useful — a new
# account can reach no data anyway (no Skool session in the store means every
# tool call is refused), while each signup does burn a verification mail from
# the Scaleway quota. Nothing is gained and something is spent.
#
# It also can't be self-service yet by construction: a Skool session has to be
# put in from the box (`catknows-session store <subject>`), so onboarding
# involves the operator regardless. Flip this to true once the streamed remote
# login (plan §2a) makes signup actually complete on its own — and pair it with
# per-IP rate limiting then, because Keycloak's brute-force protection guards
# passwords, not registrations.
#
# registrationEmailAsUsername/verifyEmail stay on: they're what a console-created
# account is keyed on, and what proves the address before a reset link is sent.
# Brute force detection is Keycloak's own; it locks an account temporarily
# after repeated failures rather than letting a password be guessed.
"$KCADM" update "realms/$REALM" \
  -s registrationAllowed=false \
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
echo "realm settings applied (registration CLOSED + email verification + brute force)"

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
