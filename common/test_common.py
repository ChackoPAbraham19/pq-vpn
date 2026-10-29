import os
import sys
import time
import subprocess
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from common.framing import pack_frame, unpack_frames, TYPE_DATA, TYPE_CONTROL
from common.openssl_wrapper import start_tls_server, start_tls_client
from client.vpn_client import drain_stderr

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
        assert len(packed) == 5 + len(p), f'bad len for {len(p)}'
        unpacked = list(unpack_frames(packed))
        assert len(unpacked) == 1, f'bad frame count for {len(p)}'
        ftype, data = unpacked[0]
        assert ftype == TYPE_DATA, f'bad type for {len(p)}'
        assert data == p, f'content mismatch for {len(p)}'
    stream = b''.join(pack_frame(b'abc') for _ in range(3))
    stream += b'garbage'
    frames = list(unpack_frames(stream))
    assert len(frames) == 3, f'expected 3 frames, got {len(frames)}'
    assert all(f == b'abc' for _, f in frames)
    test('framing round-trip (empty, small, large, multiple)', True)


def test_control_frames():
    ctrl = pack_frame(b'\x01', ftype=TYPE_CONTROL)
    ftype, payload = list(unpack_frames(ctrl))[0]
    test('control frame type byte round-trip', ftype == TYPE_CONTROL and payload == b'\x01')


def test_keepalive_frame():
    from common.framing import pack_keepalive, is_keepalive
    ka = pack_keepalive()
    ftype, payload = list(unpack_frames(ka))[0]
    ok = ftype == TYPE_CONTROL and is_keepalive(ftype, payload)
    test('keepalive control frame round-trip', ok)


def test_config_push():
    from common.framing import pack_config, unpack_config
    cfg = {'ip': '10.8.0.2', 'gateway': '10.8.0.1', 'port': 14431, 'dns': '10.8.0.1'}
    packed = pack_config(cfg)
    parsed, remaining = unpack_config(packed)
    test('config-push control frame round-trip', parsed == cfg and remaining == b'')


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

    try:
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
    finally:
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


def test_tls_relay_fd():
    """Two concurrent TLS relays on per-client ports (Phase 4 model)."""
    import select

    results = []

    def run_pair(idx):
        port = 14430 + idx  # each client gets its own data port
        cli_msg = f'client-{idx} hello'.encode()
        srv_msg = f'server-{idx} ack'.encode()

        server = start_tls_server(
            '127.0.0.1', port, SERVER_CERT, SERVER_KEY, CA,
            mode='classical',
        )
        time.sleep(0.3)
        client = start_tls_client(
            '127.0.0.1', port, CLIENT_CERT, CLIENT_KEY, CA,
            mode='classical',
        )
        time.sleep(0.3)

        client.stdin.write(cli_msg)
        client.stdin.flush()
        server.stdin.write(srv_msg)
        server.stdin.flush()

        cli_recv = b''
        srv_recv = b''
        deadline = time.time() + 5
        while (len(cli_recv) < len(srv_msg) or len(srv_recv) < len(cli_msg)) and time.time() < deadline:
            r, _, _ = select.select([server.stdout, client.stdout], [], [], 0.5)
            if server.stdout in r:
                srv_recv += server.stdout.read(min(4096, len(cli_msg) - len(srv_recv)))
            if client.stdout in r:
                cli_recv += client.stdout.read(min(4096, len(srv_msg) - len(cli_recv)))

        server.stdin.close()
        client.stdin.close()
        for proc in (server, client):
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

        results.append(cli_recv == srv_msg and srv_recv == cli_msg)

    # concurrent pairs on distinct ports
    threads = [threading.Thread(target=run_pair, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    test('two concurrent TLS relays on per-client ports', all(results) and len(results) == 2)


def _free_port():
    import socket as pysocket
    s = pysocket.socket(pysocket.AF_INET, pysocket.SOCK_STREAM)
    s.setsockopt(pysocket.SOL_SOCKET, pysocket.SO_REUSEADDR, 1)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_phase4_welcome_to_relay():
    """End-to-end with the real daemon helpers: plaintext welcome hands out a
    port only; the full config-push arrives as the first frame INSIDE the mTLS
    channel; the relay then carries framed data with the leftover buffer."""
    import socket as pysocket
    import os as osmod
    import fcntl
    from common.framing import pack_config, pack_frame
    from server.session_manager import SessionManager
    from client.vpn_client import welcome_handshake, read_pushed_config

    port_base = _free_port()
    mgr = SessionManager({'gateway': '10.8.0.1', 'dns': '10.8.0.1',
                          'tun_net': '10.8.0.0/24', 'max_clients': 3,
                          'data_port_base': port_base})

    class Status:
        bytes_in = 0
        bytes_out = 0

    status = Status()
    tun_r, tun_w = osmod.pipe()
    tun_fd = tun_w

    listener = pysocket.socket(pysocket.AF_INET, pysocket.SOCK_STREAM)
    listener.setsockopt(pysocket.SOL_SOCKET, pysocket.SO_REUSEADDR, 1)
    listener.bind(('127.0.0.1', 0))
    listener.listen(4)
    welcome_port = listener.getsockname()[1]
    results = {}
    server_procs = []

    def server_side():
        conn, _ = listener.accept()
        welcome = mgr.welcome_payload()
        results['welcome'] = welcome
        sess = mgr.get(welcome['id'])
        sess.tls_proc = start_tls_server(
            '127.0.0.1', welcome['port'], SERVER_CERT, SERVER_KEY, CA, mode='classical')
        server_procs.append(sess.tls_proc)
        # Same order as vpn_server.handle_client: config into the TLS pipe
        # first, then the port-only welcome.
        sess.tls_proc.stdin.write(pack_config(welcome))
        sess.tls_proc.stdin.flush()
        conn.sendall(pack_config({'port': welcome['port']}))
        conn.close()
        from server.vpn_server import relay_tls_to_tun
        t = threading.Thread(
            target=relay_tls_to_tun, args=(sess.tls_proc.stdout.fileno(), tun_fd, sess, status), daemon=True)
        t.start()
        time.sleep(2)
        results['server_connected'] = sess.connected
        fl = fcntl.fcntl(tun_r, fcntl.F_GETFL)
        fcntl.fcntl(tun_r, fcntl.F_SETFL, fl | osmod.O_NONBLOCK)
        data = b''
        try:
            while True:
                chunk = osmod.read(tun_r, 4096)
                if not chunk:
                    break
                data += chunk
        except BlockingIOError:
            pass
        results['server_recv'] = data
        sess.stop.set()
        sess.tls_proc.terminate()

    def client_side():
        port = welcome_handshake('127.0.0.1', welcome_port, timeout=3)
        results['client_port'] = port
        proc = None
        for _ in range(5):
            proc = start_tls_client(
                '127.0.0.1', port, CLIENT_CERT, CLIENT_KEY, CA, mode='classical')
            try:
                cfg, rest = read_pushed_config(proc.stdout.fileno(), timeout=3.0)
                results['client_cfg'] = cfg
                results['client_rest'] = rest
                break
            except RuntimeError:
                proc.terminate()
                proc.wait()
                err = proc.stderr.read().decode(errors='replace')
                if 'Connection refused' not in err:
                    raise
                time.sleep(0.2)
        msg = b'hello-over-framed-tls'
        try:
            proc.stdin.write(pack_frame(msg))
            proc.stdin.flush()
        except BrokenPipeError:
            pass
        time.sleep(1.5)
        proc.terminate()

    t1 = threading.Thread(target=server_side, daemon=True)
    t2 = threading.Thread(target=client_side, daemon=True)
    t1.start()
    time.sleep(0.3)
    t2.start()
    t1.join(timeout=8)
    t2.join(timeout=8)

    for p in server_procs:
        try:
            p.wait(timeout=2)
        except subprocess.TimeoutExpired:
            p.kill()

    welcome = results.get('welcome') or {}
    cfg = results.get('client_cfg') or {}
    test('welcome wire carries the port only',
         results.get('client_port') == welcome.get('port'))
    test('config-push arrives inside TLS with the assigned IP',
         cfg.get('ip') == '10.8.0.2' and cfg.get('gateway') == '10.8.0.1'
         and results.get('client_rest') == b'')
    ok = results.get('server_recv') == b'hello-over-framed-tls' and results.get('server_connected') is True
    test('Phase 4: welcome -> TLS config-push -> framed relay (session marked connected)', ok)
    if not ok:
        print(f'    cfg: {cfg!r}')
        print(f'    server_recv: {results.get("server_recv")!r}')


def test_tun_import():
    try:
        from common.tun_utils import create_tun, TUNSETIFF, IFF_TUN, IFF_NO_PI
        ok = all(x is not None for x in [create_tun, TUNSETIFF, IFF_TUN, IFF_NO_PI])
    except Exception as e:
        ok = False
        print(f'    import error: {e}')
    test('tun_utils import + constants', ok)


def test_welcome_handshake():
    """Plaintext welcome round-trip returns only the data port; the client
    helper rejects a welcome that tries to push anything malformed."""
    import socket as pysocket
    import threading
    from common.framing import pack_config
    from server.session_manager import SessionManager
    from client.vpn_client import welcome_handshake

    manager = SessionManager({'gateway': '10.8.0.1'})
    welcome = manager.welcome_payload()
    assert welcome is not None, 'pool should have slots'
    assert welcome['ip'] == '10.8.0.2'
    assert welcome['port'] == 14431

    def listener():
        # Bind + listen in the caller's thread so the client can never race
        # ahead of the bind (it did, on a slower clean-build container).
        srv = pysocket.socket(pysocket.AF_INET, pysocket.SOCK_STREAM)
        srv.setsockopt(pysocket.SOL_SOCKET, pysocket.SO_REUSEADDR, 1)
        srv.bind(('127.0.0.1', 0))
        srv.listen(1)
        return srv, srv.getsockname()[1]

    def serve(srv, payload):
        conn, _ = srv.accept()
        conn.sendall(pack_config(payload))
        conn.close()
        srv.close()

    srv, port = listener()
    t = threading.Thread(target=serve, args=(srv, {'port': welcome['port']}), daemon=True)
    t.start()
    got = welcome_handshake('127.0.0.1', port, timeout=3)
    t.join(timeout=3)
    test('welcome handshake returns the assigned port', got == 14431)

    srv, port = listener()
    t = threading.Thread(target=serve, args=(srv, {'port': 'evil'}), daemon=True)
    t.start()
    try:
        welcome_handshake('127.0.0.1', port, timeout=3)
        bad = False
    except RuntimeError:
        bad = True
    t.join(timeout=3)
    test('welcome handshake rejects a non-integer port', bad)


def test_session_manager_pool():
    from server.session_manager import SessionManager
    manager = SessionManager({'gateway': '10.8.0.1'})
    a = manager.welcome_payload()
    b = manager.welcome_payload()
    c = manager.welcome_payload()
    d = manager.welcome_payload()  # pool exhausted (only .2 .3 .4)
    ok = (
        a['ip'] == '10.8.0.2' and b['ip'] == '10.8.0.3' and c['ip'] == '10.8.0.4'
        and d is None
        and len(manager.snapshot()) == 3
    )
    test('session manager IP pool (3 clients max)', ok)
    test('ports are slot-bound: base, base+1, base+2',
         (a['port'], b['port'], c['port']) == (14431, 14432, 14433)
         and manager.port_range == (14431, 14433))
    # Reconnect: the freed slot's port is reused, never base+3.
    manager.remove(a['id'], manager.get(a['id']).host_id)
    e = manager.welcome_payload()
    test('freed slot is recycled within the port range',
         e is not None and e['port'] == 14431 and e['ip'] == '10.8.0.2' and e['id'] == 4)


def test_imports():
    from common import framing, tun_utils, openssl_wrapper
    ok = all(hasattr(m, '__file__') for m in [framing, tun_utils, openssl_wrapper])
    test('all common modules import cleanly', ok)


def test_parse_brief_line():
    from common.openssl_wrapper import parse_brief_line

    samples = {
        'Protocol version: TLSv1.3': ('protocol', 'TLSv1.3'),
        'Ciphersuite: TLS_AES_256_GCM_SHA384': ('cipher', 'TLS_AES_256_GCM_SHA384'),
        'Signature type: mldsa65': ('sig_type', 'mldsa65'),
        'Server Temp Key: X25519, 253 bits': ('kex', 'X25519, 253 bits'),
    }
    ok = all(parse_brief_line(line) == want for line, want in samples.items())
    test('brief parser maps known lines', ok)

    ok_none = parse_brief_line('CONNECTION ESTABLISHED') is None \
        and parse_brief_line('Peer certificate: C = US') is None \
        and parse_brief_line('Ciphersuite:') is None \
        and parse_brief_line('') is None
    test('brief parser ignores non-negotiation lines', ok_none)

    # split across chunk boundaries: value on the continuation line
    ok_split = parse_brief_line('  Signature type: ECDSA  ') == ('sig_type', 'ECDSA')
    test('brief parser tolerates whitespace', ok_split)


def test_drain_stderr_resumed_evidence():
    """'resumed' must reflect EVIDENCE (cert exchange skipped), not intent.

    A fresh per-client s_server regenerates ticket keys, so an offered ticket
    usually falls back to a full handshake — the old code reported that as
    'resumed', which was a lie.
    """
    import io
    import threading
    from common.daemon_utils import StatusTracker

    FULL = ('CONNECTION ESTABLISHED\n'
            'Protocol version: TLSv1.3\n'
            'Ciphersuite: TLS_AES_256_GCM_SHA384\n'
            'Requested Signature Algorithms: mldsa65\n'
            'Peer certificate: CN = vpn-server\n'
            'Signature type: mldsa65\n'
            'Verification: OK\n')
    RESUMED = ('CONNECTION ESTABLISHED\n'
               'Protocol version: TLSv1.3\n'
               'Ciphersuite: TLS_AES_256_GCM_SHA384\n'
               'Peer certificate: CN = vpn-server\n'
               'Verification: OK\n')

    def run(stderr_text, offered):
        st = StatusTracker({'mode': 'pqc'}, tun_name='tun0', tun_addr='x')
        t = threading.Thread(
            target=drain_stderr,
            args=(io.BytesIO(stderr_text.encode()), threading.Event(), st, offered),
            daemon=True)
        t.start()
        t.join(timeout=5)
        return st, not t.is_alive()

    st, done = run(FULL, offered=True)
    test('drain consumes full-handshake stderr and exits at EOF',
         done and st.snapshot()['resumed'] is False)

    st2, _ = run(RESUMED, offered=True)
    test('genuine resumption (no cert-exchange lines) sets resumed',
         st2.snapshot()['resumed'] is True)

    st3, _ = run(FULL, offered=False)
    ok3 = st3.snapshot()['resumed'] is False and \
        st3.snapshot()['negotiated'].get('sig_type') == 'mldsa65'
    test('full handshake without ticket never claims resumed; sig_type captured', ok3)

    # incremental delivery: lines arriving one at a time must still parse
    # (readline contract — the old blocking read(4096) held small briefs
    # in the pipe until process exit)
    class LineStream:
        def __init__(self, lines):
            self._lines = [l + '\n' for l in lines]
        def readline(self):
            return self._lines.pop(0).encode() if self._lines else b''
    st4 = StatusTracker({'mode': 'pqc'}, tun_name='tun0', tun_addr='x')
    lines = RESUMED.strip().split('\n')
    t4 = threading.Thread(target=drain_stderr,
                          args=(LineStream(lines), threading.Event(), st4, True), daemon=True)
    t4.start()
    t4.join(timeout=5)
    snap4 = st4.snapshot()
    ok4 = snap4['resumed'] is True and snap4['negotiated'].get('protocol') == 'TLSv1.3'
    test('line-by-line delivery parses incrementally and detects resumption', ok4)


def test_status_negotiated_lifecycle():
    import threading
    from common.daemon_utils import StatusTracker

    st = StatusTracker({'mode': 'pqc'}, tun_name='tun0', tun_addr='x')
    st.note_negotiated('cipher', 'TLS_AES_256_GCM_SHA384')
    st.resumed = True
    st.tls_pid = 4242

    snap = st.snapshot()
    ok_set = snap['negotiated'].get('cipher') == 'TLS_AES_256_GCM_SHA384' and snap['resumed']
    test('negotiated values visible in snapshot', ok_set)

    # simulate a reconnect: fresh attempt must not inherit stale values
    st.reset_session()
    snap2 = st.snapshot()
    ok_reset = snap2['negotiated'] == {} and snap2['resumed'] is False and snap2['tls_pid'] == 0
    test('reset_session clears negotiated/resumed/tls_pid', ok_reset)

    # snapshot returns a copy — mutating it must not corrupt tracker state
    snap3 = st.snapshot()
    snap3['negotiated']['hack'] = True
    ok_copy = 'hack' not in st.snapshot()['negotiated']
    test('snapshot negotiated dict is a copy', ok_copy)

    # concurrent notes don't corrupt state
    def hammer():
        for i in range(200):
            st.note_negotiated('k', i)
    t = threading.Thread(target=hammer)
    t.start()
    st.note_negotiated('main', 1)
    t.join()
    ok_threaded = st.snapshot()['negotiated'].get('main') == 1
    test('note_negotiated thread-safe under contention', ok_threaded)


def test_ipc_server_socket_permissions():
    """IpcServer binds its Unix socket 0666 so non-root consumers can connect."""
    import tempfile
    import common.daemon_utils as du
    from common.daemon_utils import IpcServer, StatusTracker

    with tempfile.TemporaryDirectory() as tmpdir:
        orig = du.socket_path
        du.socket_path = lambda role, instance=None: os.path.join(
            tmpdir, f'pqvpn-{role}' + (f'-{instance}' if instance else '') + '.sock')
        ipc = None
        try:
            st = StatusTracker({'mode': 'pqc'}, tun_name='tun0', tun_addr='10.8.0.1/24')
            ipc = IpcServer(st, role='permtest')
            ipc.start()
            time.sleep(0.3)
            mode = os.stat(du.socket_path('permtest')).st_mode & 0o777
            test('IpcServer socket is 0666', mode == 0o666)
        finally:
            if ipc:
                ipc.stop()
            du.socket_path = orig


def test_ipc_server_survives_caller_hangup():
    """A caller that hangs up mid-reply must not kill the IPC listener.

    Regression: the accept loop used to `break` on any OSError, so one
    BrokenPipeError from sendall ended status serving for the life of the
    process. Seen on the VPS: the tunnel relayed fine for four days while
    every status query answered "VPN is not running".
    """
    import socket
    import struct
    import tempfile
    import common.daemon_utils as du
    from common.daemon_utils import IpcServer, StatusTracker, ipc_request

    with tempfile.TemporaryDirectory() as tmpdir:
        orig = du.socket_path
        du.socket_path = lambda role, instance=None: os.path.join(
            tmpdir, f'pqvpn-{role}' + (f'-{instance}' if instance else '') + '.sock')
        ipc = None
        try:
            st = StatusTracker({'mode': 'pqc'}, tun_name='tun0', tun_addr='10.8.0.1/24')
            ipc = IpcServer(st, role='hangup')
            ipc.start()
            time.sleep(0.3)

            # Connect and vanish before the reply is written, several times —
            # RST rather than a clean FIN is what breaks sendall. A refused
            # connect means the listener is already dead, so count them and
            # report a failure rather than raising out of the suite.
            hangups = 0
            for _ in range(5):
                try:
                    s_ = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    s_.connect(du.socket_path('hangup'))
                    s_.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                  struct.pack('ii', 1, 0))
                    s_.close()
                    hangups += 1
                except OSError:
                    break
                time.sleep(0.05)

            time.sleep(0.3)
            try:
                reply = ipc_request('hangup')
            except OSError:
                reply = None      # connection refused: the listener is gone
            alive = (hangups == 5 and isinstance(reply, dict)
                     and reply.get('mode') == 'pqc')
            test('IPC listener survives callers hanging up mid-reply', alive)
        finally:
            if ipc:
                ipc.stop()
            du.socket_path = orig


def test_profile_resolution():
    """`pqvpn up [profile]` maps a name to a config in client/, never a path."""
    from cli.vpnctl import resolve_profile, profile_config_name

    test('named profile -> config-<name>.yaml',
         profile_config_name('vps') == 'config-vps.yaml')
    test('empty profile -> plain config.yaml',
         profile_config_name('') == 'config.yaml')

    have_all = lambda path: True
    got = resolve_profile(None, have_all)
    test('default prefers vps when present',
         got is not None and got[0] == 'vps' and got[1].endswith('config-vps.yaml'))

    only_pqc = lambda path: path.endswith('config-pqc.yaml')
    got = resolve_profile(None, only_pqc)
    test('default falls back to pqc', got is not None and got[0] == 'pqc')

    got = resolve_profile('vps', have_all)
    test('explicit profile is honoured', got is not None and got[0] == 'vps')

    test('missing explicit profile resolves to nothing',
         resolve_profile('nope', lambda path: False) is None)
    test('no profiles at all resolves to nothing',
         resolve_profile(None, lambda path: False) is None)


def test_wait_until_connected():
    """`up` must wait for state=connected, not merely for the IPC socket.

    Regression: _spawn_daemon returned as soon as the socket appeared, which
    happens long before routes and DNS are applied — so the command reported
    success while traffic was still leaving outside the tunnel. Clock and
    daemon are injected, so this is deterministic.
    """
    from cli.vpnctl import wait_until_connected

    ticks = {'t': 0.0}
    def clock():
        return ticks['t']
    def advance(_):
        ticks['t'] += 1.0

    # The socket answers 'handshake-in-progress' twice before it is really up.
    states = iter([{'state': 'handshake-in-progress'},
                   {'state': 'handshake-in-progress'},
                   {'state': 'connected', 'tun_addr': '10.8.0.2/24'}])
    outcome, status = wait_until_connected(
        read_status=lambda: next(states, {'state': 'connected'}),
        is_alive=lambda: True, now=clock, sleep=advance)
    test('waits through handshake, then reports connected',
         outcome == 'connected' and status.get('tun_addr') == '10.8.0.2/24')

    # A daemon that dies mid-handshake is a failure, not a timeout.
    ticks['t'] = 0.0
    alive = iter([True, False])
    outcome, _ = wait_until_connected(
        read_status=lambda: {'state': 'handshake-in-progress'},
        is_alive=lambda: next(alive, False), now=clock, sleep=advance)
    test('daemon exiting during handshake is reported as died', outcome == 'died')

    # Never reaching 'connected' must time out rather than hang or claim success.
    ticks['t'] = 0.0
    outcome, _ = wait_until_connected(
        read_status=lambda: {'state': 'reconnecting'},
        is_alive=lambda: True, now=clock, sleep=advance, timeout=5.0)
    test('a tunnel that never comes up times out', outcome == 'timeout')

    # A socket that is not answering yet must not be mistaken for success.
    ticks['t'] = 0.0
    outcome, status = wait_until_connected(
        read_status=lambda: None, is_alive=lambda: True,
        now=clock, sleep=advance, timeout=3.0)
    test('silent status socket is not treated as connected',
         outcome == 'timeout' and status is None)


def test_crl_expiry_warning():
    """An expired or near-expiry CRL is named at startup, not during handshakes.

    Regression: pki/ca.cnf shipped default_crl_days=30 against 10-year certs,
    so the CRL silently expired a month after the CA was generated. From then
    on `-crl_check -verify_return_error` refused every client, and the only
    evidence was "error 12 ... CRL has expired" inside a failed TLS exchange.
    Dates are injected so this does not depend on the wall clock.
    """
    from datetime import datetime, timedelta, timezone
    from server.vpn_server import crl_expiry_warning

    now = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
    path = '/tmp/crl.pem'

    healthy = crl_expiry_warning(now + timedelta(days=3650), now, path)
    test('healthy CRL warns about nothing', healthy is None)

    expired = crl_expiry_warning(now - timedelta(days=7), now, path)
    test('expired CRL is reported',
         expired is not None and 'EXPIRED' in expired)
    test('expired CRL message names the fix',
         expired is not None and 'refresh_crl.sh' in expired and path in expired)

    soon = crl_expiry_warning(now + timedelta(days=3), now, path)
    test('CRL expiring within 7 days warns early',
         soon is not None and 'EXPIRED' not in soon and 'refresh_crl.sh' in soon)

    edge = crl_expiry_warning(now + timedelta(days=30), now, path)
    test('CRL 30 days out does not warn', edge is None)

    test('unreadable CRL is not treated as expired',
         crl_expiry_warning(None, now, path) is None)


def test_crl_next_update_parses_openssl_format():
    """nextUpdate parsing handles openssl's padded single-digit days."""
    import subprocess
    from datetime import timezone
    import server.vpn_server as vs

    captured = {}

    class _Fake:
        def __init__(self, stdout):
            self.stdout = stdout

    orig = subprocess.run
    for raw, expect_day in (('nextUpdate=Oct 26 17:33:54 2036 GMT\n', 26),
                            ('nextUpdate=Oct  5 07:01:02 2036 GMT\n', 5)):
        subprocess.run = lambda *a, **k: _Fake(raw)
        try:
            got = vs.crl_next_update('/tmp/crl.pem', 'pqc')
        finally:
            subprocess.run = orig
        captured[expect_day] = got
        test(f'parses nextUpdate with day {expect_day}',
             got is not None and got.day == expect_day and got.year == 2036
             and got.tzinfo is timezone.utc)

    subprocess.run = lambda *a, **k: _Fake('garbage\n')
    try:
        test('unparseable nextUpdate returns None',
             vs.crl_next_update('/tmp/crl.pem', 'pqc') is None)
    finally:
        subprocess.run = orig


def test_vpnctl_eacces_handling():
    """vpnctl reports 'root-owned socket' on EACCES instead of 'not running'."""
    import socket
    import tempfile
    import cli.vpnctl as vp
    from cli.vpnctl import _read_status_sock

    if os.geteuid() == 0:
        # root bypasses socket permissions, so a 0000 socket cannot produce
        # EACCES here (Docker clean-build runs the suites as root).
        test('root-owned socket raises PermissionError (skipped: running as root)', True)
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, 'rootowned.sock')
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(1)
        os.chmod(path, 0o000)  # simulate root-only socket
        orig = vp.socket_path
        vp.socket_path = lambda role, instance=None: path
        try:
            try:
                _read_status_sock(path)
                ok = False
            except PermissionError:
                ok = True
            test('root-owned socket raises PermissionError, not silent None', ok)
        finally:
            vp.socket_path = orig
            os.chmod(path, 0o666)
            srv.close()
            os.unlink(path)


def test_pool_pressure_reclaim():
    """reclaim_idle frees idle slots under pool pressure (kill -> quick reconnect)."""
    from server.session_manager import SessionManager
    mgr = SessionManager({'gateway': '10.8.0.1', 'dns': '10.8.0.1',
                          'tun_net': '10.8.0.0/24', 'max_clients': 2})
    a = mgr.welcome_payload()
    b = mgr.welcome_payload()
    c = mgr.welcome_payload()
    ok_full = a is not None and b is not None and c is None
    test('pool full returns None before reclaim', ok_full)
    for s in list(mgr.sessions.values()):
        s.last_seen = 0  # force them all to look idle
    d = mgr.welcome_payload()
    test('idle reclaim frees a slot under pressure', d is not None)


def test_ipc_shutdown_command():
    """A daemon's IPC 'shutdown' command sets its stop event and replies ok.

    Both vpn_server and vpn_client wire this the same way; test the wiring.
    """
    import threading
    import tempfile
    import common.daemon_utils as du
    from common.daemon_utils import IpcServer, StatusTracker, ipc_request

    with tempfile.TemporaryDirectory() as tmpdir:
        orig = du.socket_path
        du.socket_path = lambda role, instance=None: os.path.join(
            tmpdir, f'pqvpn-{role}' + (f'-{instance}' if instance else '') + '.sock')
        ipc = None
        try:
            stop = threading.Event()

            def handler(line: str) -> dict:
                import json as _json
                try:
                    payload = _json.loads(line)
                except _json.JSONDecodeError:
                    return {'error': 'invalid json'}
                if payload.get('cmd') == 'shutdown':
                    stop.set()
                    return {'ok': True}
                return {'error': 'unknown command'}

            st = StatusTracker({'mode': 'classical'}, tun_name='tun0',
                               tun_addr='10.8.0.1/24')
            ipc = IpcServer(st, role='shut', command_handler=handler)
            ipc.start()
            time.sleep(0.3)
            reply = ipc_request('shut', '{"cmd": "shutdown"}')
            ok = (isinstance(reply, dict)
                  and (reply.get('result') or {}).get('ok') is True
                  and stop.is_set())
            test('IPC shutdown command sets stop event, replies ok', ok)
        finally:
            if ipc:
                ipc.stop()
            du.socket_path = orig


def test_kill_by_role_eacces_honest():
    """_kill_by_role reports root-owned daemons honestly, no traceback."""
    import cli.vpnctl as vp
    import click.testing  # noqa: F401  (ensure click present)

    # Path A: IPC shutdown works (non-root happy path) -> returns True.
    orig_ipcreq = vp.ipc_request
    vp.ipc_request = lambda role, command='', instance=None: \
        {'status': {}, 'result': {'ok': True}}
    try:
        # socket/pid files won't exist; unlink attempts must be tolerated.
        ok = vp._kill_by_role('nonexistentrole')
        test('IPC shutdown path returns True without signals', ok is True)
    finally:
        vp.ipc_request = orig_ipcreq

    # Path B: IPC unreachable, daemon root-owned -> honest message, no raise.
    vp.ipc_request = lambda *a, **k: (_ for _ in ()).throw(
        ConnectionRefusedError('gone'))
    orig_read_pid = vp.read_pid_file
    orig_kill = os.kill
    vp.read_pid_file = lambda role, instance=None: 424242
    os.kill = lambda pid, sig: (_ for _ in ()).throw(
        PermissionError(1, 'Operation not permitted'))
    try:
        ok = vp._kill_by_role('nonexistentrole')
        test('root-owned daemon reported honestly (returns False, no crash)',
             ok is False)
    finally:
        vp.ipc_request = orig_ipcreq
        vp.read_pid_file = orig_read_pid
        os.kill = orig_kill


def test_port_recycling_across_reconnects():
    """Three clients reconnecting many times never leave the 3-port range."""
    from server.session_manager import SessionManager
    mgr = SessionManager({'gateway': '10.8.0.1', 'max_clients': 3,
                          'data_port_base': 20000})
    seen_ports = set()
    seen_ips = set()
    for _round in range(5):
        batch = [mgr.welcome_payload() for _ in range(3)]
        assert all(batch), 'pool should never be exhausted between rounds'
        for w in batch:
            seen_ports.add(w['port'])
            seen_ips.add(w['ip'])
        for w in batch:
            mgr.remove(w['id'], mgr.get(w['id']).host_id)
    test('15 sessions over 3 slots use exactly 3 ports',
         seen_ports == {20000, 20001, 20002})
    test('and exactly 3 tunnel IPs', seen_ips == {'10.8.0.2', '10.8.0.3', '10.8.0.4'})


def test_pool_honours_tun_net():
    """Addresses derive from tun_net; nothing hardcodes 10.8.0."""
    from server.session_manager import SessionManager, ipv4_dst
    mgr = SessionManager({'gateway': '10.99.5.1', 'tun_net': '10.99.5.0/24',
                          'max_clients': 2})
    a = mgr.welcome_payload()
    packet = bytes([0x45]) + bytes(15) + bytes([10, 99, 5, 2]) + bytes(4)
    test('pool allocates from tun_net', a['ip'] == '10.99.5.2'
         and a['subnet'] == '10.99.5.1/24')
    test('demux lookup by packed dst finds the session',
         mgr.lookup(ipv4_dst(packet)) is mgr.get(a['id']))
    mgr.remove(a['id'], mgr.get(a['id']).host_id)
    test('lookup misses after removal', mgr.lookup(ipv4_dst(packet)) is None)


def test_naccept_flag():
    """start_tls_server passes -naccept 1 by default, omits it for None."""
    import common.openssl_wrapper as ow
    calls = []

    class FakeProc:
        pid = 0

    orig = ow.subprocess.Popen
    ow.subprocess.Popen = lambda cmd, **kw: (calls.append(cmd), FakeProc())[1]
    try:
        ow.start_tls_server('127.0.0.1', 1, 'c', 'k', 'ca')
        ow.start_tls_server('127.0.0.1', 1, 'c', 'k', 'ca', naccept=None)
    finally:
        ow.subprocess.Popen = orig
    default_cmd, long_cmd = calls
    test('-naccept 1 present by default',
         '-naccept' in default_cmd and default_cmd[default_cmd.index('-naccept') + 1] == '1')
    test('-naccept omitted for naccept=None', '-naccept' not in long_cmd)


def test_stale_handshake_reaped():
    """A slot claim that never completes mTLS shows up in stale_handshakes."""
    from server.session_manager import SessionManager
    mgr = SessionManager({'gateway': '10.8.0.1', 'max_clients': 2})
    a = mgr.welcome_payload()
    b = mgr.welcome_payload()
    mgr.get(b['id']).mark_connected()
    time.sleep(0.2)
    stale = [s.client_id for s in mgr.stale_handshakes(0.1)]
    test('unconnected slot claim is stale after timeout', stale == [a['id']])
    test('connected session is never stale', b['id'] not in stale)


def test_validate_config_rejects_garbage():
    from common.framing import validate_config
    good = {'id': 1, 'ip': '10.8.0.2', 'gateway': '10.8.0.1', 'dns': '10.8.0.1',
            'port': 14431, 'subnet': '10.8.0.1/24'}
    test('validate_config accepts a real push', validate_config(dict(good)) == good)
    bad_cases = [
        ('missing key', {k: v for k, v in good.items() if k != 'dns'}),
        ('bad ip', {**good, 'ip': 'not-an-ip'}),
        ('bad dns', {**good, 'dns': '10.8.0.1,evil.example'}),
        ('bad port', {**good, 'port': '14431'}),
        ('bad subnet', {**good, 'subnet': '10.8.0.1/99'}),
        ('not a dict', ['ip']),
    ]
    rejected = []
    for name, cfg in bad_cases:
        try:
            validate_config(cfg)
        except ValueError:
            rejected.append(name)
    test('validate_config rejects malformed pushes',
         rejected == [n for n, _ in bad_cases])


def test_config_push_over_tls_real():
    """Real s_server/s_client: a frame written to s_server stdin BEFORE any
    client connects is delivered as the first application record after mTLS,
    and s_server (-naccept 1) exits once that client hangs up."""
    from common.framing import pack_config
    from client.vpn_client import read_pushed_config

    port = _free_port()
    cfg = {'id': 7, 'ip': '10.8.0.3', 'gateway': '10.8.0.1', 'dns': '10.8.0.1',
           'port': port, 'subnet': '10.8.0.1/24'}
    server = start_tls_server('127.0.0.1', port, SERVER_CERT, SERVER_KEY, CA, mode='classical')
    server.stdin.write(pack_config(cfg))
    server.stdin.flush()
    got = None
    client = None
    try:
        for _ in range(10):
            client = start_tls_client('127.0.0.1', port, CLIENT_CERT, CLIENT_KEY, CA, mode='classical')
            try:
                got, _rest = read_pushed_config(client.stdout.fileno(), timeout=3.0)
                break
            except RuntimeError:
                client.terminate()
                client.wait()
                if 'Connection refused' not in client.stderr.read().decode(errors='replace'):
                    raise
                time.sleep(0.2)
        test('config-push delivered as first frame over real TLS', got == cfg)
        client.terminate()
        client.wait()
        try:
            server.wait(timeout=3)
            exited = True
        except subprocess.TimeoutExpired:
            exited = False
        test('s_server exits after its single client disconnects (-naccept 1)', exited)
    finally:
        for p in (client, server):
            if p is not None and p.poll() is None:
                p.kill()


def test_welcome_send_failure_releases_slot():
    """handle_client must free the slot (and kill its s_server) when the
    welcome peer is already gone, instead of leaking it via an escaped
    exception."""
    import socket as pysocket
    from server.session_manager import SessionManager
    from server import vpn_server

    port_base = _free_port()
    mgr = SessionManager({'gateway': '10.8.0.1', 'dns': '10.8.0.1',
                          'tun_net': '10.8.0.0/24', 'max_clients': 3,
                          'data_port_base': port_base})
    config = {'host': '127.0.0.1'}

    class Status:
        tls_pid = 0
        bytes_in = 0
        bytes_out = 0

    class DeadConn:
        """Looks like an accepted socket whose peer has reset."""
        closed = 0

        def sendall(self, _data):
            raise ConnectionResetError(104, 'Connection reset by peer')

        def close(self):
            self.closed += 1

    conn = DeadConn()
    t = threading.Thread(
        target=vpn_server.handle_client,
        args=(conn, mgr, config, Status(), CERT_DIR, 'classical', 'X25519MLKEM768', -1),
        daemon=True)
    t.start()
    t.join(timeout=8)
    test('handle_client returns (no escaped exception)', not t.is_alive())
    test('slot released after failed welcome send', mgr.snapshot() == [])
    # FIFO release: the freed slot sits behind the two untouched ones, so
    # three welcomes must succeed and together cover exactly the 3 ports.
    ports = {w['port'] for w in (mgr.welcome_payload() for _ in range(3)) if w}
    test('freed slot is reusable (3 welcomes cover all 3 ports)',
         ports == {port_base, port_base + 1, port_base + 2})
    test('welcome socket closed', conn.closed >= 1)
    # The per-client s_server bound to port_base must be gone too.
    probe = pysocket.socket(pysocket.AF_INET, pysocket.SOCK_STREAM)
    probe.settimeout(0.5)
    try:
        probe.connect(('127.0.0.1', port_base))
        still_listening = True
    except OSError:
        still_listening = False
    finally:
        probe.close()
    test('orphaned s_server terminated', not still_listening)



def test_welcome_limiter():
    """Per-source token bucket: burst, then deny; refills at rate; sources independent."""
    from server.session_manager import WelcomeLimiter
    lim = WelcomeLimiter(rate=1.0, burst=3)
    t = 1000.0
    first = [lim.allow('10.0.0.9', now=t) for _ in range(4)]
    test('burst of 3 allowed, 4th denied', first == [True, True, True, False])
    test('other source unaffected', lim.allow('10.0.0.10', now=t))
    test('still denied before refill', not lim.allow('10.0.0.9', now=t + 0.5))
    test('one token refilled after 1s at rate 1/s', lim.allow('10.0.0.9', now=t + 1.5))
    test('and denied again immediately', not lim.allow('10.0.0.9', now=t + 1.5))
    # Legit client pattern: reconnect backoff 1,2,4,8... never trips the limit.
    ok_backoff = all(lim.allow('10.0.0.42', now=t + d) for d in (0, 1, 3, 7, 15, 31))
    test('reconnect backoff cadence never rate-limited', ok_backoff)
    # Prune: idle entries are dropped once the table grows past 256.
    big = WelcomeLimiter(rate=1.0, burst=1, idle_expiry=60.0)
    for i in range(300):
        big.allow(f'192.0.2.{i // 256}.{i % 256}', now=t)
    big.allow('198.51.100.1', now=t + 120)  # triggers prune of 60s-idle entries
    test('idle sources pruned (table bounded)', len(big) <= 2)


def test_session_uptime_tracking():
    from common.daemon_utils import StatusTracker
    st = StatusTracker({'mode': 'classical'}, tun_name='tun0', tun_addr='pending')
    test('no session uptime before connect', st.snapshot()['session_seconds'] == 0)
    st.mark_session_start()
    st.session_start -= 5  # pretend 5s have passed
    test('session uptime counts from connect', st.snapshot()['session_seconds'] >= 5)
    st.reset_session()
    test('reset_session clears session uptime (daemon uptime untouched)',
         st.snapshot()['session_seconds'] == 0 and st.snapshot()['uptime_seconds'] >= 0)



def test_crl_flags_in_wrapper():
    """start_tls_server adds -CRL/-crl_check/-verify_return_error only when the file exists."""
    import tempfile
    import common.openssl_wrapper as ow
    calls = []

    class FakeProc:
        pid = 0

    orig = ow.subprocess.Popen
    ow.subprocess.Popen = lambda cmd, **kw: (calls.append(cmd), FakeProc())[1]
    try:
        with tempfile.NamedTemporaryFile(suffix='.pem') as crl:
            ow.start_tls_server('127.0.0.1', 1, 'c', 'k', 'ca', crl_path=crl.name)
        ow.start_tls_server('127.0.0.1', 1, 'c', 'k', 'ca', crl_path='/nonexistent/crl.pem')
        ow.start_tls_server('127.0.0.1', 1, 'c', 'k', 'ca')
    finally:
        ow.subprocess.Popen = orig
    with_crl, missing, none = calls
    test('CRL flags present when the file exists',
         '-CRL' in with_crl and '-crl_check' in with_crl and '-verify_return_error' in with_crl)
    test('CRL flags absent for a missing file / no crl_path',
         '-CRL' not in missing and '-CRL' not in none)


def test_hello_frame_and_disconnect_by_name():
    """Client hello frame names the session; disconnect_by_name targets it."""
    from common.framing import pack_hello, hello_name, unpack_frames, pack_keepalive
    from server.session_manager import SessionManager

    frames = list(unpack_frames(pack_hello('alice') + pack_keepalive()))
    test('hello frame round-trips the name',
         hello_name(*frames[0]) == 'alice' and hello_name(*frames[1]) is None)

    mgr = SessionManager({'gateway': '10.8.0.1', 'max_clients': 3})
    a = mgr.welcome_payload(); b = mgr.welcome_payload(); c = mgr.welcome_payload()
    mgr.get(a['id']).name = 'alice'
    mgr.get(b['id']).name = 'bob'
    mgr.get(c['id']).name = 'alice'   # alice reconnected twice: both go
    ids = mgr.disconnect_by_name('alice')
    test('disconnect_by_name stops every session with that name',
         sorted(ids) == sorted([a['id'], c['id']])
         and mgr.get(a['id']).stop.is_set() and mgr.get(c['id']).stop.is_set()
         and not mgr.get(b['id']).stop.is_set())
    test('unknown name disconnects nothing', mgr.disconnect_by_name('mallory') == [])
    test("snapshot carries the name", [x['name'] for x in mgr.snapshot()] == ['alice', 'bob', 'alice'])


def test_revoked_client_rejected_real():
    """Real PKI + real s_server: issue two clients, revoke one, and only the
    other can complete the handshake and deliver a frame."""
    import shutil
    import tempfile
    from common.framing import pack_frame, unpack_frames

    pki_dir = os.path.join(PROJECT_DIR, 'pki')
    with tempfile.TemporaryDirectory() as tmp:
        # Private copy of the PKI scripts so the laptop's real CA DB is untouched.
        work = os.path.join(tmp, 'pki')
        shutil.copytree(pki_dir, work, ignore=shutil.ignore_patterns('certs'))
        env = dict(os.environ)
        run = lambda *a: subprocess.run(list(a), capture_output=True, text=True, env=env, cwd=tmp)
        r = run('bash', os.path.join(work, 'gen_classical_ca.sh'))
        assert r.returncode == 0, r.stderr[-400:]
        for n in ('good', 'bad'):
            r = run('bash', os.path.join(work, 'issue_client.sh'), 'classical', n)
            assert r.returncode == 0, r.stderr[-400:]
        r = run('bash', os.path.join(work, 'revoke_client.sh'), 'classical', 'bad')
        assert r.returncode == 0, r.stderr[-400:]
        cd = os.path.join(work, 'certs', 'classical')
        crl = os.path.join(cd, 'crl.pem')
        test('revoke_client.sh regenerated the CRL with one serial',
             os.path.isfile(crl) and 'Serial Number' in run('openssl', 'crl', '-in', crl, '-noout', '-text').stdout)

        def attempt(name, port):
            server = start_tls_server('127.0.0.1', port, f'{cd}/server.crt', f'{cd}/server.key',
                                      f'{cd}/ca.crt', mode='classical', crl_path=crl)
            try:
                client = None
                cerr = ''
                for _ in range(10):
                    client = subprocess.Popen(
                        ['openssl', 's_client', '-connect', f'127.0.0.1:{port}',
                         '-cert', f'{cd}/clients/{name}.crt', '-key', f'{cd}/clients/{name}.key',
                         '-CAfile', f'{cd}/ca.crt', '-quiet'],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    time.sleep(0.4)
                    if client.poll() is None:
                        break
                    # A revoked client exits on the alert within this window;
                    # keep its stderr — it is the evidence we assert on.
                    cerr = client.stderr.read().decode(errors='replace')
                    if 'Connection refused' not in cerr:
                        break
                    time.sleep(0.2)
                try:
                    client.stdin.write(pack_frame(b'payload-from-' + name.encode()))
                    client.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
                time.sleep(1.0)
                client.terminate()
                cerr += client.stderr.read().decode(errors='replace')
                try:
                    server.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    server.kill()
                got = server.stdout.read()
                return cerr, list(unpack_frames(got))
            finally:
                if server.poll() is None:
                    server.kill()

        cerr, frames = attempt('bad', _free_port())
        test('revoked client gets alert 44 and delivers nothing',
             'certificate revoked' in cerr and frames == [])
        cerr, frames = attempt('good', _free_port())
        test('unrevoked client still relays through the same CRL',
             frames == [(0, b'payload-from-good')] and 'revoked' not in cerr)


if __name__ == '__main__':
    print('=== PQ-VPN Common Module Tests ===')
    print()
    test_imports()
    test_framing()
    test_control_frames()
    test_keepalive_frame()
    test_config_push()
    test_tun_import()
    test_session_manager_pool()
    test_welcome_handshake()
    test_phase4_welcome_to_relay()
    test_tls_relay()
    test_tls_relay_fd()
    test_parse_brief_line()
    test_drain_stderr_resumed_evidence()
    test_status_negotiated_lifecycle()
    test_ipc_server_socket_permissions()
    test_ipc_server_survives_caller_hangup()
    test_crl_expiry_warning()
    test_crl_next_update_parses_openssl_format()
    test_profile_resolution()
    test_wait_until_connected()
    test_vpnctl_eacces_handling()
    test_pool_pressure_reclaim()
    test_ipc_shutdown_command()
    test_kill_by_role_eacces_honest()
    test_port_recycling_across_reconnects()
    test_pool_honours_tun_net()
    test_naccept_flag()
    test_stale_handshake_reaped()
    test_validate_config_rejects_garbage()
    test_config_push_over_tls_real()
    test_welcome_send_failure_releases_slot()
    test_welcome_limiter()
    test_session_uptime_tracking()
    test_crl_flags_in_wrapper()
    test_hello_frame_and_disconnect_by_name()
    test_revoked_client_rejected_real()
    print()
    total = PASS + FAIL
    print(f'{PASS}/{total} passed' + (' ✅' if FAIL == 0 else ' ❌'))
    sys.exit(0 if FAIL == 0 else 1)
