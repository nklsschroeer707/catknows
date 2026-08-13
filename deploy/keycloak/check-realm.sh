#!/usr/bin/env bash
# Is the catknows realm still configured to serve the hosted MCP? Read-only.
#
#   docker compose exec -T -e KC_BOOTSTRAP_ADMIN_USERNAME=admin \
#     -e KC_BOOTSTRAP_ADMIN_PASSWORD="$KCPW" keycloak bash < check-realm.sh
#
# Run it after a restart, a realm import, or whenever a token is missing a claim
# it should have. Every failure here shows up at runtime as "refusing subject …",
# which looks like a broken mapper and is usually not — on 2026-08-13 the actual
# cause was an undeclared user-profile attribute, and finding that took an
# afternoon of guessing at mappers that were fine all along.
#
# Never checks with `kcadm get --fields <nested>`: that prints `{ }` over
# populated objects and manufactured most of that afternoon's false leads. Whole
# object, then grep.
set -uo pipefail   # not -e: every check must run, so one failure still reports the rest

REALM=catknows
SCOPE=mcp:tools
KCADM=/opt/keycloak/bin/kcadm.sh
fail=0

"$KCADM" config credentials --server http://127.0.0.1:8080 \
  --realm master --user "$KC_BOOTSTRAP_ADMIN_USERNAME" \
  --password "$KC_BOOTSTRAP_ADMIN_PASSWORD" >/dev/null

# needle, haystack-producing command, what breaks without it
check() {
	if printf '%s' "$3" | grep -q "$2"; then
		echo "  ok    $1"
	else
		echo "  FAIL  $1 — $4"
		fail=$((fail + 1))
	fi
}

realm_json=$("$KCADM" get "realms/$REALM")

echo "realm:"
check "registration open"  '"registrationAllowed" : true' "$realm_json" \
	"nobody can sign up"
check "email verification" '"verifyEmail" : true'         "$realm_json" \
	"unconfirmed addresses would pass may_use_service()"
check "brute force"        '"bruteForceProtected" : true' "$realm_json" \
	"passwords could be guessed; this replaces fail2ban"
check "smtp host"          'smtp.tem.scaleway.com'        "$realm_json" \
	"no verification mail goes out, signups stall forever"

echo "user profile:"
check "catknows_service declared" '"catknows_service"' "$("$KCADM" get "users/profile" -r "$REALM")" \
	"the attribute cannot be stored at all, so the mapper carries nothing"

echo "mappers on $SCOPE:"
scope_id=$("$KCADM" get client-scopes -r "$REALM" --fields id,name --format csv --noquotes 2>/dev/null \
	| grep ",$SCOPE\$" | cut -d, -f1 | head -1)
if [ -z "$scope_id" ]; then
	echo "  FAIL  scope $SCOPE missing — no audience, no claims, nothing works"
	fail=$((fail + 1))
else
	mappers=$("$KCADM" get "client-scopes/$scope_id/protocol-mappers/models" -r "$REALM")
	check "mcp-audience"   'mcp-audience'   "$mappers" "tokens for other clients would be accepted"
	check "email-verified" 'email-verified' "$mappers" "every request refused: email not verified"
	check "service-flag"   'service-flag'   "$mappers" "every request refused: missing catknows_service"
fi

echo
if [ "$fail" -eq 0 ]; then
	echo "realm OK — signup, entitlement and mail are all wired up"
else
	echo "$fail check(s) FAILED — re-run setup-realm.sh, then setup-smtp.sh"
	exit 1
fi
