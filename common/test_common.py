import os
import sys
import time
import subprocess
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from common.framing import pack_frame, unpack_frames
from common.openssl_wrapper import start_tls_server, start_tls_client

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CERT_DIR = os.path.join(PROJECT_DIR, 'pki', 'certs', 'classical')
CA = os.path.join(CERT_DIR, 'ca.crt')
SERVER_CERT = os.path.join(CERT_DIR, 'server.crt')
SERVER_KEY = os.path.join(CERT_DIR, 'server.key')
CLIENT_CERT = os.path.join(CERT_DIR, 'client.crt')
CLIENT_KEY = os.path.join(CERT_DIR, 'client.key')

PASS = 0
FAIL = 0


def test(name: str, ok: bool):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f'  ✓ {name}')
    else:
        FAIL += 1
        print(f'  ✗ {name}')


def test_framing():
    payloads = [b'hello', b'\x00\x01\x02' * 100, b'']
    for p in payloads:
        packed = pack_frame(p)
        assert len(packed) == 4 + len(p), f'bad len for {len(p)}'
        unpacked = list(unpack_frames(packed))
        assert len(unpacked) == 1, f'bad frame count for {len(p)}'
        assert unpacked[0] == p, f'content mismatch for {len(p)}'
    stream = b''.join(pack_frame(b'abc') for _ in range(3))
    stream += b'garbage'
    frames = list(unpack_frames(stream))
    assert len(frames) == 3, f'expected 3 frames, got {len(frames)}'
    assert all(f == b'abc' for f in frames)
    test('framing round-trip (empty, small, large, multiple)', True)


def test_tls_relay():
    port = 14430
    cli_msg = b'Hello from client!'
    srv_msg = b'Hello from server!'

    server = start_tls_server(
        '127.0.0.1', port, SERVER_CERT, SERVER_KEY, CA,
        mode='classical',
    )
    time.sleep(0.5)

    client = start_tls_client(
        '127.0.0.1', port, CLIENT_CERT, CLIENT_KEY, CA,
        mode='classical',
    )
    time.sleep(0.5)

    # Test client → server: send on client stdin, read on server stdout
    client.stdin.write(cli_msg)
    client.stdin.flush()
    # Test server → client: send on server stdin, read on client stdout
    server.stdin.write(srv_msg)
    server.stdin.flush()

    import select
    cli_received = b''
    srv_received = b''
    deadline = time.time() + 5

    while (len(cli_received) < len(cli_msg) or len(srv_received) < len(srv_msg)) and time.time() < deadline:
        r, _, _ = select.select([server.stdout, client.stdout], [], [], 0.5)
        if server.stdout in r:
            chunk = server.stdout.read(min(4096, len(cli_msg) - len(srv_received)))
            srv_received += chunk
        if client.stdout in r:
            chunk = client.stdout.read(min(4096, len(srv_msg) - len(cli_received)))
            cli_received += chunk

    server.stdin.close()
    client.stdin.close()

    for proc in (server, client):
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    ok = cli_received == srv_msg and srv_received == cli_msg
    test(f'TLS relay via openssl subprocess (bidirectional)', ok)
    if not ok:
        print(f'    expected client recv: {srv_msg!r}')
        print(f'    actual client recv:   {cli_received!r}')
        print(f'    expected server recv: {cli_msg!r}')
        print(f'    actual server recv:   {srv_received!r}')


def test_tun_import():
    try:
        from common.tun_utils import create_tun, TUNSETIFF, IFF_TUN, IFF_NO_PI
        ok = all(x is not None for x in [create_tun, TUNSETIFF, IFF_TUN, IFF_NO_PI])
    except Exception as e:
        ok = False
        print(f'    import error: {e}')
    test('tun_utils import + constants', ok)


def test_imports():
    from common import framing, tun_utils, openssl_wrapper
    ok = all(hasattr(m, '__file__') for m in [framing, tun_utils, openssl_wrapper])
    test('all common modules import cleanly', ok)


if __name__ == '__main__':
    print('=== PQ-VPN Common Module Tests ===')
    print()
    test_imports()
    test_framing()
    test_tun_import()
    test_tls_relay()
    print()
    total = PASS + FAIL
    print(f'{PASS}/{total} passed' + (' ✅' if FAIL == 0 else ' ❌'))
    sys.exit(0 if FAIL == 0 else 1)
