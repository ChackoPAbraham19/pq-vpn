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

    proc = subprocess.Popen(
        [sys.executable, script, '--config', abs_config],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        cwd=PROJECT_DIR,
    )

    for _ in range(30):
        if os.path.exists(sp):
            break
        time.sleep(0.2)
    else:
        click.echo(f'Error: {mode} daemon did not start IPC socket within 6s.', err=True)
        proc.terminate()
        proc.wait()
        sys.exit(1)

    if proc.poll() is not None:
        click.echo(f'Error: {mode} daemon exited prematurely.', err=True)
        sys.exit(1)

    click.echo(f'{mode.capitalize()} daemon started (pid={proc.pid})')


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
    click.echo(f'  {"Uptime":<{width}}{s.get("uptime_seconds", 0)}s')
    click.echo(f'  {"Bytes in":<{width}}{_fmt_bytes(s.get("bytes_in", 0))}')
    click.echo(f'  {"Bytes out":<{width}}{_fmt_bytes(s.get("bytes_out", 0))}')
    clients = s.get('clients') or []
    if clients:
        click.echo(f'  {"Clients":<{width}}')
        for c in clients:
            click.echo(
                f'    # {c.get("id")}  {c.get("ip")}  '
                f'port {c.get("port")}  in {_fmt_bytes(c.get("bytes_in", 0))}  '
                f'out {_fmt_bytes(c.get("bytes_out", 0))}'
            )
    click.echo()


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
