#!/usr/bin/env python3
"""net_config.py — client-side network plumbing for the PQ-VPN.

After the tunnel is up, pushes a default route through tun0 at metric 50 and
sets DNS via `resolvectl` (systemd) or /etc/resolv.conf fallback. Tracks what
it changed so teardown can restore the prior state.

All commands can be printed instead of executed with dry_run=True so the
behaviour is testable without root.
"""
import os
import shutil
import subprocess
import sys

RESOLV_CONF = '/etc/resolv.conf'
_RESOLV_BACKUP = '/tmp/pqvpn.client.resolv.conf.bak'

# Per-process DNS outcome memos: a dead path is probed at most ONCE, then
# remembered silently. Ordering that callers must respect:
#   1. PQVPN_SKIP_DNS env   2. resolved liveness   3. file fallback (memoized)
_dns_skip_logged = False
_resolved_liveness: bool | None = None
_resolv_conf_dead = False


def _dns_skip_requested() -> bool:
    return os.environ.get('PQVPN_SKIP_DNS', '').strip().lower() in ('1', 'true', 'yes')


def _resolved_active() -> bool:
    """True only if resolvectl exists AND systemd-resolved is actually running.

    Memoized per process — netns harnesses never have the unit, and re-probing
    on every reconnect is pure waste.
    """
    global _resolved_liveness
    if _resolved_liveness is None:
        try:
            live = subprocess.run(
                ['systemctl', 'is-active', '--quiet', 'systemd-resolved'],
                capture_output=True,
            ).returncode == 0
        except FileNotFoundError:
            live = False
        _resolved_liveness = bool(live and shutil.which('resolvectl'))
    return _resolved_liveness


def _run(cmd, dry_run=False, check=False):
    if dry_run:
        print(f'[net_config] would run: {" ".join(cmd)}')
        return None
    try:
        return subprocess.run(cmd, check=check, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f'[net_config] command failed ({e}): {" ".join(cmd)}', file=sys.stderr)
        if e.stderr:
            sys.stderr.write(e.stderr.decode(errors='replace'))
        raise


def _route_exists(gateway, tun_name, metric, dry_run=False) -> bool:
    if dry_run:
        return False
    try:
        out = subprocess.run(
            ['ip', 'route', 'show', 'default', 'via', gateway, 'dev', tun_name,
             'metric', str(metric)],
            capture_output=True, text=True,
        )
        return gateway in out.stdout and tun_name in out.stdout
    except FileNotFoundError:
        return False


def _has_resolvectl() -> bool:
    return shutil.which('resolvectl') is not None


def current_default_route(dry_run=False) -> tuple[str, str] | None:
    """Return (gateway, dev) of the current default route, or None.

    Read BEFORE any tunnel routes are pushed; used both to pin a /32
    transport route for the VPN server endpoint and by tests (monkeypatchable).
    """
    if dry_run:
        return ('198.51.100.1', 'eth0')  # plausible placeholder for dry-run output
    try:
        out = subprocess.run(
            ['ip', 'route', 'show', 'default'],
            capture_output=True, text=True,
        )
        # e.g. "default via 192.168.200.1 dev veth-ns2 metric 100"
        for line in out.stdout.splitlines():
            parts = line.split()
            if 'via' in parts and 'dev' in parts:
                gw = parts[parts.index('via') + 1]
                dev = parts[parts.index('dev') + 1]
                return gw, dev
    except FileNotFoundError:
        pass
    return None


def _ipv6_enabled() -> bool:
    try:
        with open('/proc/sys/net/ipv6/conf/all/disable_ipv6') as f:
            return f.read().strip() == '0'
    except OSError:
        return False


def apply_tunnel_net(config: dict, dry_run=False) -> dict:
    """Push default route + DNS. Returns a snapshot of applied state."""
    applied: dict = {'route': False, 'dns_method': None, 'protect': None,
                     'ipv6_blackhole': False}
    gateway = config.get('gateway', '10.8.0.1')
    tun_name = config.get('tun_name', 'tun0')
    metric = config.get('route_metric', 50)
    dns_servers = config.get('dns_servers', '10.8.0.1').split(',')
    server_host = str(config.get('server_host', ''))

    # Transport-route protection: once `default via <tun>` lands, the TLS
    # connection to the server itself would be routed INTO the tunnel and
    # blackholed (Linux re-routes per packet). Pin the endpoint to the
    # pre-existing default first.
    orig = current_default_route(dry_run=dry_run)
    if orig and server_host and server_host != gateway:
        orig_gw, orig_dev = orig
        _run(['ip', 'route', 'add', f'{server_host}/32',
              'via', orig_gw, 'dev', orig_dev], dry_run=dry_run, check=True)
        applied['protect'] = {'host': server_host, 'via': orig_gw, 'dev': orig_dev}

    if not _route_exists(gateway, tun_name, metric, dry_run):
        _run(['ip', 'route', 'add', 'default', 'via', gateway, 'dev', tun_name,
              'metric', str(metric)], dry_run=dry_run, check=True)
        applied['route'] = True

    # The tunnel carries IPv4 only. On a host with IPv6 connectivity, v6-capable
    # sites would be reached OUTSIDE the tunnel with the real address — the
    # classic VPN leak. Blackhole the v6 default at a better metric than any
    # RA-installed route while connected; restore_tunnel_net removes it.
    if config.get('block_ipv6', True) and (dry_run or _ipv6_enabled()):
        _run(['ip', '-6', 'route', 'add', 'blackhole', 'default', 'metric', str(metric)],
             dry_run=dry_run, check=False)
        applied['ipv6_blackhole'] = True

    # --- DNS, in strict precedence order: env skip -> resolved liveness ->
    # file fallback (memoized). Each dead path is probed once per process,
    # then remembered silently.
    global _dns_skip_logged
    if _dns_skip_requested():
        applied['dns_method'] = None
        if not _dns_skip_logged:
            print('[net_config] DNS management disabled (PQVPN_SKIP_DNS)')
            _dns_skip_logged = True
        return applied

    if dry_run:
        # Preview both paths without spawning anything.
        print(f'[net_config] would set DNS via '
              f'{"resolvectl" if _has_resolvectl() else "resolv.conf"}: {", ".join(dns_servers)}')
        applied['dns_method'] = 'resolvectl' if _has_resolvectl() else 'resolv.conf'
        return applied

    if _resolved_active():
        try:
            _run(['resolvectl', 'dns', tun_name] + dns_servers, check=True)
            _run(['resolvectl', 'domain', tun_name, '~.'], check=True)
            applied['dns_method'] = 'resolvectl'
            return applied
        except subprocess.CalledProcessError:
            pass  # fall through to the file fallback
    elif _resolved_liveness is False and not _dns_skip_logged:
        # Liveness already probed and failed: one clean line, no attempt.
        print('[net_config] systemd-resolved not active; using resolv.conf fallback')

    applied['dns_method'] = _set_resolv_conf(dns_servers)
    return applied


def _set_resolv_conf(dns_servers, dry_run=False) -> str | None:
    """resolv.conf fallback: snapshot existing file, then write nameservers.

    Uses temp-file + atomic replace: /etc/resolv.conf is often a symlink into
    /run/systemd/resolve/, and direct writes there fail with EPERM even for
    root (fs.protected_regular). Replacing the path itself avoids that trap.
    """
    if dry_run:
        print('[net_config] would write nameservers to /etc/resolv.conf '
              f'({", ".join(dns_servers)})')
        return 'resolv.conf'
    global _resolv_conf_dead
    if _resolv_conf_dead:
        return None  # already failed once this process; stay silent
    if not os.path.exists(RESOLV_CONF):
        return None
    if not os.path.exists(_RESOLV_BACKUP):
        try:
            shutil.copy2(RESOLV_CONF, _RESOLV_BACKUP)
        except OSError as e:
            _resolv_conf_dead = True
            print(f'[net_config] resolv.conf backup failed, DNS left untouched ({e})',
                  file=sys.stderr)
            return None
    content = ''.join(f'nameserver {d}\n' for d in dns_servers)
    tmp = RESOLV_CONF + '.pqvpn.tmp'
    try:
        with open(tmp, 'w') as f:
            f.write(content)
        os.replace(tmp, RESOLV_CONF)
        return 'resolv.conf'
    except OSError as e:
        _resolv_conf_dead = True  # one clean line per process, ever
        print(f'[net_config] DNS not applied (resolv.conf write blocked: {e}); '
              'tunnel still usable by IP', file=sys.stderr)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return None


def restore_tunnel_net(applied: dict, config: dict, dry_run=False):
    """Undo route + DNS changes made by apply_tunnel_net."""
    gateway = config.get('gateway', '10.8.0.1')
    tun_name = config.get('tun_name', 'tun0')
    metric = config.get('route_metric', 50)

    # Remove the transport protection route BEFORE the tunnel default, so the
    # endpoint never rides a half-torn-down state.
    prot = applied.get('protect')
    if prot:
        _run(['ip', 'route', 'del', f"{prot['host']}/32",
              'via', prot['via'], 'dev', prot['dev']], dry_run=dry_run, check=False)

    if applied.get('route'):
        _run(['ip', 'route', 'del', 'default', 'via', gateway, 'dev', tun_name,
              'metric', str(metric)], dry_run=dry_run, check=False)

    if applied.get('ipv6_blackhole'):
        _run(['ip', '-6', 'route', 'del', 'blackhole', 'default', 'metric', str(metric)],
             dry_run=dry_run, check=False)

    if applied.get('dns_method') == 'resolvectl' and _has_resolvectl():
        _run(['resolvectl', 'revert', tun_name], dry_run=dry_run, check=False)
    elif applied.get('dns_method') == 'resolv.conf':
        if dry_run:
            print(f'[net_config] would restore {RESOLV_CONF} from {_RESOLV_BACKUP}')
        else:
            if os.path.exists(_RESOLV_BACKUP):
                shutil.copy2(_RESOLV_BACKUP, RESOLV_CONF)
                try:
                    os.remove(_RESOLV_BACKUP)
                except OSError:
                    pass