#!/bin/sh
/usr/bin/neofetch "$@" | sed -e 's/\x1b\[[0-9;?]*[A-Za-z]//g'
