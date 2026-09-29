#!/usr/bin/env bash
set -uo pipefail
# demo_vps.sh - one-command demo of the tunnel against the deployed VPS.
#
#   sudo bash scripts/demo_vps.sh           connect, prove it works, disconnect
#   sudo bash scripts/demo_vps.sh --keep    leave the tunnel up at the end
#
# Everything runs from inside this script, so there are no multi-line shell
# pastes to get mangled. Requires the server deployed (scripts/deploy_vps.sh)
# and client/config-vps.yaml present.

PDIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PDIR}/.venv/bin/python3"
CFG="${PDIR}/client/config-vps.yaml"
LOG=/tmp/pqvpn-vps-client.log
KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1

log() { printf '\n== %s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "run with sudo: sudo bash scripts/demo_vps.sh" >&2; exit 1; }
[ -f "$CFG" ] || { echo "missing $CFG" >&2; exit 1; }
SERVER="$(awk '/^server_host:/{print $2}' "$CFG")"

cleanup() {
    if [ "$KEEP" -eq 0 ]; then
        log "Disconnecting"
        "$PY" "${PDIR}/cli/vpnctl.py" disconnect >/dev/null 2>&1
        sleep 2
        printf '   public IP after disconnect: %s\n' "$(curl -s --max-time 10 ifconfig.me || echo unknown)"
    else
        log "Tunnel left up (--keep). Stop it with: .venv/bin/python3 cli/vpnctl.py disconnect"
    fi
}
trap cleanup EXIT

log "Public IP before connecting"
BEFORE="$(curl -s --max-time 10 ifconfig.me || echo unknown)"
printf '   %s\n' "$BEFORE"

log "Starting the client (post-quantum) against ${SERVER}"
# PYTHONUNBUFFERED: without it Python block-buffers into the log file, so the
# daemon's own progress lines stay invisible while OpenSSL's unbuffered stderr
# streams through — the readiness check below would never see "Relaying".
PYTHONUNBUFFERED=1 "$PY" "${PDIR}/client/vpn_client.py" --config "$CFG" > "$LOG" 2>&1 &
CLIENT_PID=$!
CONNECTED=0
for _ in $(seq 40); do
    sleep 1
    # Either the daemon's own line, or the authoritative check: the IPC status
    # socket reporting the tunnel up. The second works even if logging changes.
    if grep -q "Relaying" "$LOG" 2>/dev/null; then CONNECTED=1; break; fi
    if "$PY" "${PDIR}/cli/vpnctl.py" status 2>/dev/null | grep -q "State *connected"; then CONNECTED=1; break; fi
    if ! kill -0 "$CLIENT_PID" 2>/dev/null; then break; fi
done
if [ "$CONNECTED" -ne 1 ]; then
    echo "   FAIL client did not connect; last lines of $LOG:"
    tail -6 "$LOG" | sed 's/^/     /'
    exit 1
fi
printf '   connected\n'

log "Negotiated cryptography (read back from OpenSSL, not from config)"
"$PY" "${PDIR}/cli/vpnctl.py" status | sed -n '/^Client/,/Bytes out/p' | sed 's/^/   /'

log "Public IP through the tunnel"
AFTER="$(curl -s --max-time 15 ifconfig.me || echo unknown)"
printf '   %s\n' "$AFTER"
if [ "$AFTER" = "$SERVER" ]; then
    printf '   OK   traffic now leaves from the VPS (was %s)\n' "$BEFORE"
else
    printf '   FAIL expected %s, got %s\n' "$SERVER" "$AFTER"
fi

log "DNS through the tunnel (server-side dnsmasq on 10.8.0.1)"
DNS_ANS="$(dig +short +time=3 +tries=1 @10.8.0.1 github.com 2>/dev/null | head -1)"
if [ -n "$DNS_ANS" ]; then
    printf '   OK   github.com resolved to %s by the server\n' "$DNS_ANS"
else
    printf '   FAIL no answer from 10.8.0.1\n'
fi

log "Fetching a real website through the tunnel"
CODE="$(curl -s -o /dev/null -w %{http_code} --max-time 20 https://www.google.com || echo 000)"
if [ "$CODE" = "200" ]; then
    printf '   OK   https://www.google.com returned HTTP %s\n' "$CODE"
else
    printf '   FAIL https://www.google.com returned %s\n' "$CODE"
fi

log "Summary"
printf '   before: %s\n   after : %s  (the VPS)\n   DNS   : %s\n' "$BEFORE" "$AFTER" "${DNS_ANS:-none}"
