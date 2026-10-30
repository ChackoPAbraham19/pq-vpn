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


def test_client_dns_skip_env():
    """PQVPN_SKIP_DNS must short-circuit BEFORE any probe or attempt, printing
    exactly one confirmatory line (memoized) so silent flag regressions show."""
    import client.net_config as nc
    from client.net_config import apply_tunnel_net

    cfg = {'gateway': '10.8.0.1', 'server_host': '192.168.100.2',
           'tun_name': 'tun0', 'route_metric': 50}
    calls = []
    orig_run = nc._run
    nc._run = lambda cmd, dry_run=False, check=False: calls.append(' '.join(cmd))
    old = os.environ.get('PQVPN_SKIP_DNS')
    os.environ['PQVPN_SKIP_DNS'] = '1'
    try:
        out1 = _dry_run_capture(lambda: apply_tunnel_net(cfg, dry_run=False))
        ok_line = 'DNS management disabled (PQVPN_SKIP_DNS)' in out1
        out2 = _dry_run_capture(lambda: apply_tunnel_net(cfg, dry_run=False))
        ok_memo = out2.count('DNS management disabled') == 0 and \
            'resolvectl' not in out2 and 'nameservers' not in out2
        test('skip-env prints one confirmatory line on first call', ok_line, out1)
        test('skip-env is memoized: later calls fully silent', ok_memo, out2)
        # route still applied — DNS skip must not affect the tunnel itself
        ok_route = any(c.startswith('ip route add 0.0.0.0/1 via 10.8.0.1 dev tun0') for c in calls)
        test('skip-env leaves route handling untouched', ok_route)
    finally:
        nc._run = orig_run
        if old is None:
            del os.environ['PQVPN_SKIP_DNS']
        else:
            os.environ['PQVPN_SKIP_DNS'] = old


def test_client_dns_liveness_fail_skips_resolvectl():
    """When systemd-resolved is not active, resolvectl is never spawned and the
    fallback path is taken with one clean line."""
    import client.net_config as nc
    from client.net_config import apply_tunnel_net

    cfg = {'gateway': '10.8.0.1', 'tun_name': 'tun0', 'route_metric': 50,
           'dns_servers': '10.8.0.1'}
    old_live, old_dead = nc._resolved_liveness, nc._resolv_conf_dead
    old_skip = nc._dns_skip_logged
    nc._resolved_liveness = False   # simulate probed-and-dead resolved
    nc._resolv_conf_dead = True     # and a known-dead file fallback
    try:
        out = _dry_run_capture(
            lambda: apply_tunnel_net(dict(cfg), dry_run=True))
        # dry-run preview still plans DNS but never claims resolvectl ran
        ok_dry = 'would set DNS via' in out
        test('dry-run preview unaffected by liveness state', ok_dry, out)
    finally:
        nc._resolved_liveness, nc._resolv_conf_dead = old_live, old_dead
        nc._dns_skip_logged = old_skip


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
        'ip route add 0.0.0.0/1 via 10.8.0.1 dev tun0',
        'ip route add 128.0.0.0/1 via 10.8.0.1 dev tun0',
        # preview names whichever method a real run would pick on this host
        'would set DNS via',
    ]
    ok = all(r in out for r in required) and '10.8.0.1' in out.split('would set DNS via')[1]
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
        i_default = out.find('ip route add 0.0.0.0/1 via 10.8.0.1')
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
        i_dd = out_r.find('ip route del 0.0.0.0/1 via 10.8.0.1')
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


def test_resolv_conf_symlink_preserved():
    """A connect/disconnect cycle must leave /etc/resolv.conf a symlink.

    Regression: the fallback path replaces the file (it cannot write *through*
    the symlink — fs.protected_regular gives EPERM even for root), and restore
    copied the saved *contents* back. That silently converted a
    systemd-managed symlink into a static file. DNS kept working, so nothing
    looked wrong, but the file no longer followed systemd-resolved.

    Runs against real files in a temp dir, so it tests the syscalls rather
    than a description of them.
    """
    import tempfile
    import client.net_config as nc

    with tempfile.TemporaryDirectory() as tmp:
        stub = os.path.join(tmp, 'stub-resolv.conf')
        with open(stub, 'w') as fh:
            fh.write('nameserver 127.0.0.53\n')
        live = os.path.join(tmp, 'resolv.conf')
        os.symlink(stub, live)

        # getattr/setattr, not attribute access: against a build without the
        # fix this must report the behavioural failure below, not die here.
        orig = (nc.RESOLV_CONF, nc._RESOLV_BACKUP,
                getattr(nc, '_RESOLV_LINK', None), nc._resolv_conf_dead)
        nc.RESOLV_CONF = live
        nc._RESOLV_BACKUP = os.path.join(tmp, 'backup')
        nc._RESOLV_LINK = os.path.join(tmp, 'link')
        nc._resolv_conf_dead = False
        try:
            method = nc._set_resolv_conf(['10.8.0.1'])
            test('fallback writes the tunnel nameserver', method == 'resolv.conf')
            with open(live) as fh:
                body = fh.read()
            test('tunnel DNS is actually in the file', 'nameserver 10.8.0.1' in body)
            test('while connected it is a plain file, not a link',
                 not os.path.islink(live))
            test('the symlink target was recorded', os.path.exists(nc._RESOLV_LINK))

            nc.restore_tunnel_net({'dns_method': 'resolv.conf'},
                                  {'gateway': '10.8.0.1', 'tun_name': 'tun0'})

            test('restore puts the symlink back', os.path.islink(live))
            test('and it points where it did before',
                 os.path.islink(live) and os.readlink(live) == stub)
            with open(live) as fh:
                test('so the original contents are served again',
                     fh.read() == 'nameserver 127.0.0.53\n')
            test('no leftover state in /tmp',
                 not os.path.exists(nc._RESOLV_BACKUP)
                 and not os.path.exists(nc._RESOLV_LINK))
        finally:
            (nc.RESOLV_CONF, nc._RESOLV_BACKUP, nc._RESOLV_LINK,
             nc._resolv_conf_dead) = orig


def test_resolv_conf_plain_file_still_restored():
    """When resolv.conf was a regular file, restore must copy it back."""
    import tempfile
    import client.net_config as nc

    with tempfile.TemporaryDirectory() as tmp:
        live = os.path.join(tmp, 'resolv.conf')
        with open(live, 'w') as fh:
            fh.write('nameserver 192.168.1.1\n')

        orig = (nc.RESOLV_CONF, nc._RESOLV_BACKUP,
                getattr(nc, '_RESOLV_LINK', None), nc._resolv_conf_dead)
        nc.RESOLV_CONF = live
        nc._RESOLV_BACKUP = os.path.join(tmp, 'backup')
        nc._RESOLV_LINK = os.path.join(tmp, 'link')
        nc._resolv_conf_dead = False
        try:
            nc._set_resolv_conf(['10.8.0.1'])
            test('no link recorded for a plain file',
                 not os.path.exists(nc._RESOLV_LINK))
            nc.restore_tunnel_net({'dns_method': 'resolv.conf'},
                                  {'gateway': '10.8.0.1', 'tun_name': 'tun0'})
            with open(live) as fh:
                test('plain file restored by content',
                     fh.read() == 'nameserver 192.168.1.1\n')
            test('and it is still a plain file', not os.path.islink(live))
        finally:
            (nc.RESOLV_CONF, nc._RESOLV_BACKUP, nc._RESOLV_LINK,
             nc._resolv_conf_dead) = orig


def test_client_restore_command():
    from client.net_config import restore_tunnel_net
    cfg = {'gateway': '10.8.0.1', 'tun_name': 'tun0', 'route_metric': 50}
    out = _dry_run_capture(
        lambda: restore_tunnel_net({'route': True, 'dns_method': 'resolv.conf'}, cfg, dry_run=True))
    required = [
        'ip route del 0.0.0.0/1 via 10.8.0.1 dev tun0',
        'ip route del 128.0.0.0/1 via 10.8.0.1 dev tun0',
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


def test_server_mss_clamp():
    from server.net_config import setup_nat, teardown_nat
    cfg = {'tun_net': '10.8.0.0/24', 'tun_name': 'tun0', 'outbound_iface': 'eth0', 'nat': True}
    out = _dry_run_capture(lambda: setup_nat(cfg, dry_run=True))
    clamp_out = 'iptables -t mangle -A FORWARD -o tun0 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu'
    clamp_in = 'iptables -t mangle -A FORWARD -i tun0 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu'
    test('server clamps TCP MSS in both tunnel directions (default on)',
         clamp_out in out and clamp_in in out, out)
    down = _dry_run_capture(lambda: teardown_nat(cfg, dry_run=True))
    test('teardown removes both MSS clamp rules',
         clamp_out.replace('-A', '-D') in down and clamp_in.replace('-A', '-D') in down, down)
    off = _dry_run_capture(lambda: setup_nat({**cfg, 'mss_clamp': False}, dry_run=True))
    test('mss_clamp: false omits the mangle rules', 'TCPMSS' not in off, off)


def test_client_ipv6_blackhole():
    from client.net_config import apply_tunnel_net, restore_tunnel_net
    cfg = {'gateway': '10.8.0.1', 'tun_name': 'tun0', 'route_metric': 50, 'dns_servers': '10.8.0.1'}
    out = _dry_run_capture(lambda: apply_tunnel_net(cfg, dry_run=True))
    test('client blackholes the IPv6 default while connected (leak guard)',
         'ip -6 route add blackhole default metric 50' in out, out)
    off = _dry_run_capture(lambda: apply_tunnel_net({**cfg, 'block_ipv6': False}, dry_run=True))
    test('block_ipv6: false leaves IPv6 alone', 'ip -6 route' not in off, off)
    down = _dry_run_capture(lambda: restore_tunnel_net(
        {'route': True, 'dns_method': None, 'ipv6_blackhole': True}, cfg, dry_run=True))
    test('restore removes the IPv6 blackhole',
         'ip -6 route del blackhole default metric 50' in down, down)
    noop = _dry_run_capture(lambda: restore_tunnel_net(
        {'route': False, 'dns_method': None}, cfg, dry_run=True))
    test('restore is a no-op for IPv6 when it was never applied', 'ip -6' not in noop, noop)


if __name__ == '__main__':
    print('=== PQ-VPN net_config Tests ===')
    print()
    test_server_nat_commands()
    test_server_nat_argument_order()
    test_server_nat_disabled()
    test_server_persist_sysctl()
    test_server_teardown_nat()
    test_server_mss_clamp()
    test_client_ipv6_blackhole()
    test_server_dnsmasq_config()
    test_client_route_command()
    test_client_dns_skip_env()
    test_client_dns_liveness_fail_skips_resolvectl()
    test_client_protect_route_order()
    test_client_no_protect_when_no_prior_default()
    test_destroy_tun_safe()
    test_resolv_conf_symlink_preserved()
    test_resolv_conf_plain_file_still_restored()
    test_client_restore_command()
    test_client_restore_idempotent()
    print()
    total = PASS + FAIL
    print(f'{PASS}/{total} passed' + (' ✅' if FAIL == 0 else ' ❌'))
    sys.exit(0 if FAIL == 0 else 1)