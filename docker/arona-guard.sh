#!/bin/sh
set -e
# Chay TRONG container warp, TRUOC khi WARP khoi dong (xem entrypoint trong compose).
# Netns nay dung chung cho warp + proxy + worker, nen rule o day ap dung cho ca 3.
# Image caomingjun/warp KHONG co iptables, chi co nft.

# Idempotent: xoa bang cu neu container restart
sudo nft delete table inet arona_guard 2>/dev/null || true

sudo nft -f - <<'EOF'
table inet arona_guard {
  chain output {
    type filter hook output priority -10; policy accept;
    oifname "lo" accept
    ct state established,related accept
    # Chan toan bo mang noi bo: LAN, gateway docker (= may host), CGNAT/Tailscale, link-local
    ip daddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16, 100.64.0.0/10 } counter reject
    ip6 daddr { fc00::/7, fe80::/10 } counter reject
  }
}
EOF

echo "[arona-guard] Da chan mang noi bo cho netns warp/proxy/worker."
