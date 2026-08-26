#!/usr/bin/env bash
set -euo pipefail
# setup_routing.sh — Network namespace test harness for pq-vpn (multi-client)
#
# Creates four namespaces: vpn_ns1 (server) and vpn_ns2/ns3/ns4 (up to 3 clients),
# each bridged to the host by its own veth pair. The server namespaces bridged
# directly server<->client peers so tunnels can ship packets without host routing.
#
# ┌─────────────┐
# │    host     │  veth-h2   veth-h3   veth-h4
# │ 192.168.100.1 │──────────►│
# ... client nss reach the server via the host → server veth path
#
# Usage: sudo bash scripts/setup_routing.sh
# Teardown: sudo bash scripts/setup_routing.sh --teardown

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_RESTORE="${SCRIPT_DIR}/backup_restore_dns.sh"

MODE="${1:-setup}"
NS1="vpn_ns1"
NS2="vpn_ns2"
NS3="vpn_ns3"
NS4="vpn_ns4"
NS_ALL=(vpn_ns1 vpn_ns2 vpn_ns3 vpn_ns4)

# Server ns ⇄ host
VETH_HOST="veth-h"
VETH_NS1="veth-ns1"
IP_HOST="192.168.100.1"
IP_NS1="192.168.100.2"

# Client ns ⇄ host (each client on its own host veth)
# client index: 2,3,4 → 200,210,220
VETH_HOST2="veth-h2"; VETH_NS2="veth-ns2"; IP_HOST2="192.168.200.1"; IP_NS2="192.168.200.2"
VETH_HOST3="veth-h3"; VETH_NS3="veth-ns3"; IP_HOST3="192.168.210.1"; IP_NS3="192.168.210.2"
VETH_HOST4="veth-h4"; VETH_NS4="veth-ns4"; IP_HOST4="192.168.220.1"; IP_NS4="192.168.220.2"

TUN_MASK="24"

CLEANUP_RAN=0

cleanup() {
    if [ "${CLEANUP_RAN}" -eq 1 ]; then
        return
    fi
    CLEANUP_RAN=1
    echo ""
    echo "=== Cleaning up ==="
    bash "${BACKUP_RESTORE}" restore 2>/dev/null || true
    for ns in "${NS_ALL[@]}"; do
        ip netns del "${ns}" 2>/dev/null || true
    done
    for v in "${VETH_HOST}" "${VETH_HOST2}" "${VETH_HOST3}" "${VETH_HOST4}"; do
        ip link del "${v}" 2>/dev/null || true
    done
    echo "Cleanup done."
}

if [ "${MODE}" = "--teardown" ]; then
    cleanup
    exit 0
fi

# Stale namespace check — refuse to run if leftovers exist
if ip netns list | grep -qE "^vpn_ns[1-4](\s|$)"; then
    echo "ERROR: Stale namespaces detected. Run teardown first:"
    echo "  sudo bash $(basename "$0") --teardown"
    exit 1
fi

trap cleanup EXIT SIGINT SIGTERM
bash "${BACKUP_RESTORE}" backup 2>/dev/null || true

echo "=== Creating namespaces ==="
for ns in "${NS_ALL[@]}"; do
    ip netns add "${ns}"
done

echo "=== Server ns (vpn_ns1) veth ⇄ host ==="
ip link add "${VETH_HOST}" type veth peer name "${VETH_NS1}"
ip link set "${VETH_NS1}" netns "${NS1}"
ip addr add "${IP_HOST}/24" dev "${VETH_HOST}"
ip link set "${VETH_HOST}" up
ip netns exec "${NS1}" ip addr add "${IP_NS1}/24" dev "${VETH_NS1}"
ip netns exec "${NS1}" ip link set "${VETH_NS1}" up
ip netns exec "${NS1}" ip link set lo up

echo "=== Client nss veth ⇄ host ==="
# each client: host veth gateway <client-subnet>.1, ns address .2
declare -A C_NS=( [2]="${VETH_NS2}:${IP_HOST2}:${IP_NS2}:${VETH_HOST2}:192.168.200.0/24" \
                  [3]="${VETH_NS3}:${IP_HOST3}:${IP_NS3}:${VETH_HOST3}:192.168.210.0/24" \
                  [4]="${VETH_NS4}:${IP_HOST4}:${IP_NS4}:${VETH_HOST4}:192.168.220.0/24" )
for i in 2 3 4; do
    IFS=':' read -r veth_ns host_ip ns_ip host_veth cidr <<< "${C_NS[$i]}"
    ns="vpn_ns${i}"
    ip link add "${host_veth}" type veth peer name "${veth_ns}"
    ip link set "${veth_ns}" netns "${ns}"
    ip addr add "${host_ip}/24" dev "${host_veth}"
    ip link set "${host_veth}" up
    ip netns exec "${ns}" ip addr add "${ns_ip}/24" dev "${veth_ns}"
    ip netns exec "${ns}" ip link set "${veth_ns}" up
    ip netns exec "${ns}" ip link set lo up
    ip netns exec "${ns}" ip route add default via "${host_ip}" dev "${veth_ns}" 2>/dev/null || true
done

echo "=== Server route + forwarding setup ==="
# Server default route out the host veth (reachable IP for clients on 192.168.100/24)
ip netns exec "${NS1}" ip route add default via "${IP_HOST}" dev "${VETH_NS1}" 2>/dev/null || true

for ns in "${NS_ALL[@]}"; do
    ip netns exec "${ns}" sysctl -w net.ipv4.ip_forward=1 > /dev/null
done
sysctl -w net.ipv4.ip_forward=1 > /dev/null
sysctl -w net.ipv4.conf.all.rp_filter=0 > /dev/null
sysctl -w net.ipv4.conf.default.rp_filter=0 > /dev/null

# Host forwards between server veth and every client veth
for host_veth in "${VETH_HOST}" "${VETH_HOST2}" "${VETH_HOST3}" "${VETH_HOST4}"; do
    for peer in "${VETH_HOST}" "${VETH_HOST2}" "${VETH_HOST3}" "${VETH_HOST4}"; do
        [ "${host_veth}" = "${peer}" ] && continue
        iptables -A FORWARD -i "${host_veth}" -o "${peer}" -j ACCEPT 2>/dev/null || true
    done
done

# Success — disable the trap so cleanup() doesn't run on exit
trap - EXIT SIGINT SIGTERM

echo "=== Setup complete ==="
echo ""
echo "Namespaces:"
echo "  vpn_ns1 (server) — 192.168.100.2 via ${VETH_NS1}"
echo "  vpn_ns2 (client1)— 192.168.200.2 via ${VETH_NS2}, gateway ${IP_HOST2}"
echo "  vpn_ns3 (client2)— 192.168.210.2 via ${VETH_NS3}, gateway ${IP_HOST3}"
echo "  vpn_ns4 (client3)— 192.168.220.2 via ${VETH_NS4}, gateway ${IP_HOST4}"
echo ""
echo "Test connectivity (from a client ns):"
echo "  sudo ip netns exec vpn_ns2 ping 192.168.100.2"
echo ""
echo "Teardown:  sudo bash scripts/setup_routing.sh --teardown"