#!/usr/bin/env python3
import os
import sys
import time
import signal
import socket
import threading
import struct
import select
import json
import yaml
import argparse

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from common.tun_utils import create_tun
from common.openssl_wrapper import start_tls_server
from common.framing import pack_frame, pack_config, unpack_frames, HEADER_LEN, TYPE_DATA
from common.daemon_utils import IpcServer, StatusTracker
from server.session_manager import SessionManager, ipv4_dst
from server.net_config import setup_nat, teardown_nat, start_dnsmasq, stop_dnsmasq, drain_dnsmasq

POLL_INTERVAL = 0.5


def parse_args():
    parser = argparse.ArgumentParser(description='PQ-VPN Server')
    parser.add_argument('--config', default='config.yaml',
                        help='Config file (default: config.yaml)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print network commands instead of executing (no root needed)')
    return parser.parse_args()


def load_config(config_path):
    with open(config_path) as f:
        return yaml.safe_load(f)


def drain_stderr(stream, stop):
    while not stop.is_set():
        try:
            chunk = stream.read(4096)
            if not chunk:
                break
            sys.stderr.write(chunk.decode(errors='replace'))
            sys.stderr.flush()
        except (ValueError, OSError):
            break


def relay_tls_to_tun(tls_stdout_fd, tun_fd, session, status):
    buf = b''
    write_drops = 0
    while not session.stop.is_set():
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
                session.touch()
                if ftype == TYPE_DATA:
                    try:
                        os.write(tun_fd, buf[HEADER_LEN:frame_len])
                    except OSError as e:
                        # A rejected packet (e.g. interface momentarily not
                        # ready) must NOT kill the whole tunnel.
                        write_drops += 1
                        if write_drops <= 3 or write_drops % 50 == 0:
                            print(f'[server] client #{session.client_id} '
                                  f'TUN write dropped packet ({e})')
                    with session._lock:
                        session.bytes_in += length
                        status.bytes_in += length
                buf = buf[frame_len:]
        except (BrokenPipeError, OSError):
            break
    session.stop.set()


def relay_tun_to_tls_loop(tun_fd, manager, status):
    """Shared TUN reader: demux each inbound packet to the right client.

    This loop must never die: one bad write to a dying session's pipe would
    otherwise blackhole every client. Errors are logged and the packet dropped.
    """
    while True:
        try:
            r, _, _ = select.select([tun_fd], [], [], POLL_INTERVAL)
            if not r:
                continue
            raw = os.read(tun_fd, 4096)
            if not raw:
                break
            dst = ipv4_dst(raw)
            session = None
            if dst is not None:
                target = f'10.8.0.{dst[-1]}'
                with manager._lock:
                    for s in manager.sessions.values():
                        if s.ip == target:
                            session = s
                            break
            if session is None or session.stop.is_set():
                continue
            try:
                frame = pack_frame(raw)
                os.write(session.tls_proc.stdin.fileno(), frame)
            except (BrokenPipeError, OSError) as e:
                print(f'[server] demux write to client #{session.client_id} '
                      f'failed, dropping packet ({e})')
                continue
            with session._lock:
                session.bytes_out += len(frame)
                status.bytes_out += len(frame)
        except OSError as e:
            # TUN read hiccup — log and keep going; only a closed fd (EBADF)
            # at shutdown ends us.
            print(f'[server] demux loop error ({e})')
            if isinstance(e, OSError) and e.errno == 9:  # EBADF: fd closed
                break


def idle_monitor(manager, idle_timeout, status, stop):
    """Teardown sessions that have been idle (no traffic/keepalive) too long."""
    while not stop.is_set():
        for session in manager.idle_sessions(idle_timeout):
            if session.stop.is_set():
                continue  # already tearing down / waiting for removal
            print(f'[server] client #{session.client_id} idle '
                  f'({session.idle_seconds():.0f}s), tearing down')
            session.stop.set()
            if session.tls_proc is not None:
                session.tls_proc.terminate()
        time.sleep(1.0)


def handle_client(conn, manager, config, status, cert_dir, mode, groups, tun_fd):
    welcome = manager.welcome_payload()
    if welcome is None:
        conn.close()
        print('[server] client rejected: IP pool full')
        return

    client_id = welcome['id']
    session = manager.get(client_id)
    cert_path = os.path.join(cert_dir, 'server.crt')
    key_path = os.path.join(cert_dir, 'server.key')
    ca_path = os.path.join(cert_dir, 'ca.crt')
    print(f'[server] client #{client_id} -> tun {session.ip}, data port {session.data_port}')

    # Start the per-client TLS listener BEFORE pushing config, so the client's
    # data connection is never refused.
    session.tls_proc = start_tls_server(
        host=config.get('host', '0.0.0.0'),
        port=session.data_port,
        cert_path=cert_path,
        key_path=key_path,
        ca_path=ca_path,
        mode=mode,
        groups=groups,
    )
    status.tls_pid = session.tls_proc.pid

    try:
        conn.sendall(pack_config(welcome))
    finally:
        conn.close()

    err_thread = threading.Thread(
        target=drain_stderr, args=(session.tls_proc.stderr, session.stop), daemon=True)

    # Wait up to 5s for this client's TLS data connection to come up.
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if session.stop.is_set():
            break
        if session.tls_proc.poll() is not None:
            err = session.tls_proc.stderr.read().decode(errors='replace').strip()
            print(f'[server] client #{client_id} TLS exited: {err}')
            manager.remove(client_id, session.host_id)
            return
        time.sleep(0.1)

    tls_stdout_fd = session.tls_proc.stdout.fileno()
    in_thread = threading.Thread(
        target=relay_tls_to_tun, args=(tls_stdout_fd, tun_fd, session, status), daemon=True)
    print(f'[server] client #{client_id} relay active ({session.ip})')
    in_thread.start()

    err_thread.start()
    in_thread.join()
    session.stop.set()
    session.tls_proc.terminate()
    print(f'[server] client #{client_id} disconnected')
    manager.remove(client_id, session.host_id)


def main():
    ipc = None
    dnsmasq_proc = None
    config = None
    try:
        args = parse_args()
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.path.basename(args.config))
        config = load_config(config_path)
        stop = threading.Event()

        os.chdir(PROJECT_DIR)
        cert_dir = config.get('cert_dir', 'pki/certs/classical')
        mode = config.get('mode', 'classical')
        groups = config.get('groups', 'X25519MLKEM768')

        print(f'[server] loading config from {config_path}, mode={mode}')
        print(f'[server] TUN name: {config.get("tun_name", "tun0")}')
        # create_tun (not open_tun_by_name): the server's tun0 MUST have its
        # address assigned and be brought UP — an unconfigured/down device
        # made dnsmasq bind fail and turned demux writes into session-killing
        # OSErrors on the first injected packets.
        gateway = config.get('gateway', '10.8.0.1')
        tun_fd = create_tun(
            config.get('tun_name', 'tun0'),
            f'{gateway}/24',
            config.get('tun_mtu', 1400),
        )
        print(f'[server] TUN fd={tun_fd} at {gateway}/24')

        if config.get('nat', True):
            print('[server] configuring NAT + forwarding...')
            setup_nat(config, dry_run=args.dry_run)

        manager = SessionManager(config)

        def ipc_command(line: str) -> dict:
            """Handle dashboard/vpnctl IPC commands (e.g. disconnect)."""
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                return {'error': 'invalid json'}
            cmd = payload.get('cmd')
            if cmd == 'disconnect':
                cid = payload.get('client_id')
                if not isinstance(cid, int) or manager.disconnect(cid) is False:
                    return {'ok': False, 'error': f'no client #{cid}'}
                print(f'[server] dashboard disconnected client #{cid}')
                return {'ok': True}
            if cmd == 'clients':
                return {'ok': True, 'clients': manager.snapshot()}
            return {'error': f'unknown command: {cmd}'}

        status = StatusTracker(
            config,
            tun_name=config.get('tun_name', 'tun0'),
            tun_addr=manager.network_ip,
        )
        ipc = IpcServer(status, role='server', command_handler=ipc_command)
        ipc.start()

        if config.get('dnsmasq', False):
            print('[server] starting dnsmasq...')
            dnsmasq_proc = start_dnsmasq(config, dry_run=args.dry_run)
            if dnsmasq_proc is not None:
                threading.Thread(
                    target=drain_dnsmasq, args=(dnsmasq_proc, stop), daemon=True).start()

        welcome_port = config['port']
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((config.get('host', '0.0.0.0'), welcome_port))
        listener.listen(4)
        print(f'[server] welcome listener on {config.get("host", "0.0.0.0")}:{welcome_port}')

        status.state = 'connected'

        def signal_handler(*_):
            stop.set()

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

        demux_thread = threading.Thread(
            target=relay_tun_to_tls_loop, args=(tun_fd, manager, status), daemon=True)
        demux_thread.start()

        def refresh_clients():
            while not stop.is_set():
                status.set_clients(manager.snapshot())
                time.sleep(1.0)

        refresh_thread = threading.Thread(target=refresh_clients, daemon=True)
        refresh_thread.start()

        idle_timeout = config.get('keepalive_interval', 15) * 3
        idle_thread = threading.Thread(
            target=idle_monitor, args=(manager, idle_timeout, status, stop), daemon=True)
        idle_thread.start()

        listener.settimeout(0.5)
        while not stop.is_set():
            try:
                conn, addr = listener.accept()
            except socket.timeout:
                continue
            print(f'[server] welcome connection from {addr}')
            t = threading.Thread(
                target=handle_client,
                args=(conn, manager, config, status, cert_dir, mode, groups, tun_fd),
                daemon=True)
            t.start()

        listener.close()
        status.state = 'disconnected'
        print('[server] Shutdown complete')
    except Exception as e:
        print(f'[server] FATAL: {e}', file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
    finally:
        # Terminate every per-client s_server child; otherwise they outlive
        # the daemon until something else reaps them.
        try:
            for session in list(manager.sessions.values()):
                session.stop.set()
                if session.tls_proc is not None:
                    session.tls_proc.terminate()
        except Exception:
            pass
        stop_dnsmasq(dnsmasq_proc)
        if config is not None:
            try:
                teardown_nat(config, dry_run=args.dry_run)
            except Exception as e:
                print(f'[server] NAT teardown failed: {e}', file=sys.stderr)
        if ipc:
            ipc.stop()


if __name__ == '__main__':
    main()