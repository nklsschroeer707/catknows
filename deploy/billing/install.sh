#!/bin/sh
# Install (or update) the root-owned billing receiver. It runs as root, so it is
# installed from a root-owned checkout, never from /opt/catknows (which the
# service user owns and could change between deploys):
#
#   git clone -b hosted-mcp https://github.com/nklsschroeer707/catknows.git /usr/local/src/catknows   # once
#   cd /usr/local/src/catknows && git pull -q && sh deploy/billing/install.sh                          # each update
#
# Re-run after every deploy that touches catknows/billing.py or catknows/sessions.py.
set -eu
SRC=$(cd "$(dirname "$0")/../.." && pwd -P)
for p in "$SRC" "$SRC/catknows" "$SRC/deploy/billing"; do
	[ "$(stat -c %u "$p")" = 0 ] || { echo "refusing: $p is not root-owned (use the root checkout)"; exit 1; }
done
LIB=/usr/local/lib/catknows-billing/ckb
install -d -m 755 -o root -g root /usr/local/lib/catknows-billing "$LIB"
install -m 644 -o root -g root /dev/null "$LIB/__init__.py"
install -m 644 -o root -g root "$SRC/catknows/billing.py" "$LIB/billing.py"
install -m 644 -o root -g root "$SRC/catknows/sessions.py" "$LIB/sessions.py"
install -m 755 -o root -g root "$SRC/deploy/billing/receive.sh" /usr/local/sbin/catknows-billing-receive
# root-owned, group catknows may read; setgid so new files inherit the group.
# The parent /var/lib is root's, so this path cannot be swapped by the service user.
[ -L /var/lib/catknows-billing ] && { echo "refusing: /var/lib/catknows-billing is a symlink"; exit 1; }
install -d -m 2750 -o root -g catknows /var/lib/catknows-billing
/usr/bin/python3 -I -c 'import sys; sys.path.insert(0, "/usr/local/lib/catknows-billing"); from ckb import billing; billing._self_check()'
echo "billing receiver installed from $(git -C "$SRC" rev-parse --short HEAD 2>/dev/null || echo "$SRC")"
