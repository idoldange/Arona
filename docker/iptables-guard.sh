#!/bin/sh
set -e

# This script must be run as root to set up iptables rules.
if [ "$(id -u)" -ne 0 ]; then
  echo "[Arona-Guard] This script must be run as root." >&2
  exit 1
fi

echo "[Arona-Guard] Khởi động lá chắn Kivotos..."

# 1. KILL SWITCH: Chặn đứng IP thật thoát ra ngoài
iptables -P OUTPUT DROP
iptables -P FORWARD DROP

# 2. MỞ CÁC ĐƯỜNG MÁU
iptables -A OUTPUT -o lo -j ACCEPT # Loopback nội bộ
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT # Allow response packets
iptables -A OUTPUT -p udp --dport 53 -j ACCEPT # DNS UDP
iptables -A OUTPUT -p tcp --dport 53 -j ACCEPT # DNS TCP
iptables -A OUTPUT -d 1.1.1.1 -j ACCEPT # Ép DNS Google/Cloudflare
iptables -A OUTPUT -p icmp --icmp-type echo-request -j ACCEPT # Cho phép ping (ICMP Echo Request) để kiểm tra kết nối mạng

# 3. MỞ CỔNG CHO WARP TUNNEL
iptables -A OUTPUT -p udp --dport 2408 -j ACCEPT
iptables -A OUTPUT -p udp --dport 500 -j ACCEPT
iptables -A OUTPUT -p udp --dport 4500 -j ACCEPT
iptables -A OUTPUT -p tcp --dport 443 -j ACCEPT

# 4. CHỜ WARP SẴN SÀNG (Handshaking)
echo "[Arona-Guard] Đang chờ Warp kết nối ổn định..."
MAX_RETRIES=30
COUNT=0
while [ $COUNT -lt $MAX_RETRIES ]; do
  # Kiểm tra IP qua proxy
  CURRENT_IP=$(curl -s --proxy http://127.0.0.1:8888 https://api4.ipify.org || echo "")
  if [ ! -z "$CURRENT_IP" ]; then
    echo "[Arona-Guard] Warp Connected! IP: $CURRENT_IP"
    break
  fi
  sleep 2
  COUNT=$((COUNT + 1))
done

if [ $COUNT -eq $MAX_RETRIES ]; then
  echo "[Arona-Guard] CRITICAL: Warp timeout. Shutting down!" >&2
  exit 1
fi

# 5. MỞ CỔNG CHO PROXY (Danteh gọi mọi port qua đây)
# Cho phép Worker gửi dữ liệu tới Proxy port 8888
iptables -A OUTPUT -p tcp -d 127.0.0.1 --dport 8888 -j ACCEPT

echo "[Arona-Guard] Hệ thống phòng thủ đã kích hoạt. Mọi port đã mở qua Proxy."
echo "[Arona-Guard] Sandbox UIDs 2000-9999 are assigned per execution by docker.py."
echo "[Arona-Guard] Starting application as root (entrypoint stays privileged for iptables)..."

# The container entrypoint stays as root because per-execution isolation is
# handled by _prepare_sandbox_workdir (chown + chmod 700) + "-u <uid>" in
# docker exec, not by a global privilege drop here.
exec "$@"