#!/usr/bin/env bash
# Forced command of the restricted "kasse" SSH key: the only thing that key can do
# is hand this box the current list of paying catnose members on stdin.
#
#   /root/.ssh/authorized_keys:
#   command="/opt/catknows/deploy/billing/receive.sh",restrict ssh-ed25519 AAAA… kasse@homelab
#
# `restrict` turns off port, agent and X11 forwarding and the pty; `command=`
# ignores whatever the client asked to run. Output: one JSON line of numbers
# (no names, no ids). See deploy/README.md "Paid access through Skool".
set -euo pipefail
cd /opt/catknows
exec /opt/catknows/.venv/bin/python -m catknows.billing receive --env-file /etc/catknows/env
