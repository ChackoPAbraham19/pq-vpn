#!/usr/bin/env bash
set -euo pipefail
# run_latency_bench.sh — ping RTT through the tunnel, repeated.
#
# Usage: sudo ip netns exec vpn_ns2 bash benchmark/run_latency_bench.sh <mode>
#
# PQVPN_LAT_REPS independent runs of PQVPN_LAT_COUNT pings each. The previous
# version did one run, so the published figure had no repetition behind it at
# all -- and a single cold first ping was enough to drag its mean from ~1.2 ms
# to 2.3 ms. Repeating makes that visible instead of silent.
#
# Interval is 0.2 s (root only) so three runs cost ~60 s rather than ~5 min.
# This is an IDLE-tunnel measurement; run_scaling_bench.sh does the same ping
# under saturation, which is the harder and more honest case.
#
# Writes mode,rep,min_ms,avg_ms,max_ms,mdev_ms

MODE="${1:-unknown}"
RESULTS_DIR="${PQVPN_RESULTS_DIR:-$(dirname "$0")/results}"
TARGET="${PQVPN_BENCH_SERVER_IP:-10.8.0.1}"
COUNT="${PQVPN_LAT_COUNT:-100}"
REPS="${PQVPN_LAT_REPS:-3}"
INTERVAL="${PQVPN_LAT_INTERVAL:-0.2}"

mkdir -p "${RESULTS_DIR}"
CSV="${RESULTS_DIR}/latency_${MODE}.csv"
echo "mode,rep,min_ms,avg_ms,max_ms,mdev_ms" > "${CSV}"

echo "=== Latency benchmark: ${MODE} (${REPS} reps x ${COUNT} pings, -i ${INTERVAL}) ==="

ok=0
for rep in $(seq "${REPS}"); do
    stats=$(ping -c "${COUNT}" -i "${INTERVAL}" "${TARGET}" 2>&1 | grep 'rtt min/avg/max/mdev' || true)
    if [ -z "$stats" ]; then
        echo "  rep ${rep}: no reply from ${TARGET}" >&2
        continue
    fi
    vals=$(echo "$stats" | sed -n 's@.*= \([0-9.]*\)/\([0-9.]*\)/\([0-9.]*\)/\([0-9.]*\).*@\1,\2,\3,\4@p')
    # A row with missing columns is worse than no row: it parses, and reads as
    # a measurement. Only write one when all four numbers came back.
    if [ "$(echo "$vals" | tr -cd ',' | wc -c)" -ne 3 ]; then
        echo "  rep ${rep}: could not parse ping summary: ${stats}" >&2
        continue
    fi
    echo "${MODE},${rep},${vals}" >> "${CSV}"
    echo "  rep ${rep}: min/avg/max/mdev = ${vals//,//}"
    ok=$((ok + 1))
done

[ "$ok" -gt 0 ] || { echo "ERROR: every ping run failed" >&2; exit 1; }
echo "  -> ${CSV}"
