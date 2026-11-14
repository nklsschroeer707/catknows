#!/bin/sh
# Install (or update) the root-owned copy of the billing receiver. Run as root
# after every deploy that touches catknows/billing.py or catknows/sessions.py:
#   sh /opt/catknows/deploy/billing/install.sh
set -eu
SRC=/opt/catknows
LIB=/usr/local/lib/catknows-billing/ckb
install -d -m 755 -o root -g root "$LIB"
install -m 644 -o root -g root /dev/null "$LIB/__init__.py"
install -m 644 -o root -g root "$SRC/catknows/billing.py" "$LIB/billing.py"
install -m 644 -o root -g root "$SRC/catknows/sessions.py" "$LIB/sessions.py"
install -m 755 -o root -g root "$SRC/deploy/billing/receive.sh" /usr/local/sbin/catknows-billing-receive
install -d -m 750 -o catknows -g catknows /var/lib/catknows/billing
/usr/bin/python3 -I -c 'import sys; sys.path.insert(0, "/usr/local/lib/catknows-billing"); from ckb import billing; billing._self_check()'
echo "billing receiver installed"
