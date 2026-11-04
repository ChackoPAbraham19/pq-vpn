#!/usr/bin/env bash
set -uo pipefail
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
# Every data point is repeated PQVPN_SCALING_REPS times (default 3). Single
# samples were the weak spot of the first version of this benchmark: the
# multi-client result reproduced across runs and could be defended, but the
# parallel-stream and latency-under-load points rested on one 10 s sample each,
# which is not enough to separate a finding from scheduler noise.
#
# Writes results/scaling_<mode>.csv: test,variant,rep,value,unit,detail
#
# The detail column records HOW each number was obtained -- above all, how
# many clients actually reported. A multi-client total that silently came
# from two clients instead of three is indistinguishable from a real
# ceiling, and that ambiguity is what made an earlier run unusable.

MODE="${1:-unknown}"
MAX_CLIENTS="${2:-3}"
BENCH_DIR="$(cd "$(dirname "$0")" && pwd)"
RESULTS_DIR="${PQVPN_RESULTS_DIR:-${BENCH_DIR}/results}"
SERVER_NETNS="vpn_ns1"
SERVER_IP="10.8.0.1"
DURATION="${PQVPN_SCALING_SECS:-10}"
REPS="${PQVPN_SCALING_REPS:-3}"
CLIENT_NS=(vpn_ns2 vpn_ns3 vpn_ns4)

mkdir -p "${RESULTS_DIR}"
CSV="${RESULTS_DIR}/scaling_${MODE}.csv"
echo "test,variant,rep,value,unit,detail" > "${CSV}"

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

echo "=== Scaling benchmark: ${MODE} (${REPS} reps per point, ${DURATION}s each) ==="

# --- 1. parallel streams -----------------------------------------------------
echo "-- parallel streams (is the ceiling per-stream or the relay?)"
for P in 1 2 4 8; do
    for rep in $(seq "${REPS}"); do
        v=$(ip netns exec "${CLIENT_NS[0]}" iperf3 -c "${SERVER_IP}" -t "${DURATION}" \
                -P "${P}" -J 2>/dev/null | mbps_of) || true
        echo "   ${P} stream(s) rep ${rep}: ${v:-failed} Mbps"
        echo "parallel_streams,${P},${rep},${v},mbps,streams=${P}" >> "${CSV}"
        sleep 1
    done
done

# --- 2. latency under load ---------------------------------------------------
echo "-- latency while the tunnel is saturated"
# The idle baseline must be measured from INSIDE a client namespace: this
# script runs on the host so it can drive several namespaces at once, and the
# host has no route to the tunnel address. Measuring it here was what made the
# whole script exit after the parallel-stream section.
for rep in $(seq "${REPS}"); do
    idle=$(ip netns exec "${CLIENT_NS[0]}" ping -c 20 -i 0.2 -W 2 "${SERVER_IP}" 2>/dev/null | \
           sed -n 's@.*= \([0-9.]*\)/\([0-9.]*\)/.*@\2@p') || true
    # ping from the same namespace as the load, so it queues behind the same traffic
    ip netns exec "${CLIENT_NS[0]}" iperf3 -c "${SERVER_IP}" -t "${DURATION}" \
        >/dev/null 2>&1 &
    LOAD_PID=$!
    sleep 1
    loaded=$(ip netns exec "${CLIENT_NS[0]}" ping -c 20 -i 0.2 -W 5 "${SERVER_IP}" 2>/dev/null | \
             sed -n 's@.*= \([0-9.]*\)/\([0-9.]*\)/.*@\2@p') || true
    wait "${LOAD_PID}" 2>/dev/null || true
    echo "   rep ${rep}: idle ${idle:-?} ms -> under load ${loaded:-?} ms"
    echo "latency,idle,${rep},${idle},ms,ping20" >> "${CSV}"
    echo "latency,under_load,${rep},${loaded},ms,ping20+iperf" >> "${CSV}"
    sleep 1
done

# --- 3. multi-client scaling -------------------------------------------------
# One iperf3 server handles ONE test at a time: a second concurrent client is
# told the server is busy. So each client gets its own server on its own port,
# and what is measured is genuinely concurrent load on the one shared TUN
# reader rather than three tests taking turns.
echo "-- concurrent clients (one shared TUN reader demuxes all of them)"
# Dedicated ports, deliberately NOT 5201: that port already carries the server
# used by the two sections above, and an iperf3 server runs ONE test at a time.
# Reusing it meant a client could be refused as "server busy" while the others
# succeeded, producing a total that looks like a ceiling but is really a
# missing client.
CPORT_BASE="${PQVPN_SCALING_PORT_BASE:-5301}"
for i in 0 1 2; do
    ip netns exec "${SERVER_NETNS}" iperf3 -s -p $((CPORT_BASE + i)) -D >/dev/null 2>&1 || true
done
sleep 1

# Verify each server is listening before trusting any total below.
srv_up=0
for i in 0 1 2; do
    if ip netns exec "${SERVER_NETNS}" ss -ltn 2>/dev/null | grep -q ":$((CPORT_BASE + i)) "; then
        srv_up=$((srv_up + 1))
    else
        echo "   ! no iperf3 server on port $((CPORT_BASE + i)): that client cannot be measured" >&2
    fi
done
echo "   ${srv_up}/3 per-client iperf3 servers listening on ${CPORT_BASE}-$((CPORT_BASE + 2))"

for N in $(seq 1 "${MAX_CLIENTS}"); do
  for rep in $(seq "${REPS}"); do
    tmp="$(mktemp -d)"
    started=0
    for i in $(seq 0 $((N - 1))); do
        ns="${CLIENT_NS[$i]}"
        ip netns exec "${ns}" ip link show tun0 >/dev/null 2>&1 || continue
        ip netns exec "${ns}" iperf3 -c "${SERVER_IP}" -p $((CPORT_BASE + i)) \
            -t "${DURATION}" -J > "${tmp}/${i}.json" 2>/dev/null &
        started=$((started + 1))
    done
    wait
    # Parse each file on its own: iperf3 -J writes pretty-printed, multi-line
    # JSON, so concatenating the files and splitting on newlines never yields a
    # whole object — which is why this silently reported nothing at all.
    # Report the total AND how it was composed. "found" is the number of
    # clients that produced a parseable result; if it is below N the total is
    # not an N-client measurement and must not be plotted as one.
    res=$(python3 "${BENCH_DIR}/_scaling_parse.py" "${tmp}")
    rm -rf "${tmp}"
    total=$(echo "${res}" | cut -d' ' -f1)
    found=$(echo "${res}" | cut -d' ' -f2)
    per=$(echo "${res}" | cut -d' ' -f3)
    if [ "${found:-0}" -ne "${started}" ] || [ "${started}" -ne "${N}" ]; then
        echo "   ! ${N} client(s) rep ${rep}: ${started} tunnel(s) up, ${found:-0} reported [${per}] -- RECORDED AS INVALID" >&2
        echo "clients,${N},${rep},,mbps,INCOMPLETE_${found:-0}of${N}" >> "${CSV}"
    else
        echo "   ${N} client(s) rep ${rep}: ${total} Mbps total  [${per}]"
        echo "clients,${N},${rep},${total},mbps,${found}of${N}:${per}" >> "${CSV}"
    fi
    sleep 1
  done
done

for i in 0 1 2; do
    ip netns exec "${SERVER_NETNS}" pkill -f "iperf3 -s -p $((CPORT_BASE + i))" 2>/dev/null || true
done

echo "  -> ${CSV}"
