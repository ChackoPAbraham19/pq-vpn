#!/usr/bin/env bash
set -euo pipefail
# run_latency_bench.sh — Measure ping latency through the VPN tunnel
#
# Prerequisites: VPN tunnel is up (client -> server ping works)
# Usage: sudo ip netns exec vpn_ns2 bash benchmark/run_latency_bench.sh <mode>
#   <mode>: classical | pqc

MODE="${1:-unknown}"
RESULTS_DIR="${PQVPN_RESULTS_DIR:-$(dirname "$0")/results}"
TARGET="10.8.0.1"
COUNT=100

mkdir -p "${RESULTS_DIR}"
CSV="${RESULTS_DIR}/latency_${MODE}.csv"

echo "=== Latency benchmark: ${MODE} (ping -c ${COUNT} ${TARGET}) ==="

raw=$(ping -c "${COUNT}" "${TARGET}" 2>&1)

# Parse summary line: rtt min/avg/max/mdev = 1.163/660.676/2127.786/843.463 ms
stats=$(echo "$raw" | grep 'rtt min/avg/max/mdev')
if [ -z "$stats" ]; then
    echo "ERROR: could not parse ping output" >&2
    exit 1
fi

min=$(echo "$stats" | sed -n 's/.*= \([0-9.]*\)\/\([0-9.]*\)\/\([0-9.]*\)\/\([0-9.]*\).*/\1/p')
avg=$(echo "$stats" | sed -n 's/.*= \([0-9.]*\)\/\([0-9.]*\)\/\([0-9.]*\)\/\([0-9.]*\).*/\2/p')
max=$(echo "$stats" | sed -n 's/.*= \([0-9.]*\)\/\([0-9.]*\)\/\([0-9.]*\)\/\([0-9.]*\).*/\3/p')
mdev=$(echo "$stats" | sed -n 's/.*= \([0-9.]*\)\/\([0-9.]*\)\/\([0-9.]*\)\/\([0-9.]*\).*/\4/p')

echo "mode,min_ms,avg_ms,max_ms,mdev_ms" > "${CSV}"
echo "${MODE},${min},${avg},${max},${mdev}" >> "${CSV}"

echo "  min=${min} avg=${avg} max=${max} mdev=${mdev}"
echo "  -> ${CSV}"
