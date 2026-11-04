#!/usr/bin/env bash
set -euo pipefail
# run_throughput_bench.sh — iperf3 through the tunnel, repeated.
#
# Usage: sudo ip netns exec vpn_ns2 bash benchmark/run_throughput_bench.sh <mode>
#
# Runs in two PHASES, because one of the measurements is destructive:
#
#   main   (default)  tcp + udp_1000m. Safe to run before other benchmarks.
#   flood             udp_max only. Appends to the CSV the main phase wrote.
#
# Why the split: udp_max offers UDP at line rate, which is ~60% lost by design
# and leaves the relay draining a full TUN queue. Running that immediately
# before the scaling and transfer benchmarks put every later measurement in
# the shadow of a flood, and a run taken that way disagreed with the previous
# run in ways cryptography cannot explain. The orchestrator therefore runs the
# flood phase LAST, after everything else for that mode has been measured.
# PQVPN_TPUT_PHASE selects the phase; PQVPN_TPUT_UDP_MAX=0 skips the flood.
#
# The three measurements, and why each is here:
#
#   tcp        A single TCP stream, repeated PQVPN_TPUT_REPS times. One 10 s
#              sample cannot distinguish "the modes agree" from "the two
#              samples landed close together" -- an earlier single-sample pair
#              reported 1268 vs 533 Mbps, a gap that was entirely scheduling
#              noise.
#
#   udp_1000m  UDP offered at 1000 Mbit/s. NOT a ceiling: it answers "if we ask
#              for 1 Gbps, does it arrive?". Kept for continuity with the
#              published series.
#
#   udp_max    UDP offered at line rate (-b 0). THIS is the ceiling. Expect
#              heavy loss: the tunnel's outlet is a TCP/TLS stream, so UDP
#              flooded into the TUN is dropped at the queue rather than
#              delivered.
#
# Every row records loss_pct. A UDP throughput figure without its loss is not
# interpretable -- and the previous version of this script reported the SENT
# rate (end.sum) rather than the delivered rate (end.sum_received), so its
# "999.89 Mbps, 0% penalty" was describing what we transmitted, not what
# arrived.
#
# Writes mode,protocol,rep,mbps,loss_pct

MODE="${1:-unknown}"
RESULTS_DIR="${PQVPN_RESULTS_DIR:-$(dirname "$0")/results}"
SERVER_IP="${PQVPN_BENCH_SERVER_IP:-10.8.0.1}"
DURATION="${PQVPN_TPUT_SECS:-10}"
REPS="${PQVPN_TPUT_REPS:-3}"
PHASE="${PQVPN_TPUT_PHASE:-main}"

mkdir -p "${RESULTS_DIR}"
CSV="${RESULTS_DIR}/throughput_${MODE}.csv"

# Pull delivered Mbps and loss out of one iperf3 JSON document.
#   sum_received = what ARRIVED          <- the throughput figure
#   sum / sum_sent = what was OFFERED    <- not throughput
# TCP has no loss field, so loss comes back empty rather than a misleading 0.
parse() {
    python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    end = d['end']
    recv = end.get('sum_received') or end.get('sum')
    loss = (end.get('sum_received') or end.get('sum') or {}).get('lost_percent')
    print(f\"{recv['bits_per_second'] / 1e6:.2f},{'' if loss is None else format(loss, '.2f')}\")
except Exception:
    print(',')
"
}

run_one() {  # name, extra iperf3 args...
    local name="$1"; shift
    local rep out mbps loss
    for rep in $(seq "${REPS}"); do
        out=$(iperf3 -c "${SERVER_IP}" -t "${DURATION}" -J "$@" 2>/dev/null | parse)
        mbps="${out%%,*}"
        loss="${out#*,}"
        if [ -z "${mbps}" ]; then
            # An unparseable run is recorded as a failure, not skipped: a
            # missing row is invisible, an empty one is not.
            echo "    ! ${name} rep ${rep}: FAILED (no result from iperf3)" >&2
            echo "${MODE},${name},${rep},,"  >> "${CSV}"
        elif [ -n "${loss}" ]; then
            echo "    ${name} rep ${rep}: ${mbps} Mbps delivered, loss ${loss}%"
            echo "${MODE},${name},${rep},${mbps},${loss}" >> "${CSV}"
        else
            echo "    ${name} rep ${rep}: ${mbps} Mbps"
            echo "${MODE},${name},${rep},${mbps}," >> "${CSV}"
        fi
        sleep 1
    done
}

case "${PHASE}" in
  main)
    echo "=== Throughput benchmark: ${MODE}, main phase (${REPS} reps x ${DURATION}s) ==="
    echo "mode,protocol,rep,mbps,loss_pct" > "${CSV}"
    echo "  TCP, single stream"
    run_one tcp
    echo "  UDP at 1000 Mbit/s offered (not a ceiling -- see header)"
    run_one udp_1000m -u -b 1000M
    ;;
  flood)
    if [ "${PQVPN_TPUT_UDP_MAX:-1}" != "1" ]; then
        echo "=== Throughput benchmark: ${MODE}, flood phase SKIPPED (PQVPN_TPUT_UDP_MAX=0) ==="
        exit 0
    fi
    # Append: the main phase created this file and wrote the header.
    [ -f "${CSV}" ] || echo "mode,protocol,rep,mbps,loss_pct" > "${CSV}"
    echo "=== Throughput benchmark: ${MODE}, flood phase (${REPS} reps x ${DURATION}s) ==="
    echo "  UDP at line rate (-b 0) -- the real ceiling, heavy loss is expected"
    run_one udp_max -u -b 0
    # Let the relay drain, so that whatever runs next is not measured in the
    # shadow of the flood.
    sleep "${PQVPN_TPUT_COOLDOWN:-5}"
    ;;
  *)
    echo "unknown PQVPN_TPUT_PHASE: ${PHASE} (expected main or flood)" >&2
    exit 2
    ;;
esac

echo "  -> ${CSV}"
