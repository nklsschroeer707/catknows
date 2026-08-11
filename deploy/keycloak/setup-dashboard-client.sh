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
if [ -z "$existing" ]; then
	"$KCADM" create clients -r "$REALM" \
	  -s "clientId=$CLIENT" \
	  -s enabled=true \
	  -s publicClient=true \
	  -s standardFlowEnabled=true \
	  -s directAccessGrantsEnabled=false \
	  -s serviceAccountsEnabled=false \
	  -s 'attributes."pkce.code.challenge.method"=S256' \
	  -s "redirectUris=[\"$BASE/auth/callback\"]" \
	  -s "webOrigins=[\"$BASE\"]" \
	  -s "baseUrl=$BASE"
	echo "client $CLIENT created (public + PKCE S256)"
else
	"$KCADM" update "clients/$existing" -r "$REALM" \
	  -s publicClient=true \
	  -s standardFlowEnabled=true \
	  -s directAccessGrantsEnabled=false \
	  -s 'attributes."pkce.code.challenge.method"=S256' \
	  -s "redirectUris=[\"$BASE/auth/callback\"]" \
	  -s "webOrigins=[\"$BASE\"]"
	echo "client $CLIENT updated"
fi

echo
echo "Dashboard env (/etc/catknows/env):"
echo "  CATKNOWS_DASHBOARD_CLIENT_ID=$CLIENT"
echo "  CATKNOWS_DASHBOARD_URL=$BASE"
echo "  CATKNOWS_OAUTH_ISSUER=https://auth.catknows.app/realms/$REALM"
echo
echo "No client secret: it's a public client, PKCE covers the exchange."
