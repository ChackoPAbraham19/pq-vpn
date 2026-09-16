import os
import sys
import time
import threading
import contextlib
import io

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

PASS = 0
FAIL = 0


def test(name: str, ok: bool, detail: str = ''):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f'  ✓ {name}')
    else:
        FAIL += 1
        print(f'  ✗ {name}')
        if detail:
            print(f'    {detail}')


def test_backoff_growth():
    from client.vpn_client import next_backoff
    seq = [1.0]
    for _ in range(6):
        seq.append(next_backoff(seq[-1], cap=30.0))
    ok = seq == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
    test('exponential backoff doubles then caps at 30s', ok, str(seq))


def test_session_idle_detection():
    from server.session_manager import SessionManager
    mgr = SessionManager({'gateway': '10.8.0.1', 'dns': '10.8.0.1', 'max_clients': 3})
    cfg = mgr.welcome_payload()
    assert cfg is not None
    sess = mgr.get(cfg['id'])
    # fresh session is not idle
    ok_fresh = mgr.idle_sessions(timeout=15) == []
    # simulate stale: force last_seen far in the past
    with sess._lock:
        sess.last_seen -= 60
    idle = mgr.idle_sessions(timeout=15)
    ok_stale = len(idle) == 1 and idle[0].client_id == sess.client_id
    test('server detects idle session for teardown', ok_fresh and ok_stale)


def test_session_touch_resets_idle():
    from server.session_manager import SessionManager
    mgr = SessionManager({'gateway': '10.8.0.1', 'dns': '10.8.0.1', 'max_clients': 3})
    sess = mgr.get(mgr.welcome_payload()['id'])
    with sess._lock:
        sess.last_seen -= 60
    sess.touch()
    ok = mgr.idle_sessions(timeout=15) == []
    test('session.touch() resets idle tracking', ok)


def test_keepalive_does_not_teardown_idle():
    """A keepalive frame arriving keeps last_seen fresh (survives the monitor)."""
    from server.session_manager import SessionManager
    mgr = SessionManager({'gateway': '10.8.0.1', 'dns': '10.8.0.1', 'max_clients': 3})
    sess = mgr.get(mgr.welcome_payload()['id'])
    # simulate keepalives every 0.05s for 0.5s; nothing should exceed the 15s idle cap
    for _ in range(10):
        sess.touch()
        time.sleep(0.05)
    ok = mgr.idle_sessions(timeout=15) == []
    test('keepalive touch prevents idle teardown', ok)


def test_keepalive_writes_over_pipe():
    """keepalive_loop emits a keepalive control frame on the given fd."""
    from common.framing import unpack_frames, TYPE_CONTROL
    import client.vpn_client as vc

    r, w = os.pipe()
    stop = threading.Event()
    t = threading.Thread(target=vc.keepalive_loop, args=(w, 0.1, stop), daemon=True)
    t.start()
    time.sleep(0.35)
    stop.set()
    t.join(timeout=2)

    data = b''
    import fcntl
    fl = fcntl.fcntl(r, fcntl.F_GETFL)
    fcntl.fcntl(r, fcntl.F_SETFL, fl | os.O_NONBLOCK)
    try:
        while True:
            chunk = os.read(r, 4096)
            if not chunk:
                break
            data += chunk
    except BlockingIOError:
        pass

    frames = list(unpack_frames(data))
    ok = len(frames) >= 2 and all(ft == TYPE_CONTROL for ft, _ in frames)
    test('keepalive_loop emits control frames periodically', ok,
         f'{len(frames)} frames: {frames}')


def test_relay_exits_on_session_stop():
    """relay_tun_to_tls on an IDLE tun must exit when the per-session stop is
    set (TLS side died) even though the global stop is clear. Before the fix
    it waited for a TUN packet to trip a write error — possibly forever."""
    import client.vpn_client as vc

    class Status:
        bytes_out = 0

    tun_r, tun_w = os.pipe()      # never written: idle tunnel
    tls_r, tls_w = os.pipe()
    stop = threading.Event()
    sess_stop = threading.Event()
    t = threading.Thread(target=vc.relay_tun_to_tls,
                         args=(tun_r, tls_w, stop, Status(), sess_stop), daemon=True)
    t.start()
    time.sleep(0.3)
    still_running = t.is_alive()
    sess_stop.set()
    t.join(timeout=1.5)
    test('idle TUN relay exits on session stop (global stop clear)',
         still_running and not t.is_alive() and not stop.is_set())
    for fd in (tun_r, tun_w, tls_r, tls_w):
        os.close(fd)


def test_keepalive_broken_pipe_sets_session_stop():
    """A keepalive write into a dead TLS stdin flags sess_stop."""
    import client.vpn_client as vc

    r, w = os.pipe()
    os.close(r)                    # peer gone: writes raise EPIPE
    stop = threading.Event()
    sess_stop = threading.Event()
    t = threading.Thread(target=vc.keepalive_loop, args=(w, 0.05, stop, sess_stop), daemon=True)
    t.start()
    t.join(timeout=2)
    test('keepalive BrokenPipe sets session stop and returns',
         not t.is_alive() and sess_stop.is_set() and not stop.is_set())
    os.close(w)


if __name__ == '__main__':
    print('=== PQ-VPN Phase 2 (keepalive + reconnect) Tests ===')
    print()
    test_backoff_growth()
    test_session_idle_detection()
    test_session_touch_resets_idle()
    test_keepalive_does_not_teardown_idle()
    test_keepalive_writes_over_pipe()
    test_relay_exits_on_session_stop()
    test_keepalive_broken_pipe_sets_session_stop()
    print()
    total = PASS + FAIL
    print(f'{PASS}/{total} passed' + (' ✅' if FAIL == 0 else ' ❌'))
    sys.exit(0 if FAIL == 0 else 1)