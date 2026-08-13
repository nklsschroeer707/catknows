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

# -- the entitlement flag ------------------------------------------------------
# What separates "has an account" from "may use the hosted service" is the user
# attribute `catknows_service=true`. Anyone can sign up; only accounts carrying
# it get past may_use_service() in catknows/auth_oauth.py.
#
# There is nothing to create here — an attribute exists once it is set on a
# user. What the realm needs is the mapper that carries it into the token; that
# is set up further down, on the mcp:tools scope.
#
# Granting is the operator's one manual step:
#   Users -> <the account> -> Attributes -> Add: catknows_service = true
# Removing it (or setting it to false) is the kill switch for a single user,
# effective on their next token — minutes, not hours.
#
# But first the realm has to *allow* the attribute. Keycloak 24+ runs a declared
# User Profile: any attribute not in that schema is refused on write with
# `error-user-attribute-required`, and the mapper below then carries nothing. On
# 2026-08-13 that cost an afternoon, because the mapper looked correct and was.
#
# view/edit are admin-only on purpose: a user who could edit this field would
# clear their own gate, and the whole point is that signing up is not access.
if "$KCADM" get users/profile -r "$REALM" | grep -q '"catknows_service"'; then
	echo "user profile: catknows_service already declared"
else
	# No python/jq/awk in this image, so the JSON is assembled with sed: append
	# our attribute right before the closing bracket of the "attributes" array,
	# which is the line holding `], "groups"`. Everything else stays untouched —
	# overwriting the profile wholesale would drop email/firstName/lastName and
	# break registration.
	"$KCADM" get users/profile -r "$REALM" > /tmp/profile.json
	tr -d '\n' < /tmp/profile.json | sed 's/\] *, *"groups"/, {"name":"catknows_service","displayName":"catknows service entitlement","multivalued":false,"permissions":{"view":["admin"],"edit":["admin"]},"validations":{}} ], "groups"/' > /tmp/profile-new.json
	"$KCADM" update users/profile -r "$REALM" -f /tmp/profile-new.json
	rm -f /tmp/profile.json /tmp/profile-new.json
	"$KCADM" get users/profile -r "$REALM" | grep -q '"catknows_service"' \
		|| { echo "FATAL: could not declare catknows_service in the user profile"; exit 1; }
	echo "user profile: catknows_service declared (admin-only)"
fi
echo "entitlement: user attribute 'catknows_service' (set per user, see README)"

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

# The lookup above uses the JSON output and returns empty on some Keycloak
# versions. Re-resolve from CSV, which has held: without an id the mappers below
# would be created against nothing and the entitlement check would keep failing.
if [ -z "$scope_id" ]; then
	scope_id=$("$KCADM" get client-scopes -r "$REALM" --fields id,name --format csv \
		--noquotes 2>/dev/null | grep ",$SCOPE\$" | cut -d, -f1 | head -1)
fi
[ -n "$scope_id" ] || { echo "FATAL: cannot resolve the $SCOPE scope id"; exit 1; }

# Optional, not default: a client gets this audience only by asking for the
# scope, so tokens minted for anything else stay unusable against the MCP server.
"$KCADM" update "realms/$REALM" -s "defaultOptionalClientScopes+=$SCOPE" 2>/dev/null \
	|| echo "  (scope already in optional list)"

# -- claims the entitlement check needs ----------------------------------------
# may_use_service() (catknows/auth_oauth.py) reads `email_verified` and
# `realm_access.roles`. Neither is in a DCR-issued token by default: a client
# registered through DCR comes out with `basic` as its only default scope, so
# every request is refused with "email not verified" while the account itself is
# perfectly fine. Cost an hour on 2026-08-13.
#
# The claims ride on the mcp:tools scope, NOT on the clients. Two failed
# attempts first, both worth not repeating:
#
#   * `-s defaultClientScopes+=email` on a client — accepted, exit 0, changes
#     nothing. A silent no-op. Same for defaultDefaultClientScopes on the realm.
#   * assigning email/roles as default scopes through the sub-resource — this
#     *works*, and then breaks every existing connection: the user consented to
#     {mcp:tools, offline_access}, the client now asks for more, and Keycloak
#     invalidates the offline token with "Client no longer has requested consent
#     from user". It also does nothing for clients registered later, and claude.ai
#     registers a fresh one on every reconnect.
#
# Hanging the mappers on mcp:tools avoids both: it is the scope claude.ai already
# requests and the user already consents to, so the consent set never changes,
# and every future DCR client inherits the claims automatically — it cannot reach
# the server without this scope anyway.
mapper() {
	name=$1; shift
	"$KCADM" create "client-scopes/$scope_id/protocol-mappers/models" -r "$REALM" \
		-s "name=$name" -s protocol=openid-connect "$@" 2>/dev/null \
		&& echo "  mapper $name created" || echo "  (mapper $name exists)"
}

mapper email-verified \
	-s protocolMapper=oidc-usermodel-property-mapper \
	-s 'config."user.attribute"=emailVerified' \
	-s 'config."claim.name"=email_verified' \
	-s 'config."jsonType.label"=boolean' \
	-s 'config."access.token.claim"=true' \
	-s 'config."id.token.claim"=true'

# The entitlement flag, as a user attribute rather than a realm role.
#
# A realm role was tried first and looked broken — no realm_access claim, on
# this very scope, while the mappers beside it worked. It was not broken: the
# role arrives fine, it was only ever measured on a DCR client, whose token
# carries no roles because DCR grants `basic` alone. The real fault was one
# level down and hit both designs equally: an attribute the realm's user profile
# does not declare cannot be stored at all, so the mapper had nothing to carry.
# See the user-profile block above.
#
# Sticking with the attribute anyway: it is a field on the account rather than a
# role assignment, and it does not ride on realm_access, which DCR tokens lack.
# Same seam in the code either way.
mapper service-flag \
	-s protocolMapper=oidc-usermodel-attribute-mapper \
	-s 'config."user.attribute"=catknows_service' \
	-s 'config."claim.name"=catknows_service' \
	-s 'config."jsonType.label"=String' \
	-s 'config."access.token.claim"=true' \
	-s 'config."id.token.claim"=true'

# Read back: the failure mode above is a silent no-op, so trusting exit codes
# here is how the hour got lost.
echo "mappers on $SCOPE (need mcp-audience, email-verified, service-flag):"
"$KCADM" get "client-scopes/$scope_id/protocol-mappers/models" -r "$REALM" \
	--fields name --format csv --noquotes 2>/dev/null | tr '\n' ' '
echo

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
