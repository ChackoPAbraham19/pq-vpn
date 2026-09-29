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

# free_port <first> — echo the first port from <first> upwards that nothing is
# listening on. 8080 is popular (Docker web apps love it), so assuming it is
# free turns into "address already in use" at the worst moment.
free_port() {
    local p="$1"
    while ss -ltn 2>/dev/null | grep -q ":${p} "; do p=$((p + 1)); done
    echo "$p"
}

if [ "$MODE" = vps ]; then
    CFG="${PDIR}/client/config-vps.yaml"
    [ -f "$CFG" ] || { echo "missing $CFG" >&2; exit 1; }
    HOST="$(awk '/^server_host:/{print $2}' "$CFG")"
    USER_AT="${PQVPN_VPS_USER:-ubuntu}@${HOST}"

    printf '\n== Server dashboard on %s\n' "$HOST"

    # A dashboard whose SSH session died without a hangup keeps running on the
    # server and holds its port, so clear any leftover before choosing one.
    # $$/$PPID are skipped because this very command line contains the pattern
    # pgrep is matching, so it would otherwise kill its own shell.
    ssh -o ConnectTimeout=10 "$USER_AT" '
        n=0
        for p in $(pgrep -f dashboard_server 2>/dev/null); do
            [ "$p" = "$$" ] || [ "$p" = "$PPID" ] || { sudo kill "$p" 2>/dev/null && n=$((n+1)); }
        done
        [ "$n" -eq 0 ] || echo "   cleared $n leftover dashboard process(es) on the server"
        sleep 1' || { echo "   cannot reach ${USER_AT}" >&2; exit 1; }

    # Two independent ports: the dashboard binds RPORT on the VPS, the browser
    # opens PORT here, and -L maps one to the other. Each is checked on its own
    # machine — a port that is busy here says nothing about the server.
    RPORT="$(ssh -o ConnectTimeout=10 "$USER_AT" \
        'p=8080; while ss -ltn 2>/dev/null | grep -q ":$p "; do p=$((p+1)); done; echo $p')" \
        || { echo "   cannot reach ${USER_AT}" >&2; exit 1; }
    PORT="$(free_port "$PORT")"
    printf '   open:  http://127.0.0.1:%s\n' "$PORT"
    printf '   token: %s\n' "$TOKEN"
    [ "$RPORT" = "${PQVPN_DASH_PORT:-8080}" ] || \
        printf '   (server port %s was busy, using %s there)\n' "${PQVPN_DASH_PORT:-8080}" "$RPORT"
    printf '   (Ctrl-C closes the tunnel and stops the remote dashboard)\n\n'
    # -t gives the remote command a pty, so Ctrl-C reaches it and the dashboard
    # dies with the session instead of lingering and holding its port. If one
    # does linger after an abrupt disconnect:
    #   ssh USER@HOST 'sudo pkill -f dashboard_server' || true
    exec ssh -t -L "${PORT}:127.0.0.1:${RPORT}" "$USER_AT" \
        "cd /opt/pqvpn && sudo PQVPN_DASH_TOKEN=${TOKEN} .venv/bin/python3 dashboard/dashboard_server.py --port ${RPORT}"
fi

PORT="$(free_port "$PORT")"

printf '\n== Local dashboard\n'
printf '   open:  http://127.0.0.1:%s\n' "$PORT"
printf '   token: %s\n' "$TOKEN"
printf '   (shows daemons running on this machine; Ctrl-C to stop)\n\n'
exec env PQVPN_DASH_TOKEN="$TOKEN" "$PY" "${PDIR}/dashboard/dashboard_server.py" --port "$PORT"
