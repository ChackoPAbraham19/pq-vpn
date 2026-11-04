#!/usr/bin/env bash
set -uo pipefail
# run_all_benchmarks.sh — regenerate every published measurement, in one command.
#
#   sudo bash scripts/run_all_benchmarks.sh              both modes, full sweep
#   sudo bash scripts/run_all_benchmarks.sh --quick      short sizes, 1 rep
#   sudo bash scripts/run_all_benchmarks.sh --mode pqc   one mode only
#
# Order matters. The rootless benchmarks run first, so a mistake in the harness
# cannot cost the handshake numbers; then the namespace harness goes up, the
# in-tunnel benchmarks run against a live tunnel per mode, and the harness comes
# down. Plots are regenerated last, from whatever was written.
#
# The harness is torn down on EVERY exit path, including a failure or Ctrl-C: a
# half-built harness leaves stale tun0 devices, namespaces and listening ports
# that make the next run fail for an unrelated reason.
#
# WAN handshake numbers are NOT regenerated here — they need the deployed VPS
# (benchmark/run_wan_handshake_bench.py) and a working internet path.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PDIR="$(dirname "$SCRIPT_DIR")"
PY="${PDIR}/.venv/bin/python3"
BENCH="${PDIR}/benchmark"
RESULTS="${BENCH}/results"

# Mode order matters and is a known confound: the modes are measured one after
# the other, so anything that drifts with time (CPU temperature, page cache,
# another process starting) lands unevenly and reads as a difference between
# modes. A warm-up pass before each mode's measurements absorbs most of it.
# Set PQVPN_MODE_ORDER=reverse to measure pqc first; if a "difference between
# modes" flips sign when you do, it was ordering, not cryptography.
if [ "${PQVPN_MODE_ORDER:-forward}" = "reverse" ]; then
    MODES=(pqc classical)
else
    MODES=(classical pqc)
fi
HS_REPS=5          # 5 x 25 trials = the n=125 the published tables use
HS_TRIALS=25
RS_REPS=3          # 3 x 20 = n=60 per kind
RS_TRIALS=20
QUICK=0

# Every in-tunnel measurement is repeated. Single samples were this suite's
# weakest point: they cannot separate "the two modes agree" from "the two
# samples landed close together", and one earlier single-sample pair reported a
# 2.4x throughput gap that was entirely scheduling noise.
export PQVPN_TPUT_REPS="${PQVPN_TPUT_REPS:-3}"
export PQVPN_LAT_REPS="${PQVPN_LAT_REPS:-3}"
export PQVPN_SCALING_REPS="${PQVPN_SCALING_REPS:-3}"
# Set to 0 to skip the line-rate UDP flood (see run_throughput_bench.sh).
export PQVPN_TPUT_UDP_MAX="${PQVPN_TPUT_UDP_MAX:-1}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --quick) QUICK=1; shift ;;
        --mode) MODES=("$2"); shift 2 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done
if [[ "$QUICK" -eq 1 ]]; then
    HS_REPS=1; RS_REPS=1
    export PQVPN_BENCH_SIZES="1K 1M 10M"
    export PQVPN_BENCH_REPS=1
    export PQVPN_TPUT_REPS=1
    export PQVPN_LAT_REPS=1
    export PQVPN_SCALING_REPS=1
    # not shortened: 3 s runs made the throughput figures swing by 2x
    export PQVPN_SCALING_SECS=10
    # A smoke test must not overwrite published results. It writes a fifth of
    # the handshake trials and three of the seven transfer sizes, so pointed at
    # results/ it silently replaces good data with worse data -- which is
    # exactly what happened once. Quick runs land in a temp directory instead.
    RESULTS="$(mktemp -d)/results"
    mkdir -p "$RESULTS"
    QUICK_NOTE="   --quick: results in ${RESULTS} (published CSVs untouched)"
fi

[ "$(id -u)" -eq 0 ] || { echo "run with sudo: sudo bash scripts/run_all_benchmarks.sh" >&2; exit 1; }
[ -x "$PY" ] || { echo "missing ${PY}" >&2; exit 1; }

log()  { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }
[ -n "${QUICK_NOTE:-}" ] && echo "$QUICK_NOTE"
warn() { printf '\033[1;33m   ! %s\033[0m\n' "$*"; }

STARTED_HARNESS=0
cleanup() {
    if [[ "$STARTED_HARNESS" -eq 1 ]]; then
        log "Tearing down"
        pkill -f "vpn_server.py|vpn_client.py" >/dev/null 2>&1 || true
        bash "${SCRIPT_DIR}/setup_routing.sh" --teardown >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT SIGINT SIGTERM

cd "$PDIR"
mkdir -p "$RESULTS"
export PQVPN_SKIP_DNS=1      # netns has no resolved and a write-blocked resolv.conf
export PYTHONUNBUFFERED=1    # or daemon logs stay empty until exit
export PQVPN_RESULTS_DIR="$RESULTS"

DISK_BEFORE="$(df --output=avail / | tail -1)"

############################################################ rootless
log "1/5  Rootless benchmarks (no harness needed)"

TMPHS="$(mktemp -d)"
for mode in "${MODES[@]}"; do
    echo "   handshake ${mode}: ${HS_REPS} x ${HS_TRIALS} trials"
    for rep in $(seq "$HS_REPS"); do
        "$PY" "${BENCH}/run_handshake_bench.py" --modes "$mode" \
            --trials "$HS_TRIALS" --out-dir "${TMPHS}/hs${rep}" 2>&1 | grep -E 'median|error' | sed 's/^/     /'
    done
    echo "   resumption ${mode}: ${RS_REPS} x ${RS_TRIALS} trials"
    for rep in $(seq "$RS_REPS"); do
        "$PY" "${BENCH}/run_handshake_bench.py" --resume --modes "$mode" \
            --trials "$RS_TRIALS" --out-dir "${TMPHS}/rs${rep}" 2>&1 | grep -E 'median|faster|error' | sed 's/^/     /'
    done
done

# Pool the repetitions into the committed CSVs, exactly as the published tables
# describe: the ratio between modes is stable, individual run medians are not.
"$PY" - "$TMPHS" "$RESULTS" "${MODES[@]}" <<'PY'
import csv, glob, os, sys, statistics as s
tmp, out = sys.argv[1], sys.argv[2]
for mode in sys.argv[3:]:
    rows = []
    for f in sorted(glob.glob(os.path.join(tmp, 'hs*', f'handshake_{mode}.csv'))):
        rows += [float(r['ms']) for r in csv.DictReader(open(f))]
    if rows:
        with open(os.path.join(out, f'handshake_{mode}.csv'), 'w', newline='') as fh:
            w = csv.writer(fh, lineterminator='\n'); w.writerow(['trial_id', 'mode', 'ms'])
            for i, ms in enumerate(rows, 1):
                w.writerow([i, mode, f'{ms:.2f}'])
        print(f'   pooled handshake {mode}: n={len(rows)} median={s.median(rows):.2f}ms')
    rr = []
    for f in sorted(glob.glob(os.path.join(tmp, 'rs*', f'handshake_resume_{mode}.csv'))):
        rr += [(r['kind'], float(r['ms'])) for r in csv.DictReader(open(f)) if r['ms'] != 'err']
    if rr:
        with open(os.path.join(out, f'handshake_resume_{mode}.csv'), 'w', newline='') as fh:
            w = csv.writer(fh, lineterminator='\n'); w.writerow(['trial_id', 'mode', 'kind', 'ms'])
            for i, (k, ms) in enumerate(rr, 1):
                w.writerow([i, mode, k, f'{ms:.2f}'])
        full = [m for k, m in rr if k == 'full']; res = [m for k, m in rr if k == 'resumed']
        print(f'   pooled resumption {mode}: full={s.median(full):.2f}ms '
              f'resumed={s.median(res):.2f}ms ({s.median(full)/s.median(res):.2f}x)')
PY
rm -rf "$TMPHS"

echo "   handshake bytes (protocol sizes, mode-independent)"
"$PY" "${BENCH}/measure_handshake_bytes.py" 2>&1 | grep -E 'client->server|HelloRetry' | sed 's/^/     /'

############################################################ harness
log "2/5  Building the netns harness"
if pgrep -f "vpn_server.py|vpn_client.py" >/dev/null 2>&1; then
    warn "stale daemons running; killing them first"
    pkill -f "vpn_server.py|vpn_client.py" >/dev/null 2>&1 || true
    sleep 1
fi
bash "${SCRIPT_DIR}/setup_routing.sh" >/dev/null || { echo "harness setup failed" >&2; exit 1; }
STARTED_HARNESS=1

############################################################ in-tunnel
log "3/5  In-tunnel benchmarks"

# Is the tunnel alive in this client namespace?
tunnel_ok() { ip netns exec "$1" ping -c 1 -W 3 10.8.0.1 >/dev/null 2>&1; }

# Check every client tunnel and report which, if any, are down. Called after
# each benchmark step: a step that breaks the tunnel used to leave every later
# measurement quietly wrong, with nothing in the output to say so.
CLIENT_NS_ALL=(vpn_ns2 vpn_ns3 vpn_ns4)
health() {   # health <label>; returns the number of live tunnels via TUNNELS_UP
    local label="$1" ns down=""
    TUNNELS_UP=0
    for ns in "${CLIENT_NS_ALL[@]}"; do
        if tunnel_ok "$ns"; then
            TUNNELS_UP=$((TUNNELS_UP + 1))
        else
            down="${down} ${ns}"
        fi
    done
    if [ -n "$down" ]; then
        warn "${label}: tunnel DOWN in${down} (${TUNNELS_UP}/3 live) -- measurements after this point are suspect"
        return 1
    fi
    return 0
}

for mode in "${MODES[@]}"; do
    echo
    echo "   ---------- ${mode} ----------"
    if [[ "$mode" == "pqc" ]]; then
        SRV_CFG="server/config-pqc.yaml"; CLI=(client/config-pqc.yaml client/config-pqc-ns3.yaml client/config-pqc-ns4.yaml)
    else
        SRV_CFG="server/config.yaml";     CLI=(client/config.yaml client/config-ns3.yaml client/config-ns4.yaml)
    fi

    setsid ip netns exec vpn_ns1 "$PY" "${PDIR}/server/vpn_server.py" \
        --config "$SRV_CFG" >"/tmp/pqvpn-bench-server-${mode}.log" 2>&1 &
    sleep 3
    for i in 0 1 2; do
        setsid ip netns exec "vpn_ns$((i+2))" "$PY" "${PDIR}/client/vpn_client.py" \
            --config "${CLI[$i]}" >"/tmp/pqvpn-bench-client$((i+1))-${mode}.log" 2>&1 &
        sleep 2
    done

    # Verify ALL THREE tunnels, not just the first. The scaling benchmark
    # drives all three, so checking only vpn_ns2 meant a dead second or third
    # client produced a multi-client "total" that was really a 1- or 2-client
    # measurement, with nothing to distinguish it from a genuine ceiling.
    health "${mode} startup" || true
    if [ "${TUNNELS_UP}" -eq 0 ]; then
        warn "${mode}: no tunnel came up, skipping in-tunnel benchmarks"
        warn "see /tmp/pqvpn-bench-server-${mode}.log"
        pkill -f "vpn_server.py|vpn_client.py" >/dev/null 2>&1 || true
        sleep 1
        continue
    fi
    [ "${TUNNELS_UP}" -eq 3 ] || warn "${mode}: only ${TUNNELS_UP}/3 tunnels up; multi-client results will be marked INCOMPLETE"

    # Warm-up, discarded. The first traffic through a fresh tunnel pays for
    # cold caches, CPU frequency ramp and slow-start on the TLS carrier. Since
    # the modes are measured one after the other, that cost lands entirely on
    # whichever mode runs first and reads as a difference between modes.
    echo "   warm-up (discarded)"
    ip netns exec vpn_ns2 ping -c 5 -i 0.2 -W 2 10.8.0.1 >/dev/null 2>&1 || true
    ip netns exec vpn_ns1 iperf3 -s -p 5399 -D >/dev/null 2>&1 || true
    sleep 1
    ip netns exec vpn_ns2 iperf3 -c 10.8.0.1 -p 5399 -t 5 >/dev/null 2>&1 || true
    ip netns exec vpn_ns1 pkill -f "iperf3 -s -p 5399" >/dev/null 2>&1 || true
    sleep 1

    ip netns exec vpn_ns2 bash "${BENCH}/run_latency_bench.sh" "$mode" 2>&1 | sed 's/^/   /'
    health "after latency" || true

    ip netns exec vpn_ns1 iperf3 -s -D >/dev/null 2>&1 || true
    sleep 1
    ip netns exec vpn_ns2 env PQVPN_TPUT_PHASE=main \
        bash "${BENCH}/run_throughput_bench.sh" "$mode" 2>&1 | sed 's/^/   /'
    ip netns exec vpn_ns1 pkill -f "iperf3 -s" 2>/dev/null || true
    health "after throughput" || true

    bash "${BENCH}/run_scaling_bench.sh" "$mode" 2>&1 | sed 's/^/   /'
    health "after scaling" || true

    ip netns exec vpn_ns2 bash "${BENCH}/run_file_transfer_bench.sh" "$mode" 2>&1 | sed 's/^/   /'
    health "after transfer" || true

    # The line-rate UDP flood runs LAST, on purpose. It is ~60% lost by design
    # and leaves the relay draining a full queue, so anything measured after it
    # is measured in its shadow. Running it here keeps the measurement without
    # letting it contaminate the rest of the mode.
    ip netns exec vpn_ns1 iperf3 -s -D >/dev/null 2>&1 || true
    sleep 1
    ip netns exec vpn_ns2 env PQVPN_TPUT_PHASE=flood \
        bash "${BENCH}/run_throughput_bench.sh" "$mode" 2>&1 | sed 's/^/   /'
    ip netns exec vpn_ns1 pkill -f "iperf3 -s" 2>/dev/null || true

    pkill -f "vpn_server.py|vpn_client.py" >/dev/null 2>&1 || true
    sleep 2
done

############################################################ plots
log "4/5  Regenerating plots"
"$PY" "${BENCH}/plot_results.py" --dir "$RESULTS" 2>&1 | sed 's/^/   /'

# Everything above ran as root, so every CSV and plot it wrote is root-owned.
# Left that way they cannot be regenerated, edited or cleaned up without sudo,
# and `git` sees a repo the user no longer owns. Hand them back.
if [ -n "${SUDO_UID:-}" ]; then
    chown -R "${SUDO_UID}:${SUDO_GID:-$SUDO_UID}" "${RESULTS}" 2>/dev/null || true
    echo "   results handed back to uid ${SUDO_UID}"
fi

############################################################ validate
log "5/5  Validating what was just measured"
# Every bad run this project has had was silent, so the sweep checks its own
# output before anyone reads it. Failures here mean the numbers must not be
# published until they are explained.
VALIDATE_ARGS=(--dir "$RESULTS"
               --reps "${PQVPN_SCALING_REPS}"
               --lat-reps "${PQVPN_LAT_REPS}"
               --tput-reps "${PQVPN_TPUT_REPS}"
               --hs-n "$((HS_REPS * HS_TRIALS))"
               --rs-n "$((RS_REPS * RS_TRIALS))"
               --transfer-reps "${PQVPN_BENCH_REPS:-3}")
[ "${PQVPN_TPUT_UDP_MAX}" = "1" ] || VALIDATE_ARGS+=(--no-flood)
[ -n "${PQVPN_BENCH_SIZES:-}" ] && VALIDATE_ARGS+=(--sizes "$("$PY" -c "
import sys
m = {'K': 1 << 10, 'M': 1 << 20, 'G': 1 << 30}
print(' '.join(str(int(t[:-1]) * m[t[-1].upper()]) if t[-1].upper() in m else t
               for t in sys.argv[1].split()))" "$PQVPN_BENCH_SIZES")")
"$PY" "${BENCH}/validate_results.py" "${VALIDATE_ARGS[@]}" 2>&1 | sed 's/^/   /'
VALIDATION_RC=${PIPESTATUS[0]}

DISK_AFTER="$(df --output=avail / | tail -1)"
log "Done"
echo "   CSVs:  ${RESULTS}"
echo "   plots: ${RESULTS}/plots"
printf '   disk free before/after: %s / %s KB' "$DISK_BEFORE" "$DISK_AFTER"
if [[ "$DISK_AFTER" -lt $((DISK_BEFORE - 102400)) ]]; then
    printf '  <- lost >100MB, something wrote to disk\n'
else
    printf '  (nothing written, as intended)\n'
fi

if [ "${VALIDATION_RC:-1}" -eq 0 ]; then
    printf '\n\033[1;32m   VALIDATION PASSED -- these numbers are safe to publish\033[0m\n'
else
    printf '\n\033[1;31m   VALIDATION FAILED -- see 5/5 above; do NOT publish until explained\033[0m\n'
fi
exit "${VALIDATION_RC:-1}"
