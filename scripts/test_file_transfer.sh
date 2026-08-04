#!/usr/bin/env bash
set -euo pipefail
# test_file_transfer.sh – Verify file transfer through PQ-VPN tunnel
#
# Prerequisites:
#   sudo bash scripts/setup_routing.sh
#   Server daemon running:  sudo ip netns exec vpn_ns1 python3 server/vpn_server.py
#   Client daemon running:  sudo ip netns exec vpn_ns2 python3 client/vpn_client.py
#
# Usage:  sudo bash scripts/test_file_transfer.sh
#
# Runs two transfers:
#   1. Small TCP transfer (1 KB) via HTTP — validates tunnel passes TCP
#   2. Bulk UDP transfer (1 MB) via netcat — validates bulk throughput

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

TUN_ADDR_SERVER="10.8.0.1"
HTTP_PORT=8000
UDP_PORT=9999
FILE_BIG_SZ_MB=1

cleanup() {
    echo ""
    echo "=== Cleaning up ==="
    pkill -f "python3 -m http.server $HTTP_PORT" 2>/dev/null || true
    pkill -f "nc -u -w 30 -q 0 -l -p $UDP_PORT" 2>/dev/null || true
    echo "Cleanup done."
}
trap cleanup EXIT SIGINT SIGTERM

echo "=== PQ-VPN File Transfer Tests ==="
echo ""
ALL_OK=0

# ── Phase 1: Small TCP transfer (1 KB) ──
echo "--- Phase 1: TCP transfer (1 KB) ---"

FILE_SMALL="/tmp/testfile_small.bin"
FILE_SMALL_OUT="/tmp/testfile_small.out"

sudo ip netns exec vpn_ns1 dd if=/dev/urandom bs=1024 count=1 of="$FILE_SMALL" status=none 2>/dev/null
SMALL_HASH=$(sudo ip netns exec vpn_ns1 sha256sum "$FILE_SMALL" | awk '{print $1}')
echo "Created 1 KB file, sha256: $SMALL_HASH"

sudo ip netns exec vpn_ns1 bash -c 'cd /tmp && python3 -m http.server $HTTP_PORT --bind 0.0.0.0' &>/dev/null &
HTTP_PID=$!
sleep 2
echo "HTTP server started (pid=$HTTP_PID)"

echo "Downloading through tunnel..."
sudo ip netns exec vpn_ns2 curl -s --connect-timeout 15 -o "$FILE_SMALL_OUT" \
    "http://${TUN_ADDR_SERVER}:${HTTP_PORT}/testfile_small.bin"
SMALL_OUT_HASH=$(sudo ip netns exec vpn_ns2 sha256sum "$FILE_SMALL_OUT" | awk '{print $1}')

if [ "$SMALL_HASH" = "$SMALL_OUT_HASH" ]; then
    echo "  ✅ TCP transfer OK"
else
    echo "  ❌ TCP transfer FAILED — hashes differ"
    echo "     src: $SMALL_HASH"
    echo "     dst: $SMALL_OUT_HASH"
    ALL_OK=1
fi

kill $HTTP_PID 2>/dev/null || true
wait $HTTP_PID 2>/dev/null || true

if [ "$ALL_OK" -ne 0 ]; then
    exit 1
fi
echo ""

# ── Phase 2: Bulk UDP transfer (1 MB) ──
echo "--- Phase 2: UDP transfer (${FILE_BIG_SZ_MB} MB) ---"

FILE_BIG="/tmp/testfile_big.bin"
FILE_BIG_OUT="/tmp/testfile_big.out"

sudo ip netns exec vpn_ns1 dd if=/dev/urandom bs=1M count="$FILE_BIG_SZ_MB" of="$FILE_BIG" status=none 2>/dev/null
BIG_HASH=$(sudo ip netns exec vpn_ns1 sha256sum "$FILE_BIG" | awk '{print $1}')
echo "Created ${FILE_BIG_SZ_MB} MB file, sha256: $BIG_HASH"

# Build a small Python-based UDP file server that chunks the file into 1400-byte datagrams
# and a receiver that reassembles them, avoiding netcat's UDP limitations.

# Server: python UDP chunked sender
# Client: python UDP chunked receiver

cat > /tmp/pqvpn_udp_sender.py << 'PYEOF'
import socket, sys, time, struct
FILE = sys.argv[1]
ADDR = (sys.argv[2], int(sys.argv[3]))
CHUNK = 1400
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

with open(FILE, 'rb') as f:
    data = f.read()
total_chunks = (len(data) + CHUNK - 1) // CHUNK
print(f'Sending {len(data)} bytes in {total_chunks} chunks to {ADDR}', flush=True)

sock.sendto(struct.pack('!I', len(data)), ADDR)   # file length header
time.sleep(0.1)
sock.sendto(struct.pack('!I', total_chunks), ADDR) # chunk count header
time.sleep(0.1)
for i in range(total_chunks):
    chunk = data[i*CHUNK:(i+1)*CHUNK]
    pkt = struct.pack('!I', i) + chunk
    sock.sendto(pkt, ADDR)
    time.sleep(0.001)
print('Done sending', flush=True)
PYEOF

cat > /tmp/pqvpn_udp_receiver.py << 'PYEOF'
import socket, sys, struct
ADDR = ('0.0.0.0', int(sys.argv[1]))
OUT = sys.argv[2]
CHUNK = 1400
TIMEOUT = 30

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(TIMEOUT)
sock.bind(ADDR)

# Receive length header
pkt, _ = sock.recvfrom(4096)
total_len = struct.unpack('!I', pkt[:4])[0]
# Receive chunk count header
pkt, _ = sock.recvfrom(4096)
total_chunks = struct.unpack('!I', pkt[:4])[0]
print(f'Expecting {total_len} bytes, {total_chunks} chunks', flush=True)

data = bytearray(total_len)
received = 0
while received < total_chunks:
    try:
        pkt, _ = sock.recvfrom(65536)
    except socket.timeout:
        print(f'Timeout after {received}/{total_chunks} chunks', flush=True)
        break
    idx = struct.unpack('!I', pkt[:4])[0]
    chunk = pkt[4:]
    start = idx * CHUNK
    end = min(start + len(chunk), total_len)
    data[start:end] = chunk
    received += 1
    if received % 100 == 0:
        print(f'  {received}/{total_chunks} chunks', flush=True)

with open(OUT, 'wb') as f:
    f.write(bytes(data))
print(f'Received {received}/{total_chunks} chunks, written to {OUT}', flush=True)
PYEOF

echo "Starting UDP receiver on server side..."
sudo ip netns exec vpn_ns1 python3 /tmp/pqvpn_udp_receiver.py "$UDP_PORT" "$FILE_BIG_OUT" &
RCVR_PID=$!
sleep 1

echo "Sending file via tunnel..."
sudo ip netns exec vpn_ns2 python3 /tmp/pqvpn_udp_sender.py "$FILE_BIG" "$TUN_ADDR_SERVER" "$UDP_PORT"
wait $RCVR_PID 2>/dev/null || true

BIG_OUT_HASH=$(sudo ip netns exec vpn_ns1 sha256sum "$FILE_BIG_OUT" | awk '{print $1}')

if [ "$BIG_HASH" = "$BIG_OUT_HASH" ]; then
    echo "  ✅ UDP transfer OK"
else
    echo "  ❌ UDP transfer FAILED — hashes differ"
    echo "     src: $BIG_HASH"
    echo "     dst: $BIG_OUT_HASH"
    ALL_OK=1
fi

echo ""
if [ "$ALL_OK" -eq 0 ]; then
    echo "✅ ALL TRANSFERS PASSED"
    exit 0
else
    echo "❌ SOME TRANSFERS FAILED"
    exit 1
fi