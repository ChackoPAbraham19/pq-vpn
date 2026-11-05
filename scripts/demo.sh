#!/usr/bin/env bash
set -euo pipefail
# demo.sh — Reproducible PQ-VPN demo
#
# Live path (default): builds the netns harness, starts server + client daemons,
# verifies the tunnel end-to-end, then tears everything down. Requires sudo.
#
#   sudo bash scripts/demo.sh                    # classical demo
#   sudo bash scripts/demo.sh --mode pqc         # PQC demo
#   sudo bash scripts/demo.sh --clients 2        # 2 concurrent clients (ns2+ns3)
#
# Dry-run path (no root, runs offline — CI-safe):
#   bash scripts/demo.sh --dry-run [--mode classical|pqc]
#
# Flags: --mode <classical|pqc>   (default classical)
#        --clients <1..3>         (default 1, max 3)
#        --dry-run                print the plan, run offline checks only
#        --keep                   don't teardown at the end (leave tunnel up)
#        --egress                 also curl a public IP-echo service from each
#                                 client THROUGH the tunnel (needs host internet)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PDIR="$(dirname "$SCRIPT_DIR")"
PY="${PDIR}/.venv/bin/python3"

MODE="classical"
NCLIENTS=1
DRY_RUN=0
KEEP=0
EGRESS=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode) MODE="$2"; shift 2 ;;
        --clients) NCLIENTS="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        --keep) KEEP=1; shift ;;
        --egress) EGRESS=1; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

if [[ "$MODE" != "classical" && "$MODE" != "pqc" ]]; then
    echo "ERROR: --mode must be classical or pqc" >&2; exit 2
fi
if [[ "$NCLIENTS" -lt 1 || "$NCLIENTS" -gt 3 ]]; then
    echo "ERROR: --clients must be 1..3" >&2; exit 2
fi

SERVER_CFG="${PDIR}/server/config.yaml"
CLIENT_CFGS=( "${PDIR}/client/config.yaml"
              "${PDIR}/client/config-ns3.yaml"
              "${PDIR}/client/config-ns4.yaml" )
if [[ "$MODE" == "pqc" ]]; then
    SERVER_CFG="${PDIR}/server/config-pqc.yaml"
    CLIENT_CFGS=( "${PDIR}/client/config-pqc.yaml"
                  "${PDIR}/client/config-pqc-ns3.yaml"
                  "${PDIR}/client/config-pqc-ns4.yaml" )
fi

log()  { echo -e "\e[1;34m[$(date +%H:%M:%S)]\e[0m $*"; }
fail() { echo -e "\e[1;31mFAIL\e[0m $*" >&2; }

check_offline() {
    local rc=0
    log "Offline sanity checks..."
    for f in "$SERVER_CFG" "${CLIENT_CFGS[@]:0:$NCLIENTS}"; do
        [[ -f "$f" ]] || { fail "missing config: $f"; rc=1; }
    done
    [[ -d "$PDIR/pki/certs/$MODE" ]] || { fail "PKI not generated for $MODE (run pki/gen_*_ca.sh)"; rc=1; }
    # Per-client certs for the extra namespaces (configs carry client_name: ns3/ns4).
    if [[ -f "$PDIR/pki/certs/$MODE/ca.key" ]]; then
        for n in ns3 ns4; do
            [[ -f "$PDIR/pki/certs/$MODE/clients/$n.crt" ]] \
                || bash "$PDIR/pki/issue_client.sh" "$MODE" "$n" >/dev/null
        done
    fi
    if [[ "$MODE" == "pqc" ]]; then
        [[ -x /opt/openssl-pqc/bin/openssl ]] || { fail "missing /opt/openssl-pqc (run scripts/install_custom_openssl.sh)"; rc=1; }
    fi
    [[ -x "$PY" ]] || { fail "missing venv python ($PY) — run python3 -m venv .venv && pip install -r requirements.txt"; rc=1; }
    return $rc
}

plan() {
    echo ""
    echo "══════════════════════════════════════════════════════════════"
    echo "  PQ-VPN DEMO"
    echo "    mode:      ${MODE}"
    echo "    clients:   ${NCLIENTS}"
    echo "    server:    ${SERVER_CFG}"
    echo "    clients:   ${CLIENT_CFGS[@]:0:$NCLIENTS}"
    echo "  Steps:"
    echo "    1. setup_routing.sh (netns harness: vpn_ns1 server, ns2..ns4 clients)"
    echo "    2. start vpn_server.py in vpn_ns1"
    echo "    3. start vpn_client.py in each client ns"
    echo "    4. ping 10.8.0.1 through the tunnel from each client"
    echo "    5. vpnctl status (session state + resumed flag)"
    echo "    6. optional: iperf3 throughput, dashboard"
    echo "══════════════════════════════════════════════════════════════"
    echo ""
}

# ---------------------------------------------------------------- dry-run
if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "== PQ-VPN demo (dry-run) =="
    check_offline || exit 1
    plan
    log "Offline unit tests (framing, net-config, keepalive, resumption, dashboard):"
    fail=0
    for t in "${PDIR}/common/test_common.py" "${PDIR}/common/test_net_config.py" \
             "${PDIR}/common/test_keepalive.py" "${PDIR}/common/test_resumption.py" \
             "${PDIR}/dashboard/test_dashboard.py"; do
        # `|| rc=$?`: under set -e a failing suite must be REPORTED, not
        # silently abort the whole dry-run (which is what a clean-build
        # container showed when one test failed).
        rc=0; out=$("$PY" "$t" 2>&1) || rc=$?
        last=$(echo "$out" | tail -1 | sed 's/^ *//')
        echo "    $(basename "$t"): ${last} (rc=$rc)"
        # On failure, print the checks that failed. Without this the only
        # evidence a CI container leaves behind is "57/58 passed", which names
        # neither the check nor its detail line and cannot be acted on.
        if [[ $rc -ne 0 ]]; then
            fail=1
            echo "$out" | grep -A1 '✗' | sed 's/^/      /'
        fi
    done
    [[ $fail -ne 0 ]] && { echo "    SOME TESTS FAILED" >&2; exit 1; }
    echo ""
    log "Real crypto sanity: handshake benchmark (5 trials, both modes)"
    TMPBENCH="$(mktemp -d)"
    "$PY" "${PDIR}/benchmark/run_handshake_bench.py" --out-dir "$TMPBENCH" \
        --modes classical pqc --trials 5 2>&1 \
        | sed 's/^/    /' | { grep -E "classical|pqc|no successful|error|Error" || true; }
    rm -rf "$TMPBENCH"
    echo ""
    log "Demo command to run live (needs sudo):"
    echo "    sudo bash scripts/demo.sh --mode ${MODE} --clients ${NCLIENTS}"
    exit 0
fi

# ---------------------------------------------------------------- live
if [[ "$(id -u)" -ne 0 ]]; then
    echo "ERROR: live demo requires sudo (use --dry-run for a rootless check)" >&2
    exit 1
fi

check_offline || exit 1

cleanup() {
    if [[ "$KEEP" -eq 1 ]]; then
        log "--keep set, leaving tunnel + namespaces up"
        return
    fi
    log "Tearing down..."
    # Kill exactly the daemons this demo started; fall back to a pattern sweep
    # in case a PID was reaped early or the variable list is empty.
    if [[ -n "${SERVER_PID:-}" ]] ; then kill "${SERVER_PID}" 2>/dev/null || true; fi
    for pid in "${CLIENT_PIDS[@]:-}"; do
        [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
    done
    pkill -f "vpn_server.py|vpn_client.py" >/dev/null 2>&1 || true
    bash "${SCRIPT_DIR}/setup_routing.sh" --teardown >/dev/null 2>&1 || true
}
trap cleanup EXIT SIGINT SIGTERM

plan
cd "$PDIR"

# netns harness has no systemd-resolved and a write-blocked /etc/resolv.conf;
# probing DNS on every client is guaranteed noise. VPS/production runs leave
# this unset and keep full DNS management.
export PQVPN_SKIP_DNS=1
# Daemon stdout is redirected to /tmp/pqvpn-demo-*.log; without this Python
# block-buffers it and the logs stay empty until the daemon exits.
export PYTHONUNBUFFERED=1

PING_FAILS=0

log "Step 1/5: building netns harness"
# Refuse to start over ghost daemons — they hold stale IPC sockets and honestly
# report nothing. Teardown kills them, but only if it ran.
if pgrep -f "vpn_server.py|vpn_client.py" >/dev/null 2>&1; then
    fail "stale pqvpn daemons still running. Kill first:"
    echo "  sudo pkill -f 'vpn_server.py|vpn_client.py'; sudo bash scripts/setup_routing.sh --teardown"
    exit 1
fi
bash "${SCRIPT_DIR}/setup_routing.sh"

log "Step 2/5: starting server (${MODE}) in vpn_ns1"
# setsid detaches daemons into their own sessions: Ctrl-C or closing the
# launcher terminal must never propagate to --keep tunnels.
setsid ip netns exec vpn_ns1 "$PY" "${PDIR}/server/vpn_server.py" --config "$SERVER_CFG" >/tmp/pqvpn-demo-server.log 2>&1 &
SERVER_PID=$!
sleep 3

# Fail fast: if the server died at startup (bad NAT rules, port conflict, ...),
# every client would just retry forever. Abort now instead.
if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    fail "server daemon exited during startup — aborting (see server output above)"
    exit 1
fi
SERVER_ALIVE_CHECK() { kill -0 "$SERVER_PID" 2>/dev/null; }

log "Step 3/5: starting $NCLIENTS client(s)"
CLIENT_PIDS=()
for ((i=0; i<NCLIENTS; i++)); do
    ns="vpn_ns$((i+2))"
    cfg="${CLIENT_CFGS[$i]}"
    log "  client $((i+1)): ${ns} with $(basename "$cfg")"
    # setsid detaches each client daemon too
    setsid ip netns exec "$ns" "$PY" "${PDIR}/client/vpn_client.py" --config "$cfg" >"/tmp/pqvpn-demo-client$((i+1)).log" 2>&1 &
    CLIENT_PIDS+=($!)
    sleep 2
done

log "Step 4/5: verifying tunnel from each client"
if ! SERVER_ALIVE_CHECK; then
    fail "server daemon died after startup — aborting (see server output above)"
    exit 1
fi
for ((i=0; i<NCLIENTS; i++)); do
    ns="vpn_ns$((i+2))"
    log "  ping 10.8.0.1 from ${ns}:"
    if ip netns exec "$ns" ping -c 3 -W 2 10.8.0.1 > /tmp/pqvpn-demo-ping.$$ 2>&1; then
        tail -2 /tmp/pqvpn-demo-ping.$$ | sed 's/^/    /'
    else
        fail "ping from ${ns} did not fully succeed (demo continues)"
        tail -4 /tmp/pqvpn-demo-ping.$$ | sed 's/^/    /'
        PING_FAILS=$((PING_FAILS+1))
    fi
    rm -f /tmp/pqvpn-demo-ping.$$
done

if [[ "$EGRESS" -eq 1 ]]; then
    log "Egress: public IP as seen from each client, THROUGH the tunnel"
    HOST_PUB="$(curl -s --max-time 8 https://ifconfig.me || echo '?')"
    echo "    host itself: ${HOST_PUB}"
    for ((i=0; i<NCLIENTS; i++)); do
        ns="vpn_ns$((i+2))"
        # Clients skip DNS management in the harness, so resolve on the host and
        # curl by IP — the request itself still goes tun0 -> TLS -> server -> NAT.
        ECHO_IP="$(getent ahostsv4 ifconfig.me | awk 'NR==1{print $1}')"
        got="$(ip netns exec "$ns" curl -s --max-time 10 -H 'Host: ifconfig.me' "http://${ECHO_IP}/ip" || echo 'FAILED')"
        if [[ "$got" == "$HOST_PUB" && -n "$got" ]]; then
            echo "    ${ns}: ${got}  (matches host: traffic left via the tunnel + NAT)"
        else
            fail "${ns}: got '${got}' (expected ${HOST_PUB})"
        fi
    done
fi

log "Step 5/5: vpnctl status"
sleep 1
"$PY" "${PDIR}/cli/vpnctl.py" status 2>&1 | sed 's/^/    /' || true

log "Optional: throughput via iperf3"
if SERVER_ALIVE_CHECK && command -v iperf3 >/dev/null 2>&1; then
    ip netns exec vpn_ns1 iperf3 -s -D >/dev/null 2>&1 || true
    sleep 1
    for ((i=0; i<NCLIENTS; i++)); do
        ns="vpn_ns$((i+2))"
        log "  iperf3 from ${ns} (10s):"
        ip netns exec "$ns" iperf3 -c 10.8.0.1 -t 10 -f m 2>/dev/null \
            | { grep -E "sender|receiver" || true; } | sed 's/^/    /' || true
    done
    # Leave iperf3 server running for manual testing; will be reaped by teardown
else
    if ! SERVER_ALIVE_CHECK; then
        log "  server dead — skipping iperf3"
    else
        log "  iperf3 not installed — skipping"
    fi
fi

if [[ "$PING_FAILS" -gt 0 ]]; then
    fail "${PING_FAILS}/${NCLIENTS} client pings failed — inspect output above"
else
    log "Demo complete."
fi
if [[ "$KEEP" -eq 0 ]]; then
    log "Cleaning up on exit..."
fi
