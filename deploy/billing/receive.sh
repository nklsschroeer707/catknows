#!/bin/sh
# Forced command of the restricted "kasse" SSH key: the only thing that key can do
# is hand this box the current list of paying catnose members on stdin.
#
# Runs as root, so it must not run anything the service user can change: not
# /opt/catknows (owned by catknows), not its venv. install.sh copies this script
# to /usr/local/sbin/catknows-billing-receive and billing.py + sessions.py to
# /usr/local/lib/catknows-billing/ckb/, all root-owned; the system python3 runs
# them isolated (-I: no PYTHONPATH, no user site, no cwd on the path).
#
#   /root/.ssh/authorized_keys:
#   command="/usr/local/sbin/catknows-billing-receive",restrict ssh-ed25519 AAAA… kasse@homelab
#
# Output: one JSON line of numbers (no names, no ids).
cd /
exec /usr/bin/python3 -I -c 'import sys; sys.path.insert(0, "/usr/local/lib/catknows-billing"); from ckb import billing; sys.exit(billing.main(["receive", "--env-file", "/etc/catknows/env"]))'
