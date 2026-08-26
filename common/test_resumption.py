import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import common.ticket_cache as tc

PASS = 0
FAIL = 0


def test(name, ok, detail=''):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f'  ✓ {name}')
    else:
        FAIL += 1
        print(f'  ✗ {name}')
        if detail:
            print(f'    {detail}')


def test_cache_paths_are_stable():
    p1 = tc.ticket_path('192.168.100.2')
    p2 = tc.ticket_path('192.168.100.2')
    p3 = tc.ticket_path('10.0.0.9')
    test('ticket path is deterministic per server', p1 == p2 and p1 != p3, f'{p1} / {p3}')
    test('ticket path under ~/.pqvpn/cache', tc.CACHE_DIR == os.path.join(os.path.expanduser('~'), '.pqvpn', 'cache'))


def test_load_missing_returns_none():
    with tempfile.TemporaryDirectory() as td:
        tc.CACHE_DIR = td
        try:
            test('missing ticket returns None', tc.load_ticket('nope.invalid') is None)
        finally:
            tc.CACHE_DIR = os.path.expanduser('~/.pqvpn/cache')


def test_rotate_ticket_atomic(tmp):
    server = '192.168.100.2'
    src = os.path.join(tmp, 'inuse.tick')
    with open(src, 'wb') as f:
        f.write(b'TICKET-BYTES')
    dest = tc.rotate_ticket(server, src)
    ok_exists = os.path.isfile(dest)
    ok_content = open(dest, 'rb').read() == b'TICKET-BYTES'
    ok_no_tmp = not os.path.exists(dest + f'.tmp{os.getpid()}')
    test('rotate_ticket copies ticket to stable cache path', ok_exists and ok_content and ok_no_tmp, dest)


def test_rotate_ticket_noop_same_path():
    p = tc.ticket_path('x.invalid')
    with open(p, 'wb') as f:
        f.write(b'OLD')
    returned = tc.rotate_ticket('x.invalid', p)
    ok = returned == p and open(p, 'rb').read() == b'OLD'
    test('rotate_ticket leaves in-place file untouched', ok, returned)
    os.remove(p)


def test_clear_ticket():
    server = 'y.invalid'
    p = tc.ticket_path(server)
    with open(p, 'wb') as f:
        f.write(b'X')
    tc.clear_ticket(server)
    test('clear_ticket removes cached file', not os.path.isfile(p))
    tc.clear_ticket(server)  # idempotent
    test('clear_ticket is idempotent', not os.path.isfile(p))


def test_resumption_flags_in_wrapper(tmp):
    import subprocess
    from unittest import mock
    from common.openssl_wrapper import start_tls_client

    sess_in = os.path.join(tmp, 'in.tick')
    sess_out = os.path.join(tmp, 'out.tick')
    with open(sess_in, 'wb') as f:
        f.write(b'X')

    with mock.patch.object(subprocess, 'Popen', return_value=object()):
        proc = start_tls_client(
            '127.0.0.1', 14499,
            'pki/certs/classical/client.crt', 'pki/certs/classical/client.key',
            'pki/certs/classical/ca.crt',
            sess_in=sess_in, sess_out=sess_out,
        )
    test('start_tls_client accepts sess_in/sess_out without error', proc is not None)


def test_openssl_resumption_real(tmp):
    """Real end-to-end: prime a ticket, then a second connection must say Reused."""
    import subprocess
    import time
    import socket

    def wait_port(host, port, secs=5):
        deadline = time.time() + secs
        while time.time() < deadline:
            try:
                socket.create_connection((host, port), timeout=0.5).close()
                return True
            except OSError:
                time.sleep(0.05)
        return False

    cert_dir = '/home/chacko/Chacko/VPN/pki/certs/classical'
    port = 15460
    server = subprocess.Popen(
        ['openssl', 's_server', '-accept', f'127.0.0.1:{port}',
         '-cert', f'{cert_dir}/server.crt', '-key', f'{cert_dir}/server.key',
         '-CAfile', f'{cert_dir}/ca.crt', '-Verify', '1', '-quiet'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert wait_port('127.0.0.1', port)
        tick = os.path.join(tmp, 'real.tick')

        def connect(sess_in=None, sess_out=None):
            cmd = ['openssl', 's_client', '-connect', f'127.0.0.1:{port}',
                   '-cert', f'{cert_dir}/client.crt', '-key', f'{cert_dir}/client.key',
                   '-CAfile', f'{cert_dir}/ca.crt', '-verify', '1']
            if sess_in:
                cmd += ['-sess_in', sess_in]
            if sess_out:
                cmd += ['-sess_out', sess_out]
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            p.stdin.write(b'HELLO\n')
            p.stdin.flush()
            time.sleep(1.0)          # let the NewSessionTicket arrive + get saved
            p.stdin.close()
            out = p.stdout.read().decode(errors='replace')
            p.wait(timeout=8)
            return out

        c1 = connect(sess_out=tick)
        c2 = connect(sess_in=tick)
        ok = 'New,' in c1 and 'Reused,' in c2
        test('real OpenSSL resumption (prime→Reused)', ok,
             f'c1 has New,={chr(34)}New,{chr(34) in c1} c2 has Reused,={chr(34)}Reused,{chr(34) in c2} '
             f'c1={c1[:120]!r} c2={c2[:120]!r}')
    finally:
        server.terminate()
        server.wait()


def test_resumption_flags_wired_in_client(tmp):
    """Client run_session passes sess_in/sess_out derived from the ticket cache."""
    src = open('/home/chacko/Chacko/VPN/client/vpn_client.py').read()
    ok = 'load_ticket' in src and 'save_ticket_file' in src and 'sess_in=' in src and 'sess_out=' in src
    test('client wired to ticket cache (sess_in/sess_out)', ok)
    # resumed must be evidence-based: drain_stderr receives ticket_offered and
    # sets resumed only when the cert exchange was actually skipped. A fresh
    # s_server regenerates ticket keys, so offered tickets usually fall back
    # to a full handshake — the old code reported that as 'resumed'.
    wired = ('ticket_offered' in src
             and "args=(tls_proc.stderr, stop, status, sess_in is not None)" in src
             and 'resumed = bool(ticket_offered and not saw_cert_exchange)' in src
             and 'status.set_resumed(resumed)' in src)
    test('resumed flag set on evidence, not intent', wired)
    test('offer message states intent, not outcome',
         'offering cached ticket for resumption' in src
         and 'resuming session from cache' not in src)
    test('outcome logged at Verification checkpoint',
         'session RESUMED (ticket accepted)' in src
         and 'full handshake (ticket not accepted' in src)
    test('old assumed-resumed line removed',
         'status.resumed = sess_in is not None' not in src)


if __name__ == '__main__':
    print('=== PQ-VPN Phase 3 (session resumption) Tests ===')
    print()
    with tempfile.TemporaryDirectory() as td:
        test_cache_paths_are_stable()
        test_load_missing_returns_none()
        test_rotate_ticket_atomic(td)
        test_rotate_ticket_noop_same_path()
        test_clear_ticket()
        test_resumption_flags_in_wrapper(td)
        test_openssl_resumption_real(td)
        test_resumption_flags_wired_in_client(td)
    print()
    total = PASS + FAIL
    print(f'{PASS}/{total} passed' + (' ✅' if FAIL == 0 else ' ❌'))
    sys.exit(0 if FAIL == 0 else 1)