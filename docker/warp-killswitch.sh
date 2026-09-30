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

# Xoa rule cu de script chay lai (restart) khong bi chong rule
iptables -F OUTPUT

# Default deny
iptables -P OUTPUT DROP
iptables -P FORWARD DROP

# Loopback + established/related (covers proxy on 127.0.0.1:8888 too)
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# CHAN MANG NOI BO: phai dat TRUOC moi rule ACCEPT ben duoi.
# Truoc day 443/53/ICMP/2408/500/4500 deu ACCEPT toi MOI dich den,
# nen container toi duoc 192.168.x.x, gateway docker (172.17.0.1 = may host), 10.x...
for NET in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 100.64.0.0/10; do
  iptables -A OUTPUT -d "$NET" -j REJECT --reject-with icmp-net-prohibited
done

# DNS: chi toi resolver public da khai bao trong compose/resolv.conf
for NS in 1.1.1.1 8.8.8.8; do
  iptables -A OUTPUT -p udp -d "$NS" --dport 53 -j ACCEPT
  iptables -A OUTPUT -p tcp -d "$NS" --dport 53 -j ACCEPT
done

# ICMP for diagnostics
iptables -A OUTPUT -p icmp --icmp-type echo-request -j ACCEPT

# WARP tunnel ports
iptables -A OUTPUT -p udp --dport 2408 -j ACCEPT
iptables -A OUTPUT -p udp --dport 500  -j ACCEPT
iptables -A OUTPUT -p udp --dport 4500 -j ACCEPT
iptables -A OUTPUT -p tcp --dport 443  -j ACCEPT

# IPv6: sysctl da tat, them default DROP de chac an (neu ip6tables khong co thi bo qua)
ip6tables -F OUTPUT 2>/dev/null || true
ip6tables -P OUTPUT DROP 2>/dev/null || true
ip6tables -P FORWARD DROP 2>/dev/null || true
ip6tables -A OUTPUT -o lo -j ACCEPT 2>/dev/null || true

echo "[Kivotos-Guard] Killswitch active. Ha noi bo con lai bi khoa."
echo "[Kivotos-Guard] Executor khong con NET_ADMIN nen khong the tu xoa luat nay."
