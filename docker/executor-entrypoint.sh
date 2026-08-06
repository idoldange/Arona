#!/bin/sh
set -e
# arona-executor no longer needs NET_ADMIN/NET_RAW - all iptables/killswitch
# logic lives in warp-killswitch.sh inside the warp container.
#
# This entrypoint only exists to (a) wait until the proxy is actually
# reachable (belt-and-suspenders on top of the compose healthcheck) and
# (b) drop from root to a fixed unprivileged uid BEFORE settling into the
# long-lived placeholder process. docker.py still `docker exec -u root`
# per-execution when it needs to chown a fresh workdir (CAP_CHOWN is a
# container-level grant, independent of what uid PID 1 runs as).

echo "[Arona-Executor] Cho proxy san sang..."
COUNT=0
MAX_RETRIES=60
while [ $COUNT -lt $MAX_RETRIES ]; do
  if curl -s --max-time 2 --proxy http://127.0.0.1:8888 https://api4.ipify.org >/dev/null 2>&1; then
    echo "[Arona-Executor] Proxy OK."
    break
  fi
  sleep 2
  COUNT=$((COUNT + 1))
done
if [ $COUNT -eq $MAX_RETRIES ]; then
  echo "[Arona-Executor] CRITICAL: Proxy timeout." >&2
  exit 1
fi

echo "[Arona-Executor] Ha quyen tu root -> uid 1000 (sandboxuser) truoc khi vao vong lap chinh."
exec su-exec 1000:1000 "$@"
