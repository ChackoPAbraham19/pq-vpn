#!/usr/bin/env python3
import os
import sys
import json
import time
import signal
import socket
import subprocess

import click

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from common.daemon_utils import (socket_path, pid_path, read_pid_file,
                                 discover_sockets, instance_label, ipc_request)


def _detect_mode(config_path) -> str:
    import yaml
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    return 'client' if 'server_host' in cfg else 'server'


def _detect_instance(config_path) -> str | None:
    import yaml
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    return cfg.get('instance')


def _daemon_script(mode: str) -> str:
    return os.path.join(PROJECT_DIR, mode, f'vpn_{mode}.py')


# Profiles, in the order `up` tries them when given no name. The VPS comes
# first because that is the real-network path; the netns configs are left out
# deliberately — they need scripts/setup_routing.sh and belong to demo.sh.
PROFILE_ORDER = ('vps', 'pqc', '')


def profile_config_name(profile: str) -> str:
    """Config basename for a profile name. '' is the plain client/config.yaml.

    Both daemons reduce --config to its basename and look inside their own
    directory (client/vpn_client.py), so a profile is just a name, never a path.
    """
    return 'config.yaml' if not profile else f'config-{profile}.yaml'


def resolve_profile(profile: str | None, exists) -> tuple[str, str] | None:
    """(profile, config path) for an explicit or default profile, or None.

    `exists` is injected so this is testable without a filesystem.
    """
    candidates = (profile,) if profile is not None else PROFILE_ORDER
    for cand in candidates:
        path = os.path.join(PROJECT_DIR, 'client', profile_config_name(cand))
        if exists(path):
            return cand, path
    return None


def _daemon_log(role: str, instance: str | None = None) -> str:
    suffix = f'-{instance}' if instance else ''
    return f'/tmp/pqvpn-{role}{suffix}.log'


def _log_tail(path: str, lines: int = 12) -> str:
    """Last few lines of a daemon log, for printing under an error."""
    try:
        with open(path, errors='replace') as fh:
            tail = fh.read().splitlines()[-lines:]
    except OSError:
        return ''
    return '\n'.join('    ' + line for line in tail if line.strip())


def _check_running(role: str, instance: str | None = None) -> int | None:
    pid = read_pid_file(role, instance)
    if pid and os.path.exists(f'/proc/{pid}'):
        return pid
    if pid:
        try:
            os.unlink(pid_path(role, instance))
        except FileNotFoundError:
            pass
    return None


def _spawn_daemon(mode: str, config_path: str):
    instance = _detect_instance(config_path)
    sp = socket_path(mode, instance)
    if os.path.exists(sp):
        label = f'{mode}[{instance}]' if instance else mode
        click.echo(f'Error: {label} already running ({sp} exists).', err=True)
        sys.exit(1)
    running_pid = _check_running(mode, instance)
    if running_pid:
        label = f'{mode}[{instance}]' if instance else mode
        click.echo(f'Error: {label} already running (pid={running_pid}).', err=True)
        sys.exit(1)

    script = _daemon_script(mode)
    if not os.path.exists(script):
        click.echo(f'Error: daemon script not found: {script}', err=True)
        sys.exit(1)

    abs_config = os.path.abspath(config_path)

    # The daemon's own output is the only explanation of a failed handshake
    # (bad CA, expired CRL, no TUN permission). Sending it to DEVNULL meant a
    # daemon that opened its socket and then died still reported success, with
    # nothing anywhere to say why. Keep it in a file and show it on failure.
    log_path = _daemon_log(mode, instance)
    try:
        log = open(log_path, 'w')
    except OSError:
        log = subprocess.DEVNULL

    # PYTHONUNBUFFERED: writing to a file makes Python block-buffer stdout, so
    # without this the log stays empty for minutes — empty at exactly the
    # moment something has gone wrong and it is the only evidence there is.
    proc = subprocess.Popen(
        [sys.executable, script, '--config', abs_config],
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        cwd=PROJECT_DIR,
        env=dict(os.environ, PYTHONUNBUFFERED='1'),
    )
    if log is not subprocess.DEVNULL:
        log.close()

    def _fail(msg: str):
        click.echo(f'Error: {msg}', err=True)
        tail = _log_tail(log_path)
        if tail:
            click.echo(f'  last lines of {log_path}:', err=True)
            click.echo(tail, err=True)
        else:
            click.echo(f'  see {log_path}', err=True)
        sys.exit(1)

    for _ in range(30):
        if os.path.exists(sp):
            break
        if proc.poll() is not None:
            _fail(f'{mode} daemon exited before opening its IPC socket.')
        time.sleep(0.2)
    else:
        proc.terminate()
        proc.wait()
        _fail(f'{mode} daemon did not start IPC socket within 6s.')

    if proc.poll() is not None:
        _fail(f'{mode} daemon exited prematurely.')

    click.echo(f'{mode.capitalize()} daemon started (pid={proc.pid})')
    return proc, sp, log_path


def _read_status_sock(sp: str) -> dict | None:
    """Read a status snapshot from an IPC socket path.

    Returns None when the socket is missing/unreadable. Raises PermissionError
    for root-owned sockets so callers can say *that* instead of lying that
    nothing is running.
    """
    if not os.path.exists(sp):
        return None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        sock.connect(sp)
        data = sock.recv(4096)
        sock.close()
        return json.loads(data.decode().strip())
    except PermissionError:
        raise
    except (socket.error, json.JSONDecodeError):
        return None


def _read_status(role: str, instance: str | None = None) -> dict | None:
    return _read_status_sock(socket_path(role, instance))


def _kill_by_role(role: str, instance: str | None = None) -> bool:
    """Stop one daemon. Returns True if a shutdown was requested.

    Prefers the IPC 'shutdown' command: the socket is 0666, so this works
    for non-root callers even when the daemon runs as root, and it runs the
    daemon's clean teardown (e.g. the server's NAT cleanup) which a SIGKILL
    would skip. Falls back to signals, reporting permission errors honestly
    instead of crashing.
    """
    try:
        reply = ipc_request(role, json.dumps({'cmd': 'shutdown'}), instance)
    except (OSError, ValueError):
        reply = None
    # The reply wraps the command handler's dict under 'result'
    # (see IpcServer: {'status': ..., 'result': handler(command)}).
    inner = reply.get('result') if isinstance(reply, dict) else None
    if isinstance(inner, dict) and inner.get('ok'):
        time.sleep(0.5)  # let the daemon run its teardown and exit
        for p in [socket_path(role, instance), pid_path(role, instance)]:
            try:
                os.unlink(p)
            except (FileNotFoundError, PermissionError):
                pass
        return True

    pid = read_pid_file(role, instance)
    if not pid:
        pid = _check_running(role, instance)
    if not pid:
        return False

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError:
        label = f'{role}[{instance}]' if instance else role
        click.echo(f'{label.capitalize()}: root-owned daemon — re-run with sudo',
                   err=True)
        return False

    for _ in range(15):
        if not os.path.exists(f'/proc/{pid}'):
            break
        time.sleep(0.2)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            label = f'{role}[{instance}]' if instance else role
            click.echo(f'{label.capitalize()}: root-owned daemon — re-run with sudo',
                       err=True)
            return False

    for p in [socket_path(role, instance), pid_path(role, instance)]:
        try:
            os.unlink(p)
        except (FileNotFoundError, PermissionError):
            pass
    return True


def _fmt_bytes(b: int) -> str:
    return f'{b:,}' if b >= 10000 else str(b)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.group()
def cli():
    pass


def wait_until_connected(read_status, is_alive, timeout: float = 25.0,
                         interval: float = 0.3, now=time.monotonic,
                         sleep=time.sleep) -> tuple[str, dict | None]:
    """Block until the tunnel is actually usable.

    Returns (outcome, last status) where outcome is 'connected', 'died' or
    'timeout'. The daemon opens its IPC socket early, long before routes and
    DNS are applied, so waiting on the socket alone returns while traffic is
    still leaving unprotected. The state only becomes 'connected' once the
    handshake, the pushed config, the TUN device and the routing are all done.

    Callables are injected so tests need neither a daemon nor the clock.
    """
    deadline = now() + timeout
    last = None
    while now() < deadline:
        status = read_status()
        if status:
            last = status
            if status.get('state') == 'connected':
                return 'connected', status
        if not is_alive():
            return 'died', last
        sleep(interval)
    return 'timeout', last


def _public_ip(timeout: float = 8.0) -> str | None:
    """Best-effort public IP, for the one line that proves a VPN is a VPN."""
    try:
        out = subprocess.run(['curl', '-s', '--max-time', str(int(timeout)),
                              'ifconfig.me'], capture_output=True, text=True)
    except OSError:
        return None
    ip = out.stdout.strip()
    return ip if ip and len(ip) <= 45 else None


@cli.command()
@click.argument('profile', required=False)
@click.option('--no-ip-check', is_flag=True, help='Skip the public-IP lookup')
def up(profile, no_ip_check):
    """Connect, and wait until routing is actually live.

    PROFILE names a config in client/ (vps -> client/config-vps.yaml).
    With no profile, the first of vps, pqc, config.yaml that exists is used.
    """
    found = resolve_profile(profile, os.path.exists)
    if not found:
        if profile is None:
            click.echo('Error: no client profile found in client/ '
                       f'(looked for {", ".join(profile_config_name(p) for p in PROFILE_ORDER)})',
                       err=True)
        else:
            click.echo(f'Error: no profile "{profile}" '
                       f'({os.path.join("client", profile_config_name(profile))} not found)',
                       err=True)
        sys.exit(1)
    name, config_path = found

    if os.geteuid() != 0:
        click.echo('Error: connecting needs root (TUN device, routes, DNS). '
                   'Run: pqvpn up', err=True)
        sys.exit(1)

    click.echo(f'Connecting [{name or "default"}] via {os.path.relpath(config_path, PROJECT_DIR)} ...')
    proc, sp, log_path = _spawn_daemon('client', config_path)

    outcome, status = wait_until_connected(
        read_status=lambda: _read_status_sock(sp),
        is_alive=lambda: proc.poll() is None,
    )

    if outcome != 'connected':
        why = ('the daemon exited' if outcome == 'died'
               else 'the tunnel did not come up in time')
        click.echo(f'Error: {why}.', err=True)
        tail = _log_tail(log_path)
        if tail:
            click.echo(f'  last lines of {log_path}:', err=True)
            click.echo(tail, err=True)
        else:
            click.echo(f'  see {log_path}', err=True)
        sys.exit(1)

    neg = (status or {}).get('negotiated') or {}
    click.echo('Connected.')
    click.echo(f'  Tunnel      {status.get("tun_name", "?")} — {status.get("tun_addr", "?")}')
    if neg:
        click.echo(f'  Negotiated  {" · ".join(f"{k}={v}" for k, v in sorted(neg.items()))}'
                   + (' (resumed)' if status.get('resumed') else ''))
    click.echo(f'  Mode        {status.get("mode", "?")} · {status.get("groups", "?")}')
    if not no_ip_check:
        ip = _public_ip()
        if ip:
            click.echo(f'  Public IP   {ip}  (traffic now leaves from here)')
    click.echo('Disconnect with: pqvpn down')


@cli.command()
@click.option('--config', '-c', required=True, help='Config file path')
def connect(config):
    """Auto-detect server or client mode from config and connect."""
    if not os.path.exists(config):
        click.echo(f'Error: config not found: {config}', err=True)
        sys.exit(1)
    mode = _detect_mode(config)
    _spawn_daemon(mode, config)


@cli.command()
@click.option('--config', '-c', required=True, help='Config file path')
def connect_server(config):
    """Start a VPN server daemon."""
    if not os.path.exists(config):
        click.echo(f'Error: config not found: {config}', err=True)
        sys.exit(1)
    _spawn_daemon('server', config)


@cli.command()
@click.option('--config', '-c', required=True, help='Config file path')
def connect_client(config):
    """Start a VPN client daemon."""
    if not os.path.exists(config):
        click.echo(f'Error: config not found: {config}', err=True)
        sys.exit(1)
    _spawn_daemon('client', config)


@cli.command()
def disconnect():
    """Disconnect the server and every client instance."""
    anything = False
    for role in ('server', 'client'):
        pids = [(None, read_pid_file(role))]
        if role == 'client':
            # also every -instance suffixed pid file
            for sp in discover_sockets(role):
                inst = instance_label(sp)
                if inst:
                    pids.append((inst, read_pid_file(role, inst)))
        seen = set()
        for inst, _ in pids:
            key = (role, inst)
            if key in seen:
                continue
            seen.add(key)
            if read_pid_file(role, inst) or _check_running(role, inst):
                label = f'{role}[{inst}]' if inst else role
                if _kill_by_role(role, inst):
                    click.echo(f'{label.capitalize()} stopped.')
                anything = True
    if not anything:
        click.echo('VPN is not running.')


@cli.command()
@click.option('--vps', is_flag=True,
              help="Show the deployed server's tunnels, over an SSH forward")
@click.option('--port', type=int, default=None, help='Local port (default 8080)')
@click.option('--token', default=None, help='Token for actions (default "demo")')
@click.option('--control', is_flag=True,
              help='Enable Connect/Disconnect in the page (needs root)')
def dashboard(vps, port, token, control):
    """Open the live web dashboard (http://127.0.0.1:8080).

    Runs unprivileged on purpose: the daemons' IPC sockets are mode 0666 so the
    dashboard can read status without root, and a web server has no business
    holding privileges it does not need.

    Delegates to scripts/dashboard.sh, which owns the port probing and the
    --vps SSH forward, so there is one implementation rather than two.
    """
    script = os.path.join(PROJECT_DIR, 'scripts', 'dashboard.sh')
    if not os.path.exists(script):
        click.echo(f'Error: {script} not found', err=True)
        sys.exit(1)
    env = dict(os.environ)
    if port is not None:
        env['PQVPN_DASH_PORT'] = str(port)
    if token is not None:
        env['PQVPN_DASH_TOKEN'] = token
    # --control is the one mode that genuinely needs root, so saying root is
    # unnecessary there contradicts the flag the user just passed.
    if os.geteuid() == 0 and env.get('SUDO_USER') and not control:
        click.echo('Note: running the dashboard as root is unnecessary '
                   '(status sockets are world-readable).', err=True)
    if control:
        if vps:
            click.echo('Error: --control is for the local dashboard; --vps shows a '
                       'remote server you cannot start tunnels on from here.', err=True)
            sys.exit(1)
        if os.geteuid() != 0:
            click.echo('Error: --control needs root. Run: pqvpn dashboard --control',
                       err=True)
            sys.exit(1)
        env['PQVPN_DASH_CONTROL'] = '1'
    args = ['bash', script] + (['--vps'] if vps else [])
    os.execvpe('bash', args, env)


@cli.command()
@click.pass_context
def down(ctx):
    """Disconnect and restore routes and DNS (alias for disconnect)."""
    ctx.invoke(disconnect)


def _print_status(label: str, s: dict):
    width = max(len(k) for k in s.keys()) + 2
    click.echo(f'{label}:')
    click.echo(f'  {"Mode":<{width}}{s.get("mode", "?")}')
    click.echo(f'  {"State":<{width}}{s.get("state", "?")}')
    neg = s.get('negotiated') or {}
    if neg:
        parts = [f'{k}={v}' for k, v in sorted(neg.items())]
        extra = ' (resumed)' if s.get('resumed') else ''
        click.echo(f'  {"Negotiated":<{width}}{" · ".join(parts)}{extra}')
    elif s.get('resumed'):
        click.echo(f'  {"Session":<{width}}resumed (ticket cache hit)')
    click.echo(f'  {"TUN":<{width}}{s.get("tun_name", "?")} — {s.get("tun_addr", "?")}')
    click.echo(f'  {"Groups":<{width}}{s.get("groups", "?")}')
    sess = s.get('session_seconds')
    if sess:
        click.echo(f'  {"Uptime":<{width}}{s.get("uptime_seconds", 0)}s (session {sess}s)')
    else:
        click.echo(f'  {"Uptime":<{width}}{s.get("uptime_seconds", 0)}s')
    click.echo(f'  {"Bytes in":<{width}}{_fmt_bytes(s.get("bytes_in", 0))}')
    click.echo(f'  {"Bytes out":<{width}}{_fmt_bytes(s.get("bytes_out", 0))}')
    clients = s.get('clients') or []
    if clients:
        click.echo(f'  {"Clients":<{width}}')
        for c in clients:
            name = c.get('name') or '-'
            click.echo(
                f'    # {c.get("id")}  {name:<10} {c.get("ip")}  '
                f'port {c.get("port")}  in {_fmt_bytes(c.get("bytes_in", 0))}  '
                f'out {_fmt_bytes(c.get("bytes_out", 0))}'
            )
    click.echo()


@cli.command('disconnect-client')
@click.argument('name')
def disconnect_client(name: str):
    """Force-disconnect the connected client(s) that announced NAME.

    Pair with `pki/revoke_client.sh <mode> <name>`: the CRL blocks the next
    handshake, this ends the session that is already up.
    """
    try:
        reply = ipc_request('server', json.dumps({'cmd': 'disconnect_client', 'name': name}))
    except (OSError, ValueError) as e:
        click.echo(f'Server not reachable ({e}).', err=True)
        sys.exit(1)
    inner = reply.get('result') if isinstance(reply, dict) else None
    if not isinstance(inner, dict) or not inner.get('ok'):
        click.echo((inner or {}).get('error', 'failed') if isinstance(inner, dict) else 'failed', err=True)
        sys.exit(1)
    click.echo(f'Disconnected {name!r}: session(s) {inner.get("client_ids")}')


@cli.command()
@click.argument('role', required=False, type=click.Choice(['server', 'client']))
def status(role: str | None):
    """Show VPN connection status for the server and every client instance."""
    try:
        server = _read_status('server')
    except PermissionError:
        server = 'denied'
    client_socks = discover_sockets('client')

    if server is None and not client_socks:
        click.echo('VPN is not running.')
        return

    if role in (None, 'server'):
        if isinstance(server, dict):
            _print_status('Server', server)
        elif server == 'denied':
            click.echo('Server: running (root-owned socket — re-run with sudo)')
        elif role == 'server':
            click.echo('Server: not running')

    if role == 'client' and not client_socks:
        click.echo('Client: not running')
    if role in (None, 'client'):
        for sp in client_socks:
            inst = instance_label(sp)
            label = f'Client[{inst}]' if inst else 'Client'
            try:
                s = _read_status_sock(sp)
            except PermissionError:
                click.echo(f'{label}: running (root-owned socket — re-run with sudo)')
                continue
            if not s:
                click.echo(f'{label}: not running')
                continue
            _print_status(label, s)


if __name__ == '__main__':
    cli()
