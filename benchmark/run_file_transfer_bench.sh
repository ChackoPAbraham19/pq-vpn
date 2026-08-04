#!/usr/bin/env bash
set -euo pipefail
# run_file_transfer_bench.sh — Measure file transfer time through tunnel
#
# Prerequisites: VPN tunnel is up, vpn_ns1 and vpn_ns2 namespaces exist
# Usage: sudo ip netns exec vpn_ns2 bash benchmark/run_file_transfer_bench.sh <mode>

MODE="${1:-unknown}"
RESULTS_DIR="$(dirname "$0")/results"
SERVER_NETNS="vpn_ns1"
SERVER_IP="10.8.0.1"
SIZE_MB=100
TMP_DIR="/tmp/pqvpn_bench"
PORT=9998

mkdir -p "${RESULTS_DIR}"
sudo ip netns exec "${SERVER_NETNS}" mkdir -p "${TMP_DIR}"

CSV="${RESULTS_DIR}/file_transfer_${MODE}.csv"

# Generate random file
SRC="${TMP_DIR}/transfer_${SIZE_MB}mb.dat"
DST="${TMP_DIR}/transfer_${SIZE_MB}mb.out"

echo "=== File transfer benchmark: ${MODE} (${SIZE_MB}MB) ==="

sudo ip netns exec "${SERVER_NETNS}" dd if=/dev/urandom of="${SRC}" bs=1M count="${SIZE_MB}" 2>/dev/null
src_sha256=$(sudo ip netns exec "${SERVER_NETNS}" sha256sum "${SRC}" | cut -d' ' -f1)

# Start HTTP server inside the server network namespace
sudo ip netns exec "${SERVER_NETNS}" bash -c "cd '${TMP_DIR}' && python3 -m http.server ${PORT} --bind 0.0.0.0" >/dev/null 2>&1 &
HTTP_PID=$!
sleep 1

start=$(date +%s.%N)
python3 -c "
import urllib.request
url = f'http://${SERVER_IP}:${PORT}/transfer_${SIZE_MB}mb.dat'
with urllib.request.urlopen(url, timeout=60) as r:
    with open('${DST}', 'wb') as f:
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            f.write(chunk)
"
end=$(date +%s.%N)
elapsed=$(python3 -c "print(round($end - $start, 2))")

sudo ip netns exec "${SERVER_NETNS}" pkill -f "python3 -m http.server ${PORT}" 2>/dev/null || true
wait 2>/dev/null || true

# Verify
dst_sha256=$(sha256sum "${DST}" 2>/dev/null | cut -d' ' -f1 || echo "FAIL")
if [ "${src_sha256}" = "${dst_sha256}" ]; then
    sha_ok="yes"
    echo "  SHA256: match"
else
    sha_ok="no"
    echo "  SHA256: MISMATCH (src=${src_sha256} dst=${dst_sha256})" >&2
fi

echo "mode,size_mb,seconds,sha256_ok" > "${CSV}"
echo "${MODE},${SIZE_MB},${elapsed},${sha_ok}" >> "${CSV}"
echo "  ${SIZE_MB}MB in ${elapsed}s, sha256_ok=${sha_ok}"
echo "  -> ${CSV}"

sudo ip netns exec "${SERVER_NETNS}" rm -f "${SRC}" 2>/dev/null || true
rm -f "${DST}"
