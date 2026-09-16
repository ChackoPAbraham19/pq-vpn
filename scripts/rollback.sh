#!/usr/bin/env bash
set -euo pipefail
# rollback.sh — Fully revert all system changes made by pq-vpn setup.
# This removes: liboqs, oqs-provider, leftover namespaces, DNS backup.
# It does NOT remove installed packages (build-essential, cmake, etc.)
# or Python dependencies.
#
# Usage: sudo bash scripts/rollback.sh

echo "=== PQ-VPN Rollback ==="

# 1. Clean up any leftover network namespaces
echo "[1/5] Cleaning network namespaces..."
ip netns del vpn_ns1 2>/dev/null || true
ip netns del vpn_ns2 2>/dev/null || true
ip link del veth-h 2>/dev/null || true

# 2. Restore DNS if backup exists
echo "[2/5] Restoring DNS..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
bash "${SCRIPT_DIR}/backup_restore_dns.sh" restore 2>/dev/null || true

# 3. Remove oqsprovider module
echo "[3/5] Removing oqsprovider module..."
rm -f /usr/lib/x86_64-linux-gnu/ossl-modules/oqsprovider.so 2>/dev/null || true
rm -f /usr/local/lib/ossl-modules/oqsprovider.so 2>/dev/null || true

# 4. Remove liboqs (legacy prefix) and the custom OpenSSL stack
echo "[4/5] Removing liboqs + /opt/openssl-pqc..."
rm -rf /opt/liboqs 2>/dev/null || true
rm -rf /opt/openssl-pqc 2>/dev/null || true
rm -f /etc/ld.so.conf.d/openssl-pqc.conf 2>/dev/null || true
ldconfig 2>/dev/null || true

# 5. Remove build sources
echo "[5/5] Removing build sources..."
rm -rf /tmp/liboqs_src 2>/dev/null || true
rm -rf /tmp/oqs_provider_src /tmp/openssl_src /tmp/pqc_build 2>/dev/null || true
rm -f /tmp/pqvpn.resolv.conf.bak 2>/dev/null || true
rm -f /tmp/pqvpn.pid /tmp/pqvpn.sock /tmp/pqvpn.state 2>/dev/null || true

echo ""
echo "=== Rollback complete ==="
echo "System changes reverted. Project files remain in:"
echo "  $(dirname "$SCRIPT_DIR")"
echo ""
echo "To also remove project files: rm -rf $(dirname "$SCRIPT_DIR")"
