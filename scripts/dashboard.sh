#!/usr/bin/env bash
set -uo pipefail
# dashboard.sh - open the live dashboard, local or on the VPS.
#
#   bash scripts/dashboard.sh            dashboard for daemons on THIS machine
#   bash scripts/dashboard.sh --vps      dashboard for the SERVER, over SSH
#
# The dashboard reads the daemons' Unix sockets, so it only sees daemons on the
# machine it runs on:
#   local  - the client daemon here (and, with the netns harness, the server too)
#   --vps  - the real server: every connected tunnel, from anywhere
#
# Ctrl-C stops it. With --vps that also closes the SSH tunnel and the remote
# process, so nothing is left running on the server.

PDIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PDIR}/.venv/bin/python3"
PORT="${PQVPN_DASH_PORT:-8080}"
TOKEN="${PQVPN_DASH_TOKEN:-demo}"
MODE=local
[ "${1:-}" = "--vps" ] && MODE=vps

# 8080 is a popular port (Docker web apps love it). Pick the next free one
# rather than failing with "address already in use".
while ss -ltn 2>/dev/null | grep -q ":${PORT} "; do
    echo "   port ${PORT} is busy, trying $((PORT + 1))"
    PORT=$((PORT + 1))
done

if [ "$MODE" = vps ]; then
    CFG="${PDIR}/client/config-vps.yaml"
    [ -f "$CFG" ] || { echo "missing $CFG" >&2; exit 1; }
    HOST="$(awk '/^server_host:/{print $2}' "$CFG")"
    USER_AT="${PQVPN_VPS_USER:-ubuntu}@${HOST}"
    printf '\n== Server dashboard on %s\n' "$HOST"
    printf '   open:  http://127.0.0.1:%s\n' "$PORT"
    printf '   token: %s\n' "$TOKEN"
    printf '   (Ctrl-C closes the tunnel and stops the remote dashboard)\n\n'
    exec ssh -t -L "${PORT}:127.0.0.1:${PORT}" "$USER_AT" \
        "cd /opt/pqvpn && sudo PQVPN_DASH_TOKEN=${TOKEN} .venv/bin/python3 dashboard/dashboard_server.py --port ${PORT}"
fi

printf '\n== Local dashboard\n'
printf '   open:  http://127.0.0.1:%s\n' "$PORT"
printf '   token: %s\n' "$TOKEN"
printf '   (shows daemons running on this machine; Ctrl-C to stop)\n\n'
exec env PQVPN_DASH_TOKEN="$TOKEN" "$PY" "${PDIR}/dashboard/dashboard_server.py" --port "$PORT"
