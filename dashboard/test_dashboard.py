import os
import sys
import json
import time
import threading
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

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


class isolated_sockets:
    """Point every IPC socket path at a private temp dir for the duration.

    Without this, tests that assert on "the sockets that exist" read the real
    /tmp/pqvpn-*.sock and fail whenever a genuine daemon happens to be running
    on the developer's machine — which is exactly when someone is least likely
    to suspect the test harness. `demo.sh --dry-run` runs these suites, so a
    live tunnel used to make it report failures.
    """

    def __enter__(self):
        import common.daemon_utils as du
        import dashboard.dashboard_server as ds
        self._du, self._ds = du, ds
        self._tmp = tempfile.TemporaryDirectory()
        root = self._tmp.name

        def sock(role, instance=None):
            name = f'pqvpn-{role}' + (f'-{instance}' if instance else '') + '.sock'
            return os.path.join(root, name)

        def pid(role, instance=None):
            name = f'pqvpn-{role}' + (f'-{instance}' if instance else '') + '.pid'
            return os.path.join(root, name)

        def discover(role):
            import glob as _glob
            paths = sorted(_glob.glob(os.path.join(root, f'pqvpn-{role}-*.sock')))
            legacy = sock(role)
            if os.path.exists(legacy):
                paths.insert(0, legacy)
            return paths

        self._saved = (du.socket_path, du.pid_path, ds.socket_path, ds.discover_sockets)
        du.socket_path, du.pid_path = sock, pid
        ds.socket_path, ds.discover_sockets = sock, discover
        return root

    def __exit__(self, *exc):
        du, ds = self._du, self._ds
        du.socket_path, du.pid_path, ds.socket_path, ds.discover_sockets = self._saved
        self._tmp.cleanup()
        return False


def test_ipc_command_envelope():
    """IpcServer: a command request returns {'status':..., 'result':...}; bare stays compatible."""
    from common.daemon_utils import IpcServer, StatusTracker, ipc_request, socket_path

    cfg = {'mode': 'classical', 'groups': 'X25519MLKEM768'}
    status = StatusTracker(cfg, tun_name='tun0', tun_addr='10.8.0.1/24')

    def handler(line):
        cmd = json.loads(line)
        if cmd.get('cmd') == 'ping':
            return {'ok': True, 'echo': cmd.get('msg')}
        return {'error': 'unknown'}

    ipc = IpcServer(status, role='dash_test', command_handler=handler)
    ipc.start()
    try:
        time.sleep(0.3)
        bare = ipc_request('dash_test')
        ok_bare = 'state' in bare and 'result' not in bare
        with_cmd = ipc_request('dash_test', command=json.dumps({'cmd': 'ping', 'msg': 'hi'}))
        ok_cmd = with_cmd.get('result', {}).get('echo') == 'hi' and 'status' in with_cmd
        test('bare request returns snapshot (vpnctl compat)', ok_bare, str(bare)[:80])
        test('command request returns status+result envelope', ok_cmd, str(with_cmd)[:120])
    finally:
        ipc.stop()
        time.sleep(0.2)
        import os as _os
        try:
            _os.remove(socket_path('dash_test'))
        except FileNotFoundError:
            pass


def test_fastapi_endpoints():
    from fastapi.testclient import TestClient
    import dashboard.dashboard_server as ds

    # Point the feed at a fake (missing) socket so snapshot() returns empties.
    ds.feed._interval = 0.05
    client = TestClient(ds.app)
    try:
        r = client.get('/api/status')
        ok_status = r.status_code == 200 and 'server' in r.json() and 'client' in r.json()
        test('GET /api/status returns JSON snapshot', ok_status, r.text[:120])

        r = client.get('/')
        ok_index = r.status_code == 200 and 'PQ-VPN Dashboard' in r.text
        test('GET / serves dashboard index.html', ok_index, f'status={r.status_code}')

        auth = {'X-PQVPN-Token': ds.DASH_TOKEN}

        r = client.post('/disconnect/1')
        test('POST /disconnect without token → 401', r.status_code == 401, f'status={r.status_code} {r.text[:80]}')
        r = client.post('/disconnect/1', headers={'X-PQVPN-Token': 'wrong'})
        test('POST /disconnect with wrong token → 401', r.status_code == 401, f'status={r.status_code}')

        r = client.post('/disconnect/1', headers=auth)
        ok_off = r.status_code == 503
        test('POST /disconnect (auth) when server down → 503', ok_off, f'status={r.status_code} {r.text[:80]}')

        r = client.post('/rekey')
        test('POST /rekey without token → 401', r.status_code == 401)
        r = client.post('/rekey', headers=auth)
        ok_rekey = r.status_code == 200 and r.json().get('ok') is False
        test('POST /rekey (auth) responds (unsupported)', ok_rekey, r.text[:80])

        r = client.get('/api/token')
        test('token is never served by a GET', r.status_code == 404, f'status={r.status_code}')
    finally:
        client.close()


def test_control_endpoints():
    """Connect/Disconnect: token-guarded, and refused unless running privileged.

    The dashboard runs unprivileged by default on purpose, so the control
    endpoints must refuse rather than half-work. vpnctl is faked here: this
    tests the guard and the plumbing, not the tunnel.
    """
    from fastapi.testclient import TestClient
    import dashboard.dashboard_server as ds

    client = TestClient(ds.app)
    auth = {'X-PQVPN-Token': ds.DASH_TOKEN}
    orig_control, orig_vpnctl = ds.CONTROL, ds._vpnctl
    try:
        # --- no token, whatever the mode
        ds.CONTROL = True
        test('POST /connect without token -> 401',
             client.post('/connect', json={}).status_code == 401)
        test('POST /down without token -> 401',
             client.post('/down').status_code == 401)
        test('POST /connect with wrong token -> 401',
             client.post('/connect', json={},
                         headers={'X-PQVPN-Token': 'wrong'}).status_code == 401)

        # --- read-only mode refuses even with the token
        ds.CONTROL = False
        r = client.post('/connect', json={'profile': 'vps'}, headers=auth)
        test('read-only mode refuses /connect with 403', r.status_code == 403,
             f'status={r.status_code}')
        test('and the refusal names how to enable it',
             'dashboard --control' in r.text, r.text[:120])
        test('read-only mode refuses /down with 403',
             client.post('/down', headers=auth).status_code == 403)

        # --- control mode, success
        calls = []
        ds.CONTROL = True
        ds._vpnctl = lambda *a, **k: (calls.append(a) or (True, 'Connected.'))
        r = client.post('/connect', json={'profile': 'vps'}, headers=auth)
        test('control mode accepts /connect', r.status_code == 200 and r.json().get('ok') is True,
             r.text[:120])
        test('the chosen profile is passed through to vpnctl',
             calls and calls[-1] == ('up', 'vps'), str(calls[-1:]))
        client.post('/connect', json={}, headers=auth)
        test('no profile means a bare `up` (CLI picks the default)',
             calls[-1] == ('up',), str(calls[-1:]))
        client.post('/down', headers=auth)
        test('/down calls vpnctl down', calls[-1] == ('down',), str(calls[-1:]))

        # --- control mode, failure surfaces the daemon's own words
        ds._vpnctl = lambda *a, **k: (False, 'verify error:num=19: self-signed certificate')
        r = client.post('/connect', json={'profile': 'vps'}, headers=auth)
        test('a failed connect returns 500', r.status_code == 500, f'status={r.status_code}')
        test('and surfaces the daemon error rather than a bare failure',
             'num=19' in r.text, r.text[:120])
    finally:
        ds.CONTROL, ds._vpnctl = orig_control, orig_vpnctl
        client.close()


def test_status_reports_control_mode():
    """The page cannot render the right controls without being told the mode."""
    from fastapi.testclient import TestClient
    import dashboard.dashboard_server as ds

    client = TestClient(ds.app)
    orig = ds.CONTROL
    try:
        ds.CONTROL = False
        body = client.get('/api/status').json()
        test('read-only status reports control=false', body.get('control') is False)
        test('and offers no profiles to pick from', body.get('profiles') == [])

        ds.CONTROL = True
        body = client.get('/api/status').json()
        test('control status reports control=true', body.get('control') is True)
        test('and lists profiles found in client/',
             isinstance(body.get('profiles'), list) and len(body['profiles']) > 0,
             str(body.get('profiles'))[:100])
    finally:
        ds.CONTROL = orig
        client.close()


def _page_script():
    """The inline <script> block of index.html, as text."""
    path = os.path.join(os.path.dirname(__file__), 'index.html')
    with open(path, encoding='utf-8') as fh:
        html = fh.read()
    start = html.index('<script>\n') + len('<script>\n')
    return html[start:html.index('</script>', start)]


def test_page_auth_wiring():
    """Every mutating fetch in the page must send the token header.

    This exists because of a bug that a fully green suite could not see. The
    page declared its header-building code inside disconnect() and the control
    panel called `headers()`, which was defined nowhere, so every Connect and
    Disconnect click threw a ReferenceError before fetch was reached -- the
    buttons were dead even when the dashboard was launched correctly with
    --control. Nothing here executed the page's JavaScript, and the only test
    touching index.html asserted that it is served, not that it works.

    A static check is enough to stop a recurrence: there must be exactly one
    definition of the helper, every POST must pass it, and the old misspelling
    must not come back.
    """
    js = _page_script()
    # Strip line comments: this file describes the bug in prose, and the prose
    # names the broken call.
    code = '\n'.join(line.split('//')[0] for line in js.splitlines())

    test('the page defines authHeaders() exactly once',
         code.count('function authHeaders') == 1,
         f'found {code.count("function authHeaders")}')

    # The call that broke: a helper that was never a function. Match only a
    # bare `headers(`, not `authHeaders(` or `foo.headers(`.
    import re as _re
    stray = _re.findall(r'(?<![\w.$])headers\s*\(', code)
    test('no call to the undefined headers()', not stray,
         'index.html calls headers(), which does not exist')

    # Each fetch( ... ) call site, trimmed to its own argument list.
    sites = [chunk[:chunk.find('})') + 2 if '})' in chunk else 400]
             for chunk in js.split('fetch(')[1:]]
    posts = [s for s in sites if "method: 'POST'" in s]
    test('the page has mutating fetch calls to check', len(posts) >= 2,
         f'found {len(posts)}')
    unguarded = [s for s in posts if 'authHeaders()' not in s]
    test('every POST sends the token header', not unguarded,
         f'{len(unguarded)} unguarded: ' + '; '.join(s[:70] for s in unguarded))


def test_token_embedded_for_loopback():
    """The token is embedded in the page locally, and withheld when exposed."""
    from fastapi.testclient import TestClient
    import dashboard.dashboard_server as ds

    client = TestClient(ds.app)
    orig_loop, orig_tok = ds.LOOPBACK, ds.DASH_TOKEN
    try:
        ds.DASH_TOKEN = 'embed-me-123'

        ds.LOOPBACK = True
        r = client.get('/')
        test('loopback: GET / embeds the live token',
             '<meta name="pqvpn-token" content="embed-me-123">' in r.text,
             r.text[:160])
        test('and tells the browser not to cache it',
             r.headers.get('cache-control') == 'no-store',
             str(r.headers.get('cache-control')))

        # Read per request, not captured at import: main() sets it after this
        # module is already loaded.
        ds.DASH_TOKEN = 'rotated-456'
        test('a rotated token is picked up without a restart',
             'rotated-456' in client.get('/').text)

        ds.LOOPBACK = False
        r = client.get('/')
        # The page always *mentions* pqvpn-token (it reads the meta tag), so
        # assert on the injected tag and on the secret itself, not the name.
        test('non-loopback: the token is NOT embedded',
             '<meta name="pqvpn-token"' not in r.text and 'rotated-456' not in r.text,
             r.text[:160])
        test('but the page is still served', r.status_code == 200 and 'PQ-VPN Dashboard' in r.text)
    finally:
        ds.LOOPBACK, ds.DASH_TOKEN = orig_loop, orig_tok
        client.close()


def test_profile_listing():
    """Profile names come from client/config*.yaml, excluding the examples.

    What is expected is derived from the files actually present, never named
    literally. The earlier version asserted that 'vps' was discovered, but
    client/config-vps.yaml is gitignored -- it holds a real public IP -- so it
    exists on a machine that has deployed to the VPS and in no clean checkout.
    That check passed for every developer and failed in CI on every push.
    """
    import glob
    import dashboard.dashboard_server as ds

    names = ds.available_profiles()
    client_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'client')
    on_disk = {os.path.basename(p)[len('config-'):-len('.yaml')]
               for p in glob.glob(os.path.join(client_dir, 'config-*.yaml'))
               if not p.endswith('.example.yaml')}

    test('the bare default is offered first', names and names[0] == '', str(names[:3]))
    # Equality both ways: nothing on disk is missed, nothing is invented. It
    # also covers "named profiles are discovered", since config-pqc.yaml is
    # tracked and so is in on_disk in every checkout.
    test('every config-*.yaml in client/ is offered, and only those',
         bool(on_disk) and set(names) - {''} == on_disk,
         f'offered={sorted(names)} on_disk={sorted(on_disk)}')
    test('example templates are not offered as profiles',
         not any('example' in n for n in names), str(names))


def test_ws_carries_control_mode():
    """The page learns the mode from the WebSocket, so the mode must be in it.

    The page opens /ws/status and never calls /api/status. When only the latter
    carried `control`, every frame arrived without it, `controlEnabled` fell to
    false on the first frame and Connect/Disconnect sat disabled for good --
    with `--control` passed and the server in control mode. Asserting on
    /api/status alone could not see that, because the page does not read it.
    """
    from fastapi.testclient import TestClient
    import dashboard.dashboard_server as ds

    ds.feed._interval = 0.05
    client = TestClient(ds.app)
    orig = ds.CONTROL
    try:
        ds.CONTROL = True
        with client.websocket_connect('/ws/status') as ws:
            frame = ws.receive_json()
        test('WebSocket frame says control is enabled', frame.get('control') is True,
             str(sorted(frame.keys()))[:160])
        test('WebSocket frame carries the profile list',
             isinstance(frame.get('profiles'), list) and len(frame['profiles']) > 0,
             str(frame.get('profiles'))[:100])
        test('and it agrees with /api/status',
             frame.get('profiles') == client.get('/api/status').json().get('profiles'))

        ds.CONTROL = False
        with client.websocket_connect('/ws/status') as ws:
            frame = ws.receive_json()
        test('a read-only server says so in the frame too', frame.get('control') is False)
        test('and offers no profiles there either', frame.get('profiles') == [])
    finally:
        ds.CONTROL = orig
        client.close()


def test_ws_streams_snapshots():
    """The WebSocket feed broadcasts snapshots to connected clients."""
    from fastapi.testclient import TestClient
    import dashboard.dashboard_server as ds

    ds.feed._interval = 0.05
    client = TestClient(ds.app)
    try:
        with client.websocket_connect('/ws/status') as ws:
            data = ws.receive_json()
            ok = 'server' in data and 'client' in data and 'timestamp' in data
            test('WebSocket delivers status snapshot', ok, str(data)[:100])
    finally:
        client.close()


def test_status_feed_change_detection():
    """StatusFeed precomputes a snapshot usable as the first WS payload."""
    import dashboard.dashboard_server as ds

    feed = ds.StatusFeed(interval=0.05)
    feed.start()
    time.sleep(0.2)
    feed.stop()
    feed._thread.join(timeout=2)

    ok = feed._last is not None and 'server' in feed._last and 'client' in feed._last
    test('feed precomputes a valid snapshot', ok, str(feed._last)[:100])


def test_feed_under_missing_sockets():
    """With no daemon sockets present, the feed must not crash."""
    import dashboard.dashboard_server as ds

    with isolated_sockets():
        feed = ds.StatusFeed(interval=0.05)
        feed.start()
        time.sleep(0.2)
        feed.stop()
        feed._thread.join(timeout=2)

        ok = (feed._last is not None and feed._last['server'] is None
              and feed._last['client'] is None)
        test('feed survives missing daemon sockets', ok, str(feed._last)[:100])


def test_multi_instance_ipc():
    """Two client instances get distinct sockets/pids; discovery sees both;
    each socket answers from its own tracker."""
    import os as _os
    from common.daemon_utils import (IpcServer, StatusTracker, ipc_request,
                                     socket_path, discover_sockets, instance_label)

    ipcs = []
    try:
        for inst, addr in (('multi_a', '10.8.0.2/24'), ('multi_b', '10.8.0.3/24')):
            st = StatusTracker({'mode': 'classical'}, tun_name='tun0', tun_addr=addr)
            ipc = IpcServer(st, role='client', instance=inst)
            ipc.start()
            ipcs.append((inst, addr, ipc))
        time.sleep(0.4)

        ok_paths = all(_os.path.exists(socket_path('client', inst)) for inst, _, _ in ipcs)
        test('per-instance sockets exist side by side', ok_paths)

        found = {instance_label(sp) for sp in discover_sockets('client')}
        ok_disc = {'multi_a', 'multi_b'} <= found
        test('discover_sockets finds both instances', ok_disc, str(found))

        answers = {}
        for inst, addr, _ in ipcs:
            snap = ipc_request('client', instance=inst)
            answers[inst] = snap.get('tun_addr')
        ok_iso = answers.get('multi_a') == '10.8.0.2/24' and answers.get('multi_b') == '10.8.0.3/24'
        test('each instance serves its own status', ok_iso, str(answers))

        # same process here -> same pid VALUE; what matters is distinct pid
        # FILES per instance, each holding a valid int
        from common.daemon_utils import read_pid_file, pid_path
        ok_pids = True
        for inst, _, _ in ipcs:
            if not _os.path.exists(pid_path('client', inst)):
                ok_pids = False
            if not isinstance(read_pid_file('client', inst), int):
                ok_pids = False
        test('pid files are per-instance', ok_pids)
    finally:
        for inst, _, ipc in ipcs:
            ipc.stop()
        time.sleep(0.2)
        for inst, _, _ in ipcs:
            try:
                _os.remove(socket_path('client', inst))
            except FileNotFoundError:
                pass


def test_feed_sees_client_instances():
    """StatusFeed surfaces every live client instance ('clients') while keeping
    'client' pointed at the first for UI compatibility."""
    import os as _os
    from common.daemon_utils import IpcServer, StatusTracker, socket_path
    import dashboard.dashboard_server as ds

    with isolated_sockets():
        st = StatusTracker({'mode': 'pqc'}, tun_name='tun0', tun_addr='10.8.0.4/24')
        ipc = IpcServer(st, role='client', instance='feedtest')
        ipc.start()
        try:
            time.sleep(0.3)
            snap = ds.StatusFeed(interval=0.05).snapshot()
            ok_list = len(snap.get('clients') or []) == 1 and \
                (snap['clients'][0].get('_instance') == 'feedtest')
            ok_compat = (snap.get('client') or {}).get('_instance') == 'feedtest'
            test("feed lists instance under 'clients' with _instance tag", ok_list, str(snap)[:140])
            test("'client' key mirrors first instance (UI compat)", ok_compat)
        finally:
            ipc.stop()
            time.sleep(0.2)


def test_single_poller():
    """Readers use the cached snapshot: 3 WS clients + 5 GETs cost ~0 extra
    IPC polls beyond what the background thread does on its interval."""
    from fastapi.testclient import TestClient
    import dashboard.dashboard_server as ds

    calls = {'n': 0}
    orig = ds.StatusFeed.snapshot

    def counting(self):
        calls['n'] += 1
        return orig(self)

    ds.StatusFeed.snapshot = counting
    ds.feed.interval = 10.0  # background thread polls once, then sleeps
    try:
        with TestClient(ds.app) as client:
            time.sleep(0.2)
            baseline = calls['n']
            for _ in range(5):
                client.get('/api/status')
            sockets = [client.websocket_connect('/ws/status').__enter__() for _ in range(3)]
            for ws in sockets:
                ws.receive_json()
            for ws in sockets:
                ws.__exit__(None, None, None)
            extra = calls['n'] - baseline
        test('GET/WS readers do not trigger IPC polls', extra == 0, f'extra polls={extra} baseline={baseline}')
        test('background poller ran at least once', baseline >= 1, f'baseline={baseline}')
    finally:
        ds.StatusFeed.snapshot = orig
        ds.feed.interval = ds.POLL_INTERVAL


def test_token_resolution():
    import dashboard.dashboard_server as ds
    os.environ['PQVPN_DASH_TOKEN'] = 'from-env'
    try:
        test('explicit --token wins', ds.resolve_token('cli') == 'cli')
        test('env token used when no flag', ds.resolve_token(None) == 'from-env')
    finally:
        del os.environ['PQVPN_DASH_TOKEN']
    a, b = ds.resolve_token(None), ds.resolve_token(None)
    test('random token otherwise (unique, long)', a != b and len(a) >= 24)
    test('token_ok rejects None/empty', not ds.token_ok(None) and not ds.token_ok(''))



if __name__ == '__main__':
    print('=== PQ-VPN Phase 5 (dashboard) Tests ===')
    print()
    test_ipc_command_envelope()
    test_multi_instance_ipc()
    test_fastapi_endpoints()
    test_control_endpoints()
    test_status_reports_control_mode()
    test_page_auth_wiring()
    test_token_embedded_for_loopback()
    test_profile_listing()
    test_ws_carries_control_mode()
    test_ws_streams_snapshots()
    test_status_feed_change_detection()
    test_feed_under_missing_sockets()
    test_feed_sees_client_instances()
    test_single_poller()
    test_token_resolution()
    print()
    total = PASS + FAIL
    print(f'{PASS}/{total} passed' + (' ✅' if FAIL == 0 else ' ❌'))
    sys.exit(0 if FAIL == 0 else 1)