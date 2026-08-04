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

from common.daemon_utils import socket_path, pid_path, read_pid_file


def _detect_mode(config_path) -> str:
    import yaml
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    return 'client' if 'server_host' in cfg else 'server'


def _daemon_script(mode: str) -> str:
    return os.path.join(PROJECT_DIR, mode, f'vpn_{mode}.py')


def _check_running(role: str) -> int | None:
    pid = read_pid_file(role)
    if pid and os.path.exists(f'/proc/{pid}'):
        return pid
    if pid:
        try:
            os.unlink(pid_path(role))
        except FileNotFoundError:
            pass
    return None


def _spawn_daemon(mode: str, config_path: str):
    sp = socket_path(mode)
    if os.path.exists(sp):
        click.echo(f'Error: {mode} already running ({sp} exists).', err=True)
        sys.exit(1)
    running_pid = _check_running(mode)
    if running_pid:
        click.echo(f'Error: {mode} already running (pid={running_pid}).', err=True)
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


def _read_status(role: str) -> dict | None:
    sp = socket_path(role)
    if not os.path.exists(sp):
        return None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        sock.connect(sp)
        data = sock.recv(4096)
        sock.close()
        return json.loads(data.decode().strip())
    except (socket.error, json.JSONDecodeError):
        return None


def _kill_by_role(role: str):
    pid = read_pid_file(role)
    if not pid:
        pid = _check_running(role)
    if not pid:
        return

    try:
        os.kill(pid, signal.SIGTERM)
    except (PermissionError, ProcessLookupError):
        pass

    for _ in range(15):
        if not os.path.exists(f'/proc/{pid}'):
            break
        time.sleep(0.2)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    for p in [socket_path(role), pid_path(role)]:
        try:
            os.unlink(p)
        except FileNotFoundError:
            pass


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
    """Disconnect both server and client VPN daemons."""
    anything = False
    for role in ('server', 'client'):
        pid = read_pid_file(role)
        if pid or _check_running(role):
            _kill_by_role(role)
            click.echo(f'{role.capitalize()} stopped.')
            anything = True
    if not anything:
        click.echo('VPN is not running.')


@cli.command()
def status():
    """Show VPN connection status for server and client."""
    server = _read_status('server')
    client = _read_status('client')

    if not server and not client:
        click.echo('VPN is not running.')
        return

    width = max(len(k) for k in (server or client or {}).keys()) + 2

    for role, s in [('Server', server), ('Client', client)]:
        if not s:
            click.echo(f'{role}: not running')
            continue
        click.echo(f'{role}:')
        click.echo(f'  {"Mode":<{width}}{s.get("mode", "?")}')
        click.echo(f'  {"State":<{width}}{s.get("state", "?")}')
        click.echo(f'  {"TUN":<{width}}{s.get("tun_name", "?")} — {s.get("tun_addr", "?")}')
        click.echo(f'  {"Groups":<{width}}{s.get("groups", "?")}')
        click.echo(f'  {"Uptime":<{width}}{s.get("uptime_seconds", 0)}s')
        click.echo(f'  {"Bytes in":<{width}}{_fmt_bytes(s.get("bytes_in", 0))}')
        click.echo(f'  {"Bytes out":<{width}}{_fmt_bytes(s.get("bytes_out", 0))}')
        click.echo()


if __name__ == '__main__':
    cli()
