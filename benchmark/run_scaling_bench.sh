#!/usr/bin/env bash
set -euo pipefail
# run_scaling_bench.sh — the three questions the other benchmarks do not answer.
#
# Usage: sudo bash benchmark/run_scaling_bench.sh <mode> [clients]
#   run from the HOST (not inside a namespace): it drives several client
#   namespaces at once, which a single-namespace script cannot do.
#
# 1. PARALLEL STREAMS. docs/PROJECT_REPORT.md §15 claims throughput is bounded
#    by the Python relay rather than by OpenSSL. If the ceiling were per-stream,
#    running more streams would raise the total; if it is the relay, the total
#    is flat however many streams are used. Until now that claim was asserted,
#    never measured. iperf3 -P 1,2,4,8.
#
# 2. LATENCY UNDER LOAD. The published latency figure is from an idle tunnel,
#    which is the easy case. A VPN that holds 2 ms idle but collapses to
#    seconds while a transfer saturates it is a bad VPN, and nothing here would
#    have caught that. Ping while iperf3 runs.
#
# 3. MULTI-CLIENT SCALING. The server demultiplexes every client's return
#    traffic through ONE shared TUN reader (server/vpn_server.py). Whether that
#    is a bottleneck is the obvious question about the design, and it has never
#    been measured. Total throughput with 1, 2 and 3 clients at once.
#
# Writes results/scaling_<mode>.csv: test,variant,value,unit

MODE="${1:-unknown}"
MAX_CLIENTS="${2:-3}"
BENCH_DIR="$(cd "$(dirname "$0")" && pwd)"
RESULTS_DIR="${BENCH_DIR}/results"
SERVER_NETNS="vpn_ns1"
SERVER_IP="10.8.0.1"
DURATION="${PQVPN_SCALING_SECS:-10}"
CLIENT_NS=(vpn_ns2 vpn_ns3 vpn_ns4)

mkdir -p "${RESULTS_DIR}"
CSV="${RESULTS_DIR}/scaling_${MODE}.csv"
echo "test,variant,value,unit" > "${CSV}"

command -v iperf3 >/dev/null 2>&1 || { echo "iperf3 not installed" >&2; exit 1; }

# One iperf3 server for the whole run.
ip netns exec "${SERVER_NETNS}" iperf3 -s -D >/dev/null 2>&1 || true
sleep 1
cleanup() { ip netns exec "${SERVER_NETNS}" pkill -f "iperf3 -s" 2>/dev/null || true; }
trap cleanup EXIT

mbps_of() {  # read one iperf3 JSON result from stdin
    python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(round(d['end']['sum_received']['bits_per_second'] / 1e6, 2))
except Exception:
    print('')
"
}

echo "=== Scaling benchmark: ${MODE} ==="

# --- 1. parallel streams -----------------------------------------------------
echo "-- parallel streams (is the ceiling per-stream or the relay?)"
for P in 1 2 4 8; do
    v=$(ip netns exec "${CLIENT_NS[0]}" iperf3 -c "${SERVER_IP}" -t "${DURATION}" \
            -P "${P}" -J 2>/dev/null | mbps_of)
    echo "   ${P} stream(s): ${v:-failed} Mbps"
    echo "parallel_streams,${P},${v},mbps" >> "${CSV}"
done

# --- 2. latency under load ---------------------------------------------------
echo "-- latency while the tunnel is saturated"
idle=$(ping -c 20 -i 0.2 -W 2 "${SERVER_IP}" 2>/dev/null | \
       sed -n 's@.*= \([0-9.]*\)/\([0-9.]*\)/.*@\2@p')
# ping from the same namespace as the load, so it queues behind the same traffic
ip netns exec "${CLIENT_NS[0]}" iperf3 -c "${SERVER_IP}" -t "${DURATION}" \
    >/dev/null 2>&1 &
LOAD_PID=$!
sleep 1
loaded=$(ip netns exec "${CLIENT_NS[0]}" ping -c 20 -i 0.2 -W 5 "${SERVER_IP}" 2>/dev/null | \
         sed -n 's@.*= \([0-9.]*\)/\([0-9.]*\)/.*@\2@p')
wait "${LOAD_PID}" 2>/dev/null || true
echo "   idle ${idle:-?} ms -> under load ${loaded:-?} ms"
echo "latency,idle,${idle},ms" >> "${CSV}"
echo "latency,under_load,${loaded},ms" >> "${CSV}"

# --- 3. multi-client scaling -------------------------------------------------
echo "-- concurrent clients (one shared TUN reader demuxes all of them)"
for N in $(seq 1 "${MAX_CLIENTS}"); do
    tmp="$(mktemp -d)"
    for i in $(seq 0 $((N - 1))); do
        ns="${CLIENT_NS[$i]}"
        ip netns exec "${ns}" ip link show tun0 >/dev/null 2>&1 || continue
        ip netns exec "${ns}" iperf3 -c "${SERVER_IP}" -t "${DURATION}" -J \
            > "${tmp}/${i}.json" 2>/dev/null &
    done
    wait
    total=$(cat "${tmp}"/*.json 2>/dev/null | python3 -c "
import sys, json
tot = 0.0
buf = sys.stdin.read()
for chunk in buf.replace('}{', '}\n{').splitlines():
    try:
        tot += json.loads(chunk)['end']['sum_received']['bits_per_second']
    except Exception:
        pass
print(round(tot / 1e6, 2) if tot else '')
")
    rm -rf "${tmp}"
    echo "   ${N} client(s): ${total:-failed} Mbps total"
    echo "clients,${N},${total},mbps" >> "${CSV}"
done

echo "  -> ${CSV}"
