#!/usr/bin/env bash
set -euo pipefail
# run_throughput_bench.sh — Measure TCP and UDP throughput through the tunnel
#
# Prerequisites: VPN tunnel is up, iperf3 is installed
# Usage:
#   TERMINAL 1 (server ns): sudo ip netns exec vpn_ns1 iperf3 -s
#   TERMINAL 2 (client ns): sudo ip netns exec vpn_ns2 bash benchmark/run_throughput_bench.sh <mode>

MODE="${1:-unknown}"
RESULTS_DIR="${PQVPN_RESULTS_DIR:-$(dirname "$0")/results}"
SERVER_IP="10.8.0.1"
DURATION=10

mkdir -p "${RESULTS_DIR}"
CSV="${RESULTS_DIR}/throughput_${MODE}.csv"

echo "=== Throughput benchmark: ${MODE} ==="

echo "mode,protocol,mbps" > "${CSV}"

# TCP
echo "  TCP (${DURATION}s)..."
tcp_raw=$(iperf3 -c "${SERVER_IP}" -t "${DURATION}" -J 2>/dev/null || \
          iperf3 -c "${SERVER_IP}" -t "${DURATION}" 2>&1 | tail -3)
tcp_mbps=$(echo "$tcp_raw" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(round(d['end']['sum_received']['bits_per_second'] / 1e6, 2))
except Exception:
    print('?')
" 2>/dev/null || echo '?')
echo "${MODE},tcp,${tcp_mbps}" >> "${CSV}"
echo "  TCP: ${tcp_mbps} Mbps"

# UDP
echo "  UDP (${DURATION}s)..."
udp_raw=$(iperf3 -c "${SERVER_IP}" -u -t "${DURATION}" -b 1000M -J 2>/dev/null || \
          iperf3 -c "${SERVER_IP}" -u -t "${DURATION}" -b 1000M 2>&1 | tail -3)
udp_mbps=$(echo "$udp_raw" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(round(d['end']['sum']['bits_per_second'] / 1e6, 2))
except Exception:
    print('?')
" 2>/dev/null || echo '?')
echo "${MODE},udp,${udp_mbps}" >> "${CSV}"
echo "  UDP: ${udp_mbps} Mbps"

echo "  -> ${CSV}"
