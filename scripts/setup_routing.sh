#!/usr/bin/env bash
set -euo pipefail
# setup_routing.sh — Network namespace test harness for pq-vpn
#
# Creates two Linux network namespaces (vpn_ns1=server, vpn_ns2=client)
# bridged by a veth pair. Sets up TUN interfaces, routing, and NAT.
#
# ┌──────────┐   veth pair   ┌──────────┐
# │ vpn_ns1  │◄─────────────►│ vpn_ns2  │
# │ (server) │ 192.168.100.x │ (client) │
# │ tun0     │               │ tun0     │
# │10.8.0.1  │               │10.8.0.2  │
# │ NAT+forward              │          │
# └──────────┘               └──────────┘
#
# Usage: sudo bash scripts/setup_routing.sh
# Teardown: sudo bash scripts/setup_routing.sh --teardown
#
# On setup, backs up /etc/resolv.conf (defensive — this script does not
# modify it, but the backup provides a safety net). On teardown, restores it.
# Uses trap to auto-clean on script exit, SIGINT, or SIGTERM.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_RESTORE="${SCRIPT_DIR}/backup_restore_dns.sh"

MODE="${1:-setup}"
NS1="vpn_ns1"
NS2="vpn_ns2"
VETH_HOST="veth-h"
VETH_NS1="veth-ns1"
VETH_HOST2="veth-h2"
VETH_NS2="veth-ns2"
VETH_PEER1="veth-p1"
VETH_PEER2="veth-p2"
IP_HOST="192.168.100.1"
IP_NS1="192.168.100.2"
IP_HOST2="192.168.200.1"
IP_NS2="192.168.200.2"
IP_PEER1="192.168.10.1"
IP_PEER2="192.168.10.2"
TUN_NS1="10.8.0.1"
TUN_NS2="10.8.0.2"
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
    iptables -D FORWARD -i "${VETH_HOST}" -o "${VETH_HOST2}" -j ACCEPT 2>/dev/null || true
    iptables -D FORWARD -i "${VETH_HOST2}" -o "${VETH_HOST}" -j ACCEPT 2>/dev/null || true
    ip link del "${VETH_PEER1}" 2>/dev/null || true
    ip netns del "${NS1}" 2>/dev/null || true
    ip netns del "${NS2}" 2>/dev/null || true
    ip link del "${VETH_HOST}" 2>/dev/null || true
    ip link del "${VETH_HOST2}" 2>/dev/null || true
    echo "Cleanup done."
}

if [ "${MODE}" = "--teardown" ]; then
    cleanup
    exit 0
fi

# Stale namespace check — refuse to run if leftovers exist
if ip netns list | grep -qE "^(vpn_ns1|vpn_ns2)(\s|$)"; then
    echo "ERROR: Stale namespaces detected. Run teardown first:"
    echo "  sudo bash $(basename "$0") --teardown"
    exit 1
fi

# Trap auto-cleanup on premature exit (not on successful completion)
trap cleanup EXIT SIGINT SIGTERM

# Backup DNS before any network changes (defensive safety net)
bash "${BACKUP_RESTORE}" backup 2>/dev/null || true

echo "=== Creating network namespaces ==="
ip netns add "${NS1}"
ip netns add "${NS2}"

echo "=== Creating veth pair (host <-> ns1) ==="
ip link add "${VETH_HOST}" type veth peer name "${VETH_NS1}"
ip link set "${VETH_NS1}" netns "${NS1}"

echo "=== Configuring veth addresses ==="
ip addr add "${IP_HOST}/24" dev "${VETH_HOST}"
ip link set "${VETH_HOST}" up
ip netns exec "${NS1}" ip addr add "${IP_NS1}/24" dev "${VETH_NS1}"
ip netns exec "${NS1}" ip link set "${VETH_NS1}" up
ip netns exec "${NS1}" ip link set lo up
ip netns exec "${NS2}" ip link set lo up

echo "=== Configuring routing ==="
# Host routes to reach ns1's TUN subnet via veth
ip route add "${TUN_NS1}/24" via "${IP_NS1}" dev "${VETH_HOST}" 2>/dev/null || true
# ns1 default route via veth (to reach internet if needed)
ip netns exec "${NS1}" ip route add default via "${IP_HOST}" dev "${VETH_NS1}" 2>/dev/null || true

echo "=== Creating TUN interfaces ==="
ip netns exec "${NS1}" ip tuntap add dev tun0 mode tun 2>/dev/null || true
ip netns exec "${NS1}" ip addr add "${TUN_NS1}/${TUN_MASK}" dev tun0
ip netns exec "${NS1}" ip link set tun0 mtu 1400 up
ip netns exec "${NS1}" ip route add "${TUN_NS2}/32" dev tun0 2>/dev/null || true

ip netns exec "${NS2}" ip tuntap add dev tun0 mode tun 2>/dev/null || true
ip netns exec "${NS2}" ip addr add "${TUN_NS2}/${TUN_MASK}" dev tun0
ip netns exec "${NS2}" ip link set tun0 mtu 1400 up
ip netns exec "${NS2}" ip route add "${TUN_NS1}/32" dev tun0 2>/dev/null || true

echo "=== Configuring veth for vpn_ns2 ==="
ip link add "${VETH_HOST2}" type veth peer name "${VETH_NS2}"
ip link set "${VETH_NS2}" netns "${NS2}"
ip addr add "${IP_HOST2}/24" dev "${VETH_HOST2}"
ip link set "${VETH_HOST2}" up
ip netns exec "${NS2}" ip addr add "${IP_NS2}/24" dev "${VETH_NS2}"
ip netns exec "${NS2}" ip link set "${VETH_NS2}" up
ip netns exec "${NS2}" ip route add default via "${IP_HOST2}" dev "${VETH_NS2}" 2>/dev/null || true

echo "=== Configuring direct veth peer (ns1 <-> ns2) ==="
ip link add "${VETH_PEER1}" type veth peer name "${VETH_PEER2}"
ip link set "${VETH_PEER1}" netns "${NS1}"
ip link set "${VETH_PEER2}" netns "${NS2}"
ip netns exec "${NS1}" ip addr add "${IP_PEER1}/24" dev "${VETH_PEER1}"
ip netns exec "${NS1}" ip link set "${VETH_PEER1}" up
ip netns exec "${NS2}" ip addr add "${IP_PEER2}/24" dev "${VETH_PEER2}"
ip netns exec "${NS2}" ip link set "${VETH_PEER2}" up

echo "=== Enabling IP forwarding and NAT in vpn_ns1 ==="
ip netns exec "${NS1}" sysctl -w net.ipv4.ip_forward=1
ip netns exec "${NS1}" iptables -t nat -A POSTROUTING -s "${TUN_NS1}/24" -o "${VETH_NS1}" -j MASQUERADE 2>/dev/null || true
ip netns exec "${NS1}" iptables -A FORWARD -i tun0 -o "${VETH_NS1}" -j ACCEPT 2>/dev/null || true
ip netns exec "${NS1}" iptables -A FORWARD -i "${VETH_NS1}" -o tun0 -j ACCEPT 2>/dev/null || true

echo "=== Enabling IP forwarding on host ==="
sysctl -w net.ipv4.ip_forward=1 > /dev/null
sysctl -w net.ipv4.conf.all.rp_filter=0 > /dev/null
sysctl -w net.ipv4.conf.default.rp_filter=0 > /dev/null
sysctl -w net.ipv4.conf."${VETH_HOST}".rp_filter=0 > /dev/null
sysctl -w net.ipv4.conf."${VETH_HOST2}".rp_filter=0 > /dev/null
iptables -A FORWARD -i "${VETH_HOST}" -o "${VETH_HOST2}" -j ACCEPT 2>/dev/null || true
iptables -A FORWARD -i "${VETH_HOST2}" -o "${VETH_HOST}" -j ACCEPT 2>/dev/null || true

# Success — disable the trap so cleanup() doesn't run on exit
trap - EXIT SIGINT SIGTERM

echo "=== Setup complete ==="
echo ""
echo "Namespaces:"
echo "  vpn_ns1 (server) — tun0=${TUN_NS1}/${TUN_MASK}, veth=${IP_NS1}"
echo "  vpn_ns2 (client) — tun0=${TUN_NS2}/${TUN_MASK}, veth=${IP_NS2}"
echo ""
echo "Test connectivity:"
echo "  sudo ip netns exec vpn_ns2 ping ${IP_NS1}  # veth via host forwarding"
echo ""
echo "Teardown:  sudo bash scripts/setup_routing.sh --teardown"
