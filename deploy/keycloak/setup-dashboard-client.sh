#!/usr/bin/env bash
# Register the dashboard as an OAuth client in the catknows realm. Idempotent.
#
#   docker compose exec -T keycloak bash < setup-dashboard-client.sh
#
# The dashboard (plan §2a) signs a *person* in, unlike the MCP endpoint which
# verifies a machine's bearer token. It therefore needs its own client with a
# redirect URI, which the MCP resource server has no use for.
set -euo pipefail

REALM=catknows
CLIENT=catknows-dashboard
BASE=${DASHBOARD_URL:-https://catknows.app}
KCADM=/opt/keycloak/bin/kcadm.sh

"$KCADM" config credentials --server http://127.0.0.1:8080 \
  --realm master --user "$KC_BOOTSTRAP_ADMIN_USERNAME" \
  --password "$KC_BOOTSTRAP_ADMIN_PASSWORD" >/dev/null

existing=$("$KCADM" get clients -r "$REALM" -q "clientId=$CLIENT" --fields id 2>/dev/null \
	| grep '"id"' | cut -d'"' -f4 | head -1 || true)

# Public client with PKCE, not a confidential one: the dashboard holds no secret
# it could keep better than the box already keeps its env file, and PKCE is what
# actually protects the code exchange. One less secret to rotate.
#
# The redirect URI is exact — no wildcard. A wildcard here is the classic
# open-redirect that hands an attacker the authorization code.
#
# Direct access grants ON (2026-08-13, Niklas' call): the landing page signs
# people in inline from the account popover — POST /auth/password relays the
# credentials to the token endpoint on this same box. Keycloak's brute-force
# counter sees those attempts like any others. Registration and password reset
# stay on the Keycloak pages; only the sign-in moved inline.
if [ -z "$existing" ]; then
	"$KCADM" create clients -r "$REALM" \
	  -s "clientId=$CLIENT" \
	  -s enabled=true \
	  -s publicClient=true \
	  -s standardFlowEnabled=true \
	  -s directAccessGrantsEnabled=true \
	  -s serviceAccountsEnabled=false \
	  -s 'attributes."pkce.code.challenge.method"=S256' \
	  -s "redirectUris=[\"$BASE/auth/callback\"]" \
	  -s "webOrigins=[\"$BASE\"]" \
	  -s "baseUrl=$BASE"
	echo "client $CLIENT created (public + PKCE S256, direct grants for the popover)"
else
	"$KCADM" update "clients/$existing" -r "$REALM" \
	  -s publicClient=true \
	  -s standardFlowEnabled=true \
	  -s directAccessGrantsEnabled=true \
	  -s 'attributes."pkce.code.challenge.method"=S256' \
	  -s "redirectUris=[\"$BASE/auth/callback\"]" \
	  -s "webOrigins=[\"$BASE\"]"
	echo "client $CLIENT updated (direct grants on, for the popover sign-in)"
fi

# The dashboard requests mcp:tools so its access token carries the
# catknows_service claim — that is what the account panel reads to decide
# between a green dot and "awaiting approval". Without the scope assigned here
# the authorization request fails outright with `invalid_scope`.
#
# Assigned through the sub-resource, not as a client field: `-s
# optionalClientScopes+=...` exits 0 and does nothing. Optional rather than
# default, matching how the DCR clients get it — the client asks for it
# explicitly.
cid=${existing:-$("$KCADM" get clients -r "$REALM" -q "clientId=$CLIENT" --fields id \
	--format csv --noquotes 2>/dev/null | head -1)}
sid=$("$KCADM" get client-scopes -r "$REALM" --fields id,name --format csv --noquotes \
	2>/dev/null | grep ',mcp:tools$' | cut -d, -f1 | head -1)

if [ -n "$cid" ] && [ -n "$sid" ]; then
	"$KCADM" update "clients/$cid/optional-client-scopes/$sid" -r "$REALM" 2>/dev/null \
		&& echo "scope mcp:tools assigned" || echo "  (mcp:tools already assigned)"
	printf 'optional scopes on %s: ' "$CLIENT"
	"$KCADM" get "clients/$cid/optional-client-scopes" -r "$REALM" --fields name \
		--format csv --noquotes 2>/dev/null | tr '\n' ' '
	echo
else
	echo "WARNING: could not assign mcp:tools (client or scope not found) —"
	echo "  the dashboard's panel will show every account as awaiting approval."
	echo "  Run setup-realm.sh first, it creates the scope."
fi

echo
echo "Dashboard env (/etc/catknows/env):"
echo "  CATKNOWS_DASHBOARD_CLIENT_ID=$CLIENT"
echo "  CATKNOWS_DASHBOARD_URL=$BASE"
echo "  CATKNOWS_OAUTH_ISSUER=https://auth.catknows.app/realms/$REALM"
echo
echo "No client secret: it's a public client, PKCE covers the exchange."
