#!/bin/sh
set -e
# Runs INSIDE the warp container (already privileged, has NET_ADMIN/SYS_MODULE
# natively). This is the only place iptables rules for the shared netns get
# applied — arona-executor no longer needs NET_ADMIN/NET_RAW at all.

echo "[Kivotos-Guard] Cho warp-cli ket noi truoc..."
COUNT=0
MAX_RETRIES=60
while [ $COUNT -lt $MAX_RETRIES ]; do
  if warp-cli --accept-tos status 2>/dev/null | grep -qi "Connected"; then
    echo "[Kivotos-Guard] Warp da Connected."
    break
  fi
  sleep 2
  COUNT=$((COUNT + 1))
done
if [ $COUNT -eq $MAX_RETRIES ]; then
  echo "[Kivotos-Guard] CRITICAL: Warp timeout, khong khoa netns." >&2
  exit 1
fi

echo "[Kivotos-Guard] Khoa killswitch cho toan bo netns (warp+proxy+executor)..."

# Default deny
iptables -P OUTPUT DROP
iptables -P FORWARD DROP

# Loopback + established/related (covers proxy on 127.0.0.1:8888 too)
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# DNS
iptables -A OUTPUT -p udp --dport 53 -j ACCEPT
iptables -A OUTPUT -p tcp --dport 53 -j ACCEPT
iptables -A OUTPUT -d 1.1.1.1 -j ACCEPT

# ICMP for diagnostics
iptables -A OUTPUT -p icmp --icmp-type echo-request -j ACCEPT

# WARP tunnel ports
iptables -A OUTPUT -p udp --dport 2408 -j ACCEPT
iptables -A OUTPUT -p udp --dport 500  -j ACCEPT
iptables -A OUTPUT -p udp --dport 4500 -j ACCEPT
iptables -A OUTPUT -p tcp --dport 443  -j ACCEPT

echo "[Kivotos-Guard] Killswitch active. Ha noi bo con lai bi khoa."
echo "[Kivotos-Guard] Executor khong con NET_ADMIN nen khong the tu xoa luat nay."
