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
    """End-to-end: welcome handshake -> assigned data port -> framed TLS relay."""
    import socket as pysocket
    import os as osmod
    import fcntl
    from common.framing import pack_config, unpack_config, pack_frame
    from server.session_manager import SessionManager

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
        sess = mgr.get(welcome['id'])
        from common.openssl_wrapper import start_tls_server
        sess.tls_proc = start_tls_server(
            '127.0.0.1', welcome['port'], SERVER_CERT, SERVER_KEY, CA, mode='classical')
        server_procs.append(sess.tls_proc)
        conn.sendall(pack_config(welcome))
        conn.close()
        from server.vpn_server import relay_tls_to_tun
        t = threading.Thread(
            target=relay_tls_to_tun, args=(sess.tls_proc.stdout.fileno(), tun_fd, sess, status), daemon=True)
        t.start()
        time.sleep(2)
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
        conn.close()

    def client_side():
        s = pysocket.create_connection(('127.0.0.1', welcome_port), timeout=3)
        buf = b''
        cfg = None
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
            cfg, _ = unpack_config(buf)
            if cfg:
                break
        s.close()
        results['client_cfg'] = cfg
        proc = None
        for _ in range(5):
            proc = start_tls_client(
                '127.0.0.1', cfg['port'], CLIENT_CERT, CLIENT_KEY, CA, mode='classical')
            deadline = time.time() + 1.0
            ok_started = True
            while time.time() < deadline:
                if proc.poll() is not None:
                    err = proc.stderr.read().decode(errors='replace').strip()
                    if 'Connection refused' in err:
                        ok_started = False
                        break
                    raise RuntimeError(f's_client exited: {err}')
                time.sleep(0.05)
            if ok_started:
                break
            proc.terminate()
            time.sleep(0.2)
        time.sleep(0.3)
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

    cfg = results.get('client_cfg') or {}
    ok = cfg.get('ip') == '10.8.0.2' and results.get('server_recv') == b'hello-over-framed-tls'
    test('Phase 4: welcome handshake -> assigned port -> framed relay', ok)
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
    """Server assigns IP+port via config-push control frame; client parses it."""
    import socket as pysocket
    import threading
    from common.framing import pack_config, unpack_config
    from server.session_manager import SessionManager

    manager = SessionManager({'gateway': '10.8.0.1'})
    welcome = manager.welcome_payload()
    assert welcome is not None, 'pool should have slots'
    assert welcome['ip'] == '10.8.0.2'
    assert welcome['port'] == 14431

    def serve(port):
        srv = pysocket.socket(pysocket.AF_INET, pysocket.SOCK_STREAM)
        srv.setsockopt(pysocket.SOL_SOCKET, pysocket.SO_REUSEADDR, 1)
        srv.bind(('127.0.0.1', port))
        srv.listen(1)
        conn, _ = srv.accept()
        conn.sendall(pack_config(welcome))
        conn.close()
        srv.close()

    port = 15430
    t = threading.Thread(target=serve, args=(port,), daemon=True)
    t.start()

    client_cfg = _welcome_handshake_client('127.0.0.1', port)
    t.join(timeout=3)
    ok = client_cfg == welcome
    test('welcome config-push handshake round-trip', ok)
    if not ok:
        print(f'    expected {welcome!r}')
        print(f'    received {client_cfg!r}')


def _welcome_handshake_client(host, port):
    import socket as pysocket
    from common.framing import unpack_config
    s = pysocket.create_connection((host, port), timeout=3)
    try:
        buf = b''
        while True:
            chunk = s.recv(4096)
            if not chunk:
                raise RuntimeError('connection closed before config')
            buf += chunk
            cfg, _ = unpack_config(buf)
            if cfg is not None:
                return cfg
    finally:
        s.close()


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


def test_vpnctl_eacces_handling():
    """vpnctl reports 'root-owned socket' on EACCES instead of 'not running'."""
    import socket
    import tempfile
    import cli.vpnctl as vp
    from cli.vpnctl import _read_status_sock

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
    test_vpnctl_eacces_handling()
    test_pool_pressure_reclaim()
    test_ipc_shutdown_command()
    test_kill_by_role_eacces_honest()
    print()
    total = PASS + FAIL
    print(f'{PASS}/{total} passed' + (' ✅' if FAIL == 0 else ' ❌'))
    sys.exit(0 if FAIL == 0 else 1)
