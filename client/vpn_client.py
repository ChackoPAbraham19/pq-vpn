#!/usr/bin/env python3
import json
import os
import sys
import time
import signal
import socket
import threading
import struct
import select
import yaml
import argparse

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from common.tun_utils import create_tun, destroy_tun
from common.openssl_wrapper import start_tls_client, parse_brief_line
from common.framing import pack_frame, pack_keepalive, unpack_config, HEADER_LEN, TYPE_DATA
from common.daemon_utils import IpcServer, StatusTracker
from common.ticket_cache import load_ticket, save_ticket_file
from client.net_config import apply_tunnel_net, restore_tunnel_net

POLL_INTERVAL = 0.5


def parse_args():
    parser = argparse.ArgumentParser(description='PQ-VPN Client')
    parser.add_argument('--config', default='config.yaml',
                        help='Config file (default: config.yaml)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print network commands instead of executing (no root needed)')
    return parser.parse_args()


def load_config(config_path):
    with open(config_path) as f:
        return yaml.safe_load(f)


def welcome_handshake(server_host, server_port, timeout=5.0):
    """Connect to the welcome port, read the config-push, return config dict."""
    sock = socket.create_connection((server_host, server_port), timeout=timeout)
    try:
        buf = b''
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            cfg, remaining = unpack_config(buf)
            if cfg is not None:
                return cfg
        raise RuntimeError('welcome closed before config received')
    finally:
        sock.close()


def drain_stderr(stream, stop, status=None, ticket_offered=False):
    """Forward s_client stderr to the console, extracting `-brief` negotiation
    info (protocol/cipher/signature/KEX) into the status tracker as it appears.

    Uses readline() so values surface WHILE the tunnel is up: brief output is
    newline-terminated and small (<4096B), so a blocking read(4096) would sit
    on it until process exit.

    Resumption is confirmed, not assumed: a resumed TLS 1.3 brief omits the
    certificate-exchange lines ('Requested Signature Algorithms'/'Signature
    type'). A ticket merely being OFFERED is not proof it was accepted — each
    per-client s_server process regenerates ticket keys, so cross-restart
    resumption normally falls back to a full handshake.
    """
    saw_cert_exchange = False
    while not stop.is_set():
        try:
            line = stream.readline()
            if not line:
                break
            text = line.decode(errors='replace') if isinstance(line, bytes) else line
            if status is not None:
                parsed = parse_brief_line(text)
                if parsed:
                    status.note_negotiated(*parsed)
                    if parsed[0] == 'sig_type':
                        saw_cert_exchange = True
                elif 'Requested Signature Algorithms:' in text:
                    saw_cert_exchange = True
                elif text.strip() == 'Verification: OK':
                    # Handshake finished; now we know whether the exchange
                    # was genuinely skipped.
                    resumed = bool(ticket_offered and not saw_cert_exchange)
                    status.set_resumed(resumed)
                    if ticket_offered:
                        if resumed:
                            print('[client] session RESUMED (ticket accepted)')
                        else:
                            print('[client] full handshake (ticket not accepted — '
                                  'fresh s_server has new ticket keys)')
            sys.stderr.write(text)
            sys.stderr.flush()
        except (ValueError, OSError):
            break


def relay_tls_to_tun(tls_stdout_fd, tun_fd, stop, status, no_gateway=False):
    buf = b''
    write_drops = 0
    while not stop.is_set():
        try:
            r, _, _ = select.select([tls_stdout_fd], [], [], POLL_INTERVAL)
            if not r:
                continue
            chunk = os.read(tls_stdout_fd, 4096)
            if not chunk:
                break
            buf += chunk
            while len(buf) >= HEADER_LEN:
                ftype = buf[0]
                length = struct.unpack('!I', buf[1:HEADER_LEN])[0]
                frame_len = HEADER_LEN + length
                if len(buf) < frame_len:
                    break
                if ftype == TYPE_DATA:
                    try:
                        os.write(tun_fd, buf[HEADER_LEN:frame_len])
                    except OSError as e:
                        write_drops += 1
                        if write_drops <= 3 or write_drops % 50 == 0:
                            print(f'[client] TUN write dropped packet ({e})')
                    status.bytes_in += length
                buf = buf[frame_len:]
        except (BrokenPipeError, OSError):
            break


def relay_tun_to_tls(tun_fd, tls_stdin_fd, stop, status):
    while not stop.is_set():
        try:
            r, _, _ = select.select([tun_fd], [], [], POLL_INTERVAL)
            if not r:
                continue
            raw = os.read(tun_fd, 4096)
            if not raw:
                break
            frame = pack_frame(raw)
            os.write(tls_stdin_fd, frame)
            status.bytes_out += len(frame)
        except (BrokenPipeError, OSError):
            break


def next_backoff(current: float, factor: float = 2.0, cap: float = 30.0) -> float:
    """Exponential backoff growth, capped."""
    return min(current * factor, cap)


def keepalive_loop(tls_stdin_fd, interval, stop):
    """Send a control keepalive frame every `interval` seconds while connected."""
    while not stop.is_set():
        if stop.wait(interval):
            return
        try:
            os.write(tls_stdin_fd, pack_keepalive())
        except (BrokenPipeError, OSError):
            return


def run_session(config, args, stop, status):
    """Run one tunnel session; returns when it disconnects.

    Returns True if the session ended cleanly (signal) or False if it dropped
    unexpectedly (caller should reconnect).
    """
    os.chdir(PROJECT_DIR)
    cert_dir = config.get('cert_dir', 'pki/certs/classical')
    mode = config.get('mode', 'classical')
    groups = config.get('groups', 'X25519MLKEM768')

    # Fresh attempt: drop any negotiated/resumed values from a previous tunnel
    # so status never shows stale data across reconnects.
    status.reset_session()
    # The client offers exactly one group (and the server enforces -groups in
    # pqc mode), so a completed handshake proves this group was used.
    status.note_negotiated('group_offered', groups)

    status.state = 'handshake-in-progress'
    print(f'[client] welcome handshake to {config["server_host"]}:{config["server_port"]}...')
    welcome = welcome_handshake(config['server_host'], config['server_port'])
    assigned_ip = welcome['ip']
    data_port = welcome['port']
    print(f'[client] assigned {assigned_ip} (data port {data_port}, gateway {welcome["gateway"]})')

    tun_name = config.get('tun_name', 'tun0')
    tun_mtu = config.get('tun_mtu', 1400)
    print(f'[client] Opening TUN {tun_name} at {assigned_ip}/24...')
    tun_fd = None
    try:
        tun_fd = create_tun(tun_name, f'{assigned_ip}/24', tun_mtu)
    except OSError as e:
        if e.errno == 16:  # EBUSY: a stale device from a previous drop is
            # still held (e.g. after a crash). Delete and retry once.
            print(f'[client] TUN {tun_name} busy — removing stale device')
            destroy_tun(tun_name)
            tun_fd = create_tun(tun_name, f'{assigned_ip}/24', tun_mtu)
        else:
            raise
    print(f'[client] TUN fd={tun_fd}')
    status.set_tun_addr(f'{assigned_ip}/24')

    clean = False
    applied_net = None
    try:
        print(f'[client] Connecting TLS to {config["server_host"]}:{data_port}...')
        MAX_RETRIES = 5
        RETRY_DELAY = 0.5
        sess_in = None
        sess_out = None
        if config.get('resume_cache', True):
            sess_in = load_ticket(config['server_host'])
            sess_out = save_ticket_file(config['server_host'])
            if sess_in:
                # Intent, not outcome: whether the ticket is ACCEPTED is
                # reported by drain_stderr at Verification: OK.
                print(f'[client] offering cached ticket for resumption: {sess_in}')
        tls_proc = None
        for attempt in range(MAX_RETRIES):
            tls_proc = start_tls_client(
                host=config['server_host'],
                port=data_port,
                cert_path=os.path.join(cert_dir, 'client.crt'),
                key_path=os.path.join(cert_dir, 'client.key'),
                ca_path=os.path.join(cert_dir, 'ca.crt'),
                mode=mode,
                groups=groups,
                sess_in=sess_in,
                sess_out=sess_out,
            )
            print(f'[client] TLS subprocess started, pid={tls_proc.pid} (attempt {attempt+1}/{MAX_RETRIES})')
            status.tls_pid = tls_proc.pid

            deadline = time.time() + 5.0
            while time.time() < deadline:
                if tls_proc.poll() is not None:
                    err = tls_proc.stderr.read().decode(errors='replace').strip()
                    if attempt < MAX_RETRIES - 1 and 'Connection refused' in err:
                        print(f'[client] connection refused, retrying in {RETRY_DELAY}s...')
                        time.sleep(RETRY_DELAY)
                        break
                    raise RuntimeError(f'TLS subprocess exited early with code {tls_proc.returncode}: {err}')
                time.sleep(0.1)
            else:
                break
        else:
            err = tls_proc.stderr.read().decode(errors='replace').strip() if tls_proc else 'unknown'
            raise RuntimeError(f'Failed to connect after {MAX_RETRIES} attempts: {err}')
        print('[client] connected')
        status.state = 'connected'
        # 'resumed' is set by drain_stderr on hard evidence (cert exchange
        # skipped); offering a ticket alone proves nothing — fresh s_server
        # processes regenerate ticket keys.
        status.set_resumed(False)
        if sess_out:
            print('[client] session cached for next reconnect')

        applied_net = apply_tunnel_net({**config, 'gateway': welcome['gateway'],
                                        'dns_servers': welcome['dns']},
                                       dry_run=args.dry_run)
        print(f'[client] network applied: {applied_net}')

        tls_stdin_fd = tls_proc.stdin.fileno()
        tls_stdout_fd = tls_proc.stdout.fileno()

        t1 = threading.Thread(target=relay_tun_to_tls, args=(tun_fd, tls_stdin_fd, stop, status), daemon=True)
        t2 = threading.Thread(target=relay_tls_to_tun, args=(tls_stdout_fd, tun_fd, stop, status), daemon=True)
        t3 = threading.Thread(target=drain_stderr, args=(tls_proc.stderr, stop, status, sess_in is not None), daemon=True)
        t4 = threading.Thread(
            target=keepalive_loop,
            args=(tls_stdin_fd, config.get('keepalive_interval', 15), stop),
            daemon=True)
        t1.start()
        t2.start()
        t3.start()
        t4.start()
        print(f'[client] Relaying... (TUN {tun_name} <-> TLS pid={tls_proc.pid})')
        t1.join()
        t2.join()

        clean = stop.is_set()
        status.state = 'disconnected'
        tls_proc.terminate()
        tls_proc.wait()
    finally:
        # Tear down in reverse order: routes first, then the device itself.
        # Destroying the TUN releases the fd that made every reconnect fail
        # with EBUSY before this fix.
        if applied_net:
            restore_tunnel_net(applied_net, {**config, 'gateway': welcome['gateway'],
                                             'dns_servers': welcome['dns']},
                               dry_run=args.dry_run)
            print(f'[client] network restored: {applied_net}')
        destroy_tun(tun_name, tun_fd)
    return clean


def main():
    ipc = None
    try:
        args = parse_args()
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.path.basename(args.config))
        config = load_config(config_path)
        stop = threading.Event()

        def signal_handler(*_):
            stop.set()

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

        status = StatusTracker(config, tun_name=config.get('tun_name', 'tun0'),
                               tun_addr='pending')

        def ipc_command(line: str) -> dict:
            """Handle vpnctl IPC commands (shutdown)."""
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                return {'error': 'invalid json'}
            cmd = payload.get('cmd')
            if cmd == 'shutdown':
                # Graceful stop: the reconnect loop sees stop set and exits
                # cleanly (same path as SIGTERM/SIGINT).
                print('[client] shutdown requested over IPC')
                stop.set()
                return {'ok': True}
            return {'error': f'unknown command: {cmd}'}

        ipc = IpcServer(status, role='client', instance=config.get('instance'),
                        command_handler=ipc_command)
        ipc.start()

        backoff = config.get('backoff_min', 1.0)
        backoff_max = config.get('backoff_max', 30.0)

        while not stop.is_set():
            try:
                clean = run_session(config, args, stop, status)
                if clean:
                    print('[client] Shutdown complete')
                    return
                raise RuntimeError('tunnel dropped')
            except Exception as e:
                if stop.is_set():
                    print('[client] Shutdown complete (during reconnect)')
                    return
                print(f'[client] connection lost: {e}')
                status.state = 'reconnecting'
                status.tls_pid = 0
                print(f'[client] reconnecting in {backoff:.0f}s...')
                if stop.wait(backoff):
                    return
                backoff = next_backoff(backoff, cap=backoff_max)
    except Exception as e:
        print(f'[client] FATAL: {e}', file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
    finally:
        if ipc:
            ipc.stop()


if __name__ == '__main__':
    main()