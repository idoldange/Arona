#!/usr/bin/env bash
# Dong goi Activity: ban build cua Signal (activity/signal/app/dist) + dau vao Discord + trang chan doan -> activity/web (server phuc vu thu muc nay).
# Truoc do: cd activity/signal && npx pnpm@10.34.5 install && npx pnpm@10.34.5 run build
set -euo pipefail
cd "$(dirname "$0")"

# esbuild + Embedded App SDK cai trong activity/client (npm install)
ESBUILD=./client/node_modules/.bin/esbuild
export NODE_PATH="$PWD/client/node_modules"

rm -rf web && mkdir web
cp -r signal/app/dist/. web/

# Khong dung service worker trong iframe (cache cu rat kho debug) va khong dung Google Analytics / Google Fonts (Discord chan, va khong gui du lieu nguoi dung di)
rm -f web/service-worker.js* web/workbox-*
python - <<'PYEOF'
import re
for page in ("web/edit.html", "web/index.html"):
    try:
        html = open(page, encoding="utf-8").read()
    except FileNotFoundError:
        continue
    html = re.sub(r"<!--\s*Global site tag.*?-->\s*<script\s+async.*?</script>\s*<script>.*?</script>", "", html, flags=re.S)
    html = re.sub(r'<link\s+rel="preconnect"[^>]*>', "", html, flags=re.S)
    html = re.sub(r'<link\s+href="https://fonts\.googleapis\.com[^>]*>', "", html, flags=re.S)
    if "arona-boot.js" not in html and page.endswith("edit.html"):
        html = html.replace("</head>", '  <script type="module" src="/arona-boot.js"></script>\n</head>', 1)
    open(page, "w", encoding="utf-8").write(html)
PYEOF

# Soundfont cua Signal (duong dan /A320U.sf2 da sua trong ma nguon) -> tai ve 1 lan tu CDN goc va phuc vu tu server cua minh
mkdir -p asset-cache
fetch() { [ -s "asset-cache/$2" ] || curl -fsSL --retry 3 -o "asset-cache/$2" "$1"; cp "asset-cache/$2" "web/$2"; }
fetch "https://cdn.jsdelivr.net/gh/ryohey/signal@4569a31/public/A320U.sf2" A320U.sf2
fetch "https://cdn.jsdelivr.net/gh/ryohey/signal@6959f35/public/A320U_drums.sf2" A320U_drums.sf2

"$ESBUILD" client/boot.js --bundle --format=esm --minify --outfile=web/arona-boot.js
"$ESBUILD" diag/main.js --bundle --format=esm --minify --outfile=web/arona-diag.js
cp diag/index.html web/diag.html
echo "web/ ready: $(du -sh web | cut -f1)"
