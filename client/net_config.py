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


def apply_tunnel_net(config: dict, dry_run=False) -> dict:
    """Push default route + DNS. Returns a snapshot of applied state."""
    applied: dict = {'route': False, 'dns_method': None, 'protect': None}
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

    if _has_resolvectl() and not dry_run:
        try:
            _run(['resolvectl', 'dns', tun_name] + dns_servers, dry_run=dry_run, check=True)
            _run(['resolvectl', 'domain', tun_name, '~.'], dry_run=dry_run, check=True)
            applied['dns_method'] = 'resolvectl'
        except subprocess.CalledProcessError:
            applied['dns_method'] = _set_resolv_conf(dns_servers, dry_run=dry_run)
    else:
        applied['dns_method'] = _set_resolv_conf(dns_servers, dry_run=dry_run)

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
    if not os.path.exists(RESOLV_CONF):
        return None
    if not os.path.exists(_RESOLV_BACKUP):
        try:
            shutil.copy2(RESOLV_CONF, _RESOLV_BACKUP)
        except OSError as e:
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