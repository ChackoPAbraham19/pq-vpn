#!/usr/bin/env bash
set -euo pipefail
# diagnose_tunnel.sh – Step-by-step tunnel diagnostic
# Run:  sudo bash scripts/diagnose_tunnel.sh
# Output shows exactly where TCP breaks.

CDIR="$(cd "$(dirname "$0")" && pwd)"
PDIR="$(dirname "$CDIR")"
LOGDIR="/tmp/pqvpn-diag"
mkdir -p "$LOGDIR"

cleanup() {
    echo "" | tee -a "$LOGDIR/report.txt"
    echo "=== Cleanup ===" | tee -a "$LOGDIR/report.txt"
    bash "$CDIR/setup_routing.sh" --teardown >> "$LOGDIR/report.txt" 2>&1 || true
}
trap cleanup EXIT SIGINT SIGTERM

exec > >(tee -a "$LOGDIR/report.txt") 2>&1

echo "=== PQ-VPN Diagnostic ==="
date

echo ""
echo "=== Step 1: Setup namespaces ==="
cd "$PDIR"
bash "$CDIR/setup_routing.sh"

echo ""
echo "=== Step 2: Start daemons (unbuffered) ==="
PYTHONUNBUFFERED=1 sudo -E ip netns exec vpn_ns1 python3 server/vpn_server.py > $LOGDIR/server.log 2>&1 &
SERVER_PID=$!
PYTHONUNBUFFERED=1 sudo -E ip netns exec vpn_ns2 python3 client/vpn_client.py > $LOGDIR/client.log 2>&1 &
CLIENT_PID=$!
sleep 3

echo "Server log size: $(wc -c < $LOGDIR/server.log) bytes"
echo "Client log size: $(wc -c < $LOGDIR/client.log) bytes"
if [ -s "$LOGDIR/server.log" ]; then
    echo "--- Server log ---"
    cat "$LOGDIR/server.log"
fi
if [ -s "$LOGDIR/client.log" ]; then
    echo "--- Client log ---"
    cat "$LOGDIR/client.log"
fi

echo ""
echo "=== Step 3: Ping test (ICMP) ==="
if sudo ip netns exec vpn_ns2 ping -c 3 -W 5 10.8.0.1; then
    echo "✅ Ping OK"
else
    echo "❌ Ping FAILED"
    exit 1
fi

echo ""
echo "=== Step 4: TCP echo test (netcat) ==="
echo "Starting TCP listener in vpn_ns1..."
sudo ip netns exec vpn_ns1 timeout 10 nc -l -p 9999 > $LOGDIR/nc_received.txt &
NC_PID=$!
sleep 1

echo "Connecting from vpn_ns2..."
echo "HELLO_FROM_TUNNEL" | sudo ip netns exec vpn_ns2 timeout 5 nc -w 3 10.8.0.1 9999 || echo "⏰ nc timed out"
wait $NC_PID 2>/dev/null || true

if [ -s "$LOGDIR/nc_received.txt" ]; then
    echo "✅ TCP echo OK — received: $(cat $LOGDIR/nc_received.txt)"
else
    echo "❌ TCP echo FAILED — no data received"
fi

echo ""
echo "=== Step 5: HTTP server test ==="
echo "Starting HTTP server in vpn_ns1..."
sudo ip netns exec vpn_ns1 bash -c 'cd /tmp && python3 -m http.server 8000 --bind 0.0.0.0' > $LOGDIR/http_server.log 2>&1 &
HTTP_PID=$!
sleep 2

echo "Connecting from client (curl)..."
sudo ip netns exec vpn_ns2 curl -v --connect-timeout 10 --max-time 15 \
    http://10.8.0.1:8000/ > $LOGDIR/curl_out.txt 2>$LOGDIR/curl_stderr.txt || echo "⏰ curl timed out"

echo "Curl stderr:"
cat "$LOGDIR/curl_stderr.txt"

echo ""
echo "=== Step 6: Check daemon logs ==="
echo "Server log:"
cat "$LOGDIR/server.log" 2>/dev/null || echo "(empty)"
echo "Client log:"
cat "$LOGDIR/client.log" 2>/dev/null || echo "(empty)"
echo "HTTP server log:"
cat "$LOGDIR/http_server.log" 2>/dev/null || echo "(empty)"

echo ""
echo "=== Step 7: Network state ==="
echo "vpn_ns1 routing:"
sudo ip netns exec vpn_ns1 ip route
echo "vpn_ns2 routing:"
sudo ip netns exec vpn_ns2 ip route
echo "vpn_ns1 listening sockets:"
sudo ip netns exec vpn_ns1 ss -tlnp
echo "vpn_ns2 listening sockets:"
sudo ip netns exec vpn_ns2 ss -tlnp

echo ""
echo "=== Diagnostic complete ==="
kill $SERVER_PID $CLIENT_PID $HTTP_PID 2>/dev/null || true
wait 2>/dev/null || true
