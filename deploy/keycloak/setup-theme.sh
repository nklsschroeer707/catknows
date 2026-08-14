#!/usr/bin/env bash
# Point the catknows realm at our login theme.
#
#   cd /opt/catknows/deploy/keycloak
#   docker compose up -d          # picks up the themes/ mount
#   docker compose exec -T \
#     -e KC_BOOTSTRAP_ADMIN_USERNAME=... -e KC_BOOTSTRAP_ADMIN_PASSWORD=... \
#     keycloak bash < setup-theme.sh
#
# Run it after the mount exists: setting loginTheme to a theme Keycloak cannot
# see leaves every login screen on the built-in one, silently.
set -euo pipefail

REALM=catknows
THEME=catknows
KCADM=/opt/keycloak/bin/kcadm.sh

: "${KC_BOOTSTRAP_ADMIN_USERNAME:?pass the admin username}"
: "${KC_BOOTSTRAP_ADMIN_PASSWORD:?pass the admin password}"

# The theme has to be on disk in the container, or the realm points at nothing.
if [ ! -f "/opt/keycloak/themes/$THEME/login/theme.properties" ]; then
  echo "theme not mounted: /opt/keycloak/themes/$THEME/login/theme.properties missing" >&2
  echo "check the volumes: entry in compose.yml, then 'docker compose up -d'" >&2
  exit 1
fi

"$KCADM" config credentials --server http://127.0.0.1:8080 \
  --realm master --user "$KC_BOOTSTRAP_ADMIN_USERNAME" \
  --password "$KC_BOOTSTRAP_ADMIN_PASSWORD" >/dev/null

# loginTheme covers login, registration, email verification, password reset and
# the OAuth consent screen — every page a tester meets on the way in.
# emailTheme stays default: the theme ships no email templates, and pointing at
# it would fall back per-template anyway.
"$KCADM" update "realms/$REALM" -s "loginTheme=$THEME"

got=$("$KCADM" get "realms/$REALM" --fields loginTheme --format csv --noquotes)
if [ "$got" != "$THEME" ]; then
  echo "realm still reports loginTheme=$got" >&2
  exit 1
fi

echo "realm $REALM now uses the $THEME login theme"
echo "check it: https://auth.catknows.app/realms/$REALM/account"
