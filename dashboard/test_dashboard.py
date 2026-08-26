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

        r = client.post('/disconnect/1')
        ok_off = r.status_code == 503
        test('POST /disconnect when server down → 503', ok_off, f'status={r.status_code} {r.text[:80]}')

        r = client.post('/rekey')
        ok_rekey = r.status_code == 200 and r.json().get('ok') is False
        test('POST /rekey responds (unsupported)', ok_rekey, r.text[:80])
    finally:
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

    feed = ds.StatusFeed(interval=0.05)
    feed.start()
    time.sleep(0.2)
    feed.stop()
    feed._thread.join(timeout=2)

    ok = feed._last is not None and feed._last['server'] is None and feed._last['client'] is None
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
        try:
            _os.remove(socket_path('client', 'feedtest'))
        except FileNotFoundError:
            pass


if __name__ == '__main__':
    print('=== PQ-VPN Phase 5 (dashboard) Tests ===')
    print()
    test_ipc_command_envelope()
    test_multi_instance_ipc()
    test_fastapi_endpoints()
    test_ws_streams_snapshots()
    test_status_feed_change_detection()
    test_feed_under_missing_sockets()
    test_feed_sees_client_instances()
    print()
    total = PASS + FAIL
    print(f'{PASS}/{total} passed' + (' ✅' if FAIL == 0 else ' ❌'))
    sys.exit(0 if FAIL == 0 else 1)