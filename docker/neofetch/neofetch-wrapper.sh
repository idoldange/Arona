#!/bin/sh
CONF=/home/sandboxuser/.config/neofetch/config.conf
[ -f "$CONF" ] || CONF=/etc/neofetch/arona.conf
/usr/bin/neofetch --config "$CONF" "$@" | sed -e 's/\x1b\[[0-9;?]*[A-Za-z]//g'
