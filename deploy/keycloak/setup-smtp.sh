#!/usr/bin/env bash
# Point the catknows realm at Scaleway TEM for outgoing mail.
#
#   docker compose exec -T \
#     -e SMTP_USER=... -e SMTP_PASS=... -e SMTP_FROM=... \
#     keycloak bash < setup-smtp.sh
#
# Without working mail nobody can register: verifyEmail=true means the
# confirmation link never arrives and the account stays unusable.
set -euo pipefail

REALM=catknows
KCADM=/opt/keycloak/bin/kcadm.sh

: "${SMTP_USER:?pass SMTP_USER (the Scaleway TEM project/username)}"
: "${SMTP_PASS:?pass SMTP_PASS (your Scaleway API secret key)}"
FROM="${SMTP_FROM:-noreply@catknows.app}"
FROM_NAME="${SMTP_FROM_NAME:-catknows}"

"$KCADM" config credentials --server http://127.0.0.1:8080 \
  --realm master --user "$KC_BOOTSTRAP_ADMIN_USERNAME" \
  --password "$KC_BOOTSTRAP_ADMIN_PASSWORD" >/dev/null

# Port 2465, not 465: netcup blocks outbound 25/465/587 (verified on the box —
# only 2465 connects), which is why Scaleway publishes alternate ports at all.
# The failure mode is a plain connect timeout, so it reads like a broken host
# or wrong credentials rather than a blocked port.
#
# Still implicit TLS, not 587+STARTTLS: a stripped STARTTLS downgrades to
# plaintext, and these messages carry account recovery links.
"$KCADM" update "realms/$REALM" \
  -s 'smtpServer.host=smtp.tem.scaleway.com' \
  -s "smtpServer.port=${SMTP_PORT:-2465}" \
  -s 'smtpServer.ssl=true' \
  -s 'smtpServer.starttls=false' \
  -s 'smtpServer.auth=true' \
  -s "smtpServer.user=$SMTP_USER" \
  -s "smtpServer.password=$SMTP_PASS" \
  -s "smtpServer.from=$FROM" \
  -s "smtpServer.fromDisplayName=$FROM_NAME" \
  -s "smtpServer.replyTo=$FROM" \
  -s 'smtpServer.envelopeFrom='"$FROM"

echo "SMTP configured: $FROM via smtp.tem.scaleway.com:${SMTP_PORT:-2465}"
echo
echo "Test it: admin console -> Realm settings -> Email -> 'Test connection'."
echo "That button is the only proof the password is right — kcadm accepts"
echo "anything and Keycloak only finds out when it tries to send."
echo
echo "If it fails with 535 Permission denied, the key is usually fine and the"
echo "permission isn't. Two things Scaleway needs, both invisible from here:"
echo "  1. the domain must show 'Verified' (DNS check can lag by minutes)"
echo "  2. IAM -> Policies: the *application* owning the key needs"
echo "     TransactionalEmailFullAccess. A key with no policy authenticates"
echo "     as nobody and is refused exactly like a wrong password."
echo
echo "Faster than testing through Keycloak:"
echo "  python3 -c \"import smtplib;s=smtplib.SMTP_SSL('smtp.tem.scaleway.com',2465);s.login('<user>','<secret>');print('OK')\""
