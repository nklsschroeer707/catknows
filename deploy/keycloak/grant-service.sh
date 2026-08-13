#!/usr/bin/env bash
# Grant or revoke the hosted service for one account.
#
#   ./grant-service.sh <email> [true|false]
#
# Run this ON THE BOX, from deploy/keycloak — not inside the container: it needs
# python3, which the Keycloak image does not have.
#
# Why a script for a one-field change: setting a user attribute through kcadm
# has two traps that both fail *loudly enough to look like something else*
# (2026-08-13, an afternoon).
#
#   1. `-s 'attributes.catknows_service=true'` — exit 0, no effect. The nested
#      key never reaches the server. Same class as Falle 13.
#   2. `update users/<id> -f -` with a body of only {"attributes": ...} — fails
#      with `error-user-attribute-required`. kcadm PUTs the body as the *whole*
#      user, and email/firstName/lastName are required by the user profile.
#      The message names attributes, the cause is the missing rest of the user.
#
# So: read the full user, add one key, write it all back. And verify from the
# token, not from `get --fields attributes` — that filter hides admin-only
# attributes and prints `{ }` for an attribute that is very much set.
set -euo pipefail

EMAIL="${1:?usage: grant-service.sh <email> [true|false]}"
VALUE="${2:-true}"
REALM=catknows

read -rsp "Keycloak admin password: " KCPW; echo

kc() {
	docker compose exec -T -e KC_BOOTSTRAP_ADMIN_USERNAME=admin \
		-e KC_BOOTSTRAP_ADMIN_PASSWORD="$KCPW" keycloak bash -c \
		'/opt/keycloak/bin/kcadm.sh config credentials --server http://127.0.0.1:8080 \
		   --realm master --user "$KC_BOOTSTRAP_ADMIN_USERNAME" \
		   --password "$KC_BOOTSTRAP_ADMIN_PASSWORD" >/dev/null
		 '"$1"
}

uid=$(kc "/opt/keycloak/bin/kcadm.sh get users -r $REALM -q email=$EMAIL --fields id --format csv --noquotes" \
	| tr -d '\r' | grep -v '^$' | head -1)
[ -n "$uid" ] || { echo "no account for $EMAIL"; exit 1; }
echo "account $EMAIL -> $uid"

kc "/opt/keycloak/bin/kcadm.sh get users/$uid -r $REALM" > /tmp/ck-user.json
VALUE="$VALUE" python3 - <<'PY'
import json, os
u = json.load(open("/tmp/ck-user.json"))
u.setdefault("attributes", {})["catknows_service"] = [os.environ["VALUE"]]
json.dump(u, open("/tmp/ck-user.json", "w"))
PY
kc "/opt/keycloak/bin/kcadm.sh update users/$uid -r $REALM -f -" < /tmp/ck-user.json
rm -f /tmp/ck-user.json
unset KCPW

echo "catknows_service=$VALUE set for $EMAIL"
echo
echo "Verify from a token, not from 'get --fields attributes' (it hides"
echo "admin-only attributes and prints an empty object either way)."
echo "The account's next token carries it; existing ones stay valid until they"
echo "expire, so a revoke takes effect within an access token's lifetime."
