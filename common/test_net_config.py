import io
import os
import sys
import contextlib

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


def _dry_run_capture(fn):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn()
    return buf.getvalue()


def test_server_nat_commands():
    from server.net_config import setup_nat
    cfg = {'tun_net': '10.8.0.0/24', 'tun_name': 'tun0', 'outbound_iface': 'eth0', 'nat': True}
    out = _dry_run_capture(lambda: setup_nat(cfg, dry_run=True))
    required = [
        'sysctl -w net.ipv4.ip_forward=1',
        # iptables requires: -t <table> -A <chain> (flag hugs chain name)
        'iptables -t nat -A POSTROUTING -s 10.8.0.0/24 -o eth0 -j MASQUERADE',
        'iptables -A FORWARD -i tun0 -o eth0 -j ACCEPT',
        'iptables -A FORWARD -i eth0 -o tun0 -j ACCEPT',
    ]
    ok = all(r in out for r in required)
    test('server NAT emits forward+NAT+MASQUERADE rules', ok, out)


def test_server_nat_argument_order():
    """Regression guard for the live netns failure: an action flag followed by
    '-t' is always invalid iptables usage. The dry-run tests once asserted
    exactly that broken ordering and locked the bug in."""
    from server.net_config import setup_nat, teardown_nat
    cfg = {'tun_net': '10.8.0.0/24', 'outbound_iface': 'eth0', 'nat': True}
    out = _dry_run_capture(lambda: setup_nat(cfg, dry_run=True)) + \
          _dry_run_capture(lambda: teardown_nat(cfg, dry_run=True))
    ok = '-A -t' not in out and '-D -t' not in out and '-C -t' not in out
    test('no action-flag-before-table ordering anywhere', ok, out)


def test_server_nat_disabled():
    from server.net_config import setup_nat
    out = _dry_run_capture(lambda: setup_nat({'nat': False}, dry_run=True))
    test('server NAT skipped when nat=false', 'iptables' not in out, out)


def test_server_persist_sysctl():
    from server.net_config import setup_nat
    cfg = {'nat': True, 'persist_sysctl': True, 'outbound_iface': 'eth0'}
    out = _dry_run_capture(lambda: setup_nat(cfg, dry_run=True))
    test('server NAT persists sysctl when requested',
         '/etc/sysctl.d/99-pqvpn.conf' in out, out)


def test_server_teardown_nat():
    from server.net_config import teardown_nat
    cfg = {'tun_net': '10.8.0.0/24', 'tun_name': 'tun0', 'outbound_iface': 'eth0', 'nat': True}
    out = _dry_run_capture(lambda: teardown_nat(cfg, dry_run=True))
    required = [
        'iptables -t nat -D POSTROUTING -s 10.8.0.0/24 -o eth0 -j MASQUERADE',
        'iptables -D FORWARD -i tun0 -o eth0 -j ACCEPT',
        'iptables -D FORWARD -i eth0 -o tun0 -j ACCEPT',
    ]
    ok = all(r in out for r in required)
    test('server NAT teardown removes same rules', ok, out)


def test_server_dnsmasq_config():
    from server.net_config import start_dnsmasq
    out = _dry_run_capture(lambda: start_dnsmasq({'dnsmasq': True}, dry_run=True))
    test('server dnsmasq gated by config', 'dnsmasq on tun0' in out, out)


def test_client_route_command():
    from client.net_config import apply_tunnel_net
    cfg = {'gateway': '10.8.0.1', 'tun_name': 'tun0', 'route_metric': 50,
           'dns_servers': '10.8.0.1'}
    out = _dry_run_capture(lambda: apply_tunnel_net(cfg, dry_run=True))
    required = [
        'ip route add default via 10.8.0.1 dev tun0 metric 50',
        'nameservers to /etc/resolv.conf (10.8.0.1)',
    ]
    ok = all(r in out for r in required)
    test('client applies default route + DNS', ok, out)


def test_client_protect_route_order():
    """Regression guard for the live failure: the TLS connection to the server
    must be pinned via a /32 host route through the ORIGINAL gateway BEFORE
    the tunnel default lands, otherwise the tunnel routes itself blackhole."""
    import client.net_config as nc
    from client.net_config import apply_tunnel_net

    orig = nc.current_default_route
    nc.current_default_route = lambda dry_run=False: ('192.168.200.1', 'veth-ns2')
    try:
        cfg = {'gateway': '10.8.0.1', 'tun_name': 'tun0', 'route_metric': 50,
               'dns_servers': '10.8.0.1', 'server_host': '192.168.100.2'}
        out = _dry_run_capture(lambda: apply_tunnel_net(cfg, dry_run=True))
        protect = 'ip route add 192.168.100.2/32 via 192.168.200.1 dev veth-ns2'
        ok_present = protect in out
        i_protect = out.find(protect)
        i_default = out.find('ip route add default via 10.8.0.1')
        ok_order = i_protect != -1 and i_default != -1 and i_protect < i_default
        test('protect /32 route emitted for server endpoint', ok_present, out)
        test('protect route comes BEFORE tunnel default', ok_order,
             f'protect@{i_protect} default@{i_default}')

        applied_snap = None
        def capture_apply():
            nonlocal applied_snap
            applied_snap = apply_tunnel_net(cfg, dry_run=True)
        _dry_run_capture(capture_apply)
        from client.net_config import restore_tunnel_net
        out_r = _dry_run_capture(lambda: restore_tunnel_net(applied_snap, cfg, dry_run=True))
        del_protect = 'ip route del 192.168.100.2/32 via 192.168.200.1 dev veth-ns2'
        i_dp = out_r.find(del_protect)
        i_dd = out_r.find('ip route del default via 10.8.0.1')
        ok_restore = i_dp != -1 and i_dd != -1 and i_dp < i_dd
        test('restore deletes protect route before tunnel default', ok_restore, out_r)
    finally:
        nc.current_default_route = orig


def test_client_no_protect_when_no_prior_default():
    """Without an existing default (server on-link), no protect route is added."""
    import client.net_config as nc
    from client.net_config import apply_tunnel_net

    orig = nc.current_default_route
    nc.current_default_route = lambda dry_run=False: None
    try:
        cfg = {'gateway': '10.8.0.1', 'server_host': '192.168.100.2',
               'tun_name': 'tun0', 'route_metric': 50}
        out = _dry_run_capture(lambda: apply_tunnel_net(cfg, dry_run=True))
        ok = '/32' not in out
        test('no protect route when no prior default exists', ok, out)
    finally:
        nc.current_default_route = orig


def test_destroy_tun_safe():
    """destroy_tun never raises on missing devices/fds (idempotent teardown)."""
    from common.tun_utils import destroy_tun
    try:
        destroy_tun('pqvpn_test_nonexistent')
        destroy_tun('pqvpn_test_nonexistent', fd=999999)
        ok = True
    except Exception as e:
        ok = False
        print(f'    unexpected: {e}')
    test('destroy_tun is safe on stale/missing devices', ok)


def test_client_restore_command():
    from client.net_config import restore_tunnel_net
    cfg = {'gateway': '10.8.0.1', 'tun_name': 'tun0', 'route_metric': 50}
    out = _dry_run_capture(
        lambda: restore_tunnel_net({'route': True, 'dns_method': 'resolv.conf'}, cfg, dry_run=True))
    required = [
        'ip route del default via 10.8.0.1 dev tun0 metric 50',
        'would restore /etc/resolv.conf',
    ]
    ok = all(r in out for r in required)
    test('client restores route + DNS', ok, out)


def test_client_restore_idempotent():
    from client.net_config import restore_tunnel_net
    cfg = {'gateway': '10.8.0.1', 'tun_name': 'tun0', 'route_metric': 50}
    out = _dry_run_capture(
        lambda: restore_tunnel_net({'route': False, 'dns_method': None}, cfg, dry_run=True))
    ok = 'ip route del' not in out and 'restore' not in out
    test('client restore no-op when nothing applied', ok, out)


if __name__ == '__main__':
    print('=== PQ-VPN net_config Tests ===')
    print()
    test_server_nat_commands()
    test_server_nat_argument_order()
    test_server_nat_disabled()
    test_server_persist_sysctl()
    test_server_teardown_nat()
    test_server_dnsmasq_config()
    test_client_route_command()
    test_client_protect_route_order()
    test_client_no_protect_when_no_prior_default()
    test_destroy_tun_safe()
    test_client_restore_command()
    test_client_restore_idempotent()
    print()
    total = PASS + FAIL
    print(f'{PASS}/{total} passed' + (' ✅' if FAIL == 0 else ' ❌'))
    sys.exit(0 if FAIL == 0 else 1)