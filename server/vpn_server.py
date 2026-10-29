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
from datetime import datetime, timezone

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from common.tun_utils import create_tun
from common.openssl_wrapper import start_tls_server
from common.framing import (pack_frame, pack_config, unpack_frames, hello_name,
                            HEADER_LEN, TYPE_DATA)
from common.daemon_utils import IpcServer, StatusTracker
from server.session_manager import SessionManager, WelcomeLimiter, ipv4_dst
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
            if not session.connected:
                # First application data = the client passed mTLS. Until this
                # point the session was only a slot claim (see stale_handshakes).
                session.mark_connected()
                print(f'[server] client #{session.client_id} relay active ({session.ip})')
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
                elif not session.name:
                    name = hello_name(ftype, buf[HEADER_LEN:frame_len])
                    if name:
                        session.name = name
                        print(f'[server] client #{session.client_id} is "{name}"')
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
            session = manager.lookup(dst) if dst is not None else None
            if session is None or session.stop.is_set() or session.tls_proc is None:
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


def crl_serial_count(crl_path: str, mode: str) -> int:
    """Number of revoked serials in a PEM CRL (for the startup log)."""
    import subprocess
    from common.openssl_wrapper import _make_env
    try:
        out = subprocess.run(['openssl', 'crl', '-in', crl_path, '-noout', '-text'],
                             capture_output=True, text=True, env=_make_env(mode)).stdout
    except OSError:
        return 0
    return out.count('Serial Number:')


def crl_next_update(crl_path: str, mode: str) -> datetime | None:
    """Parse a PEM CRL's nextUpdate as an aware UTC datetime, or None."""
    import subprocess
    from common.openssl_wrapper import _make_env
    try:
        out = subprocess.run(['openssl', 'crl', '-in', crl_path, '-noout', '-nextupdate'],
                             capture_output=True, text=True, env=_make_env(mode)).stdout
    except OSError:
        return None
    _, _, value = out.strip().partition('=')
    # openssl prints "Oct 26 17:33:54 2036 GMT", padding single-digit days with
    # a second space; collapse whitespace before parsing.
    value = ' '.join(value.replace('GMT', '').split())
    try:
        return datetime.strptime(value, '%b %d %H:%M:%S %Y').replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def crl_expiry_warning(next_update, now, crl_path: str,
                       warn_days: int = 7) -> str | None:
    """Message when a CRL has expired or is about to; None when it is healthy.

    Pure (dates passed in) so tests do not depend on the wall clock.

    A CRL carries its own validity window, and the per-session
    `-crl_check -verify_return_error` turns an expired one into a *handshake*
    failure: every client is refused, and OpenSSL reports only
    "error 12 ... CRL has expired" from inside the failed TLS exchange, where
    nothing points at the file. The daemon can see this at startup, so it says
    so there.
    """
    if next_update is None:
        return None
    stamp = next_update.strftime('%Y-%m-%d %H:%M UTC')
    remaining = next_update - now
    if remaining.total_seconds() <= 0:
        return (f'[server] CRL EXPIRED on {stamp} ({crl_path}). Every client will be '
                f'rejected during the TLS handshake ("CRL has expired"). '
                f'Fix: bash pki/refresh_crl.sh <classical|pqc>')
    if remaining.days <= warn_days:
        return (f'[server] CRL expires in {remaining.days}d, on {stamp} ({crl_path}); '
                f'after that every client is rejected. '
                f'Refresh: bash pki/refresh_crl.sh <classical|pqc>')
    return None


def idle_monitor(manager, idle_timeout, handshake_timeout, status, stop):
    """Teardown sessions that have been idle (no traffic/keepalive) too long,
    and slot claims whose client never completed mTLS within handshake_timeout.

    The second rule bounds what an unauthenticated welcome connection costs:
    a slot for handshake_timeout seconds, not the full idle window.
    """
    while not stop.is_set():
        victims = [(s, 'idle', s.idle_seconds()) for s in manager.idle_sessions(idle_timeout)]
        victims += [(s, 'no mTLS handshake', s.age_seconds())
                    for s in manager.stale_handshakes(handshake_timeout)]
        for session, why, secs in victims:
            if session.stop.is_set():
                continue  # already tearing down / waiting for removal
            print(f'[server] client #{session.client_id} {why} '
                  f'({secs:.0f}s), tearing down')
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
    crl_path = os.path.join(cert_dir, 'crl.pem')  # loaded fresh per session
    print(f'[server] client #{client_id} -> tun {session.ip}, data port {session.data_port}')

    # Start the per-client TLS listener BEFORE telling the client its port, so
    # the data connection is never refused.
    session.tls_proc = start_tls_server(
        host=config.get('host', '0.0.0.0'),
        port=session.data_port,
        cert_path=cert_path,
        key_path=key_path,
        ca_path=ca_path,
        mode=mode,
        groups=groups,
        crl_path=crl_path,
    )
    status.tls_pid = session.tls_proc.pid

    # The full config (ip/gateway/dns) goes INSIDE the TLS channel: s_server
    # only reads stdin once a connection is up, so this frame sits in the pipe
    # and becomes the first application-data record after mTLS succeeds. The
    # plaintext welcome carries nothing a MITM could usefully tamper with.
    try:
        session.tls_proc.stdin.write(pack_config(welcome))
        session.tls_proc.stdin.flush()
    except (BrokenPipeError, OSError) as e:
        print(f'[server] client #{client_id} TLS stdin unavailable ({e})')
        conn.close()
        manager.remove(client_id, session.host_id)
        return

    try:
        conn.sendall(pack_config({'port': session.data_port}))
    except OSError as e:
        # Peer vanished mid-welcome (port scan, reset). Without this the
        # exception escaped the thread and the slot + its s_server leaked.
        print(f'[server] client #{client_id} welcome send failed ({e}), releasing slot')
        conn.close()
        manager.remove(client_id, session.host_id)
        return
    finally:
        conn.close()

    err_thread = threading.Thread(
        target=drain_stderr, args=(session.tls_proc.stderr, session.stop), daemon=True)
    tls_stdout_fd = session.tls_proc.stdout.fileno()
    in_thread = threading.Thread(
        target=relay_tls_to_tun, args=(tls_stdout_fd, tun_fd, session, status), daemon=True)
    # No fixed startup wait: the relay starts now and an s_server that dies
    # early (or a client that never connects) surfaces as EOF / stale reap.
    err_thread.start()
    in_thread.start()
    in_thread.join()
    session.stop.set()
    rc = session.tls_proc.poll()
    if rc not in (None, 0) and not session.connected:
        print(f'[server] client #{client_id} TLS exited early (rc={rc})')
    print(f'[server] client #{client_id} disconnected')
    manager.remove(client_id, session.host_id)


def main():
    ipc = None
    dnsmasq_proc = None
    config = None
    manager = None
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
        crl_file = os.path.join(cert_dir, 'crl.pem')
        if os.path.isfile(crl_file):
            n = crl_serial_count(crl_file, mode)
            print(f'[server] CRL: {crl_file} ({n} revoked serial{"s" if n != 1 else ""}); '
                  'checked on every new session')
            crl_warning = crl_expiry_warning(
                crl_next_update(crl_file, mode), datetime.now(timezone.utc), crl_file)
            if crl_warning:
                print(crl_warning, file=sys.stderr, flush=True)
        else:
            print(f'[server] no CRL at {crl_file}: revocation disabled '
                  '(run pki/gen_*_ca.sh to create one)')
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
            if cmd == 'disconnect_client':
                name = payload.get('name')
                ids = manager.disconnect_by_name(name) if isinstance(name, str) else []
                if not ids:
                    return {'ok': False, 'error': f'no connected client named {name!r}'}
                print(f'[server] disconnected {name!r}: sessions {ids}')
                return {'ok': True, 'client_ids': ids}
            if cmd == 'shutdown':
                # Graceful daemon stop: the main loop's stop.wait wakes, and
                # the finally block runs NAT/IPC teardown — unlike a SIGKILL,
                # which would leak iptables rules.
                print('[server] shutdown requested over IPC')
                stop.set()
                return {'ok': True}
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
        lo, hi = manager.port_range
        print(f'[server] welcome listener on {config.get("host", "0.0.0.0")}:{welcome_port}; '
              f'data ports {lo}-{hi} ({manager.max_clients} slots)')

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
        handshake_timeout = float(config.get('handshake_timeout', 10))
        idle_thread = threading.Thread(
            target=idle_monitor,
            args=(manager, idle_timeout, handshake_timeout, status, stop), daemon=True)
        idle_thread.start()

        limiter = WelcomeLimiter(rate=float(config.get('welcome_rate', 1.0)),
                                 burst=int(config.get('welcome_burst', 3)))
        limited_logged: dict[str, float] = {}  # src ip -> last log time

        listener.settimeout(0.5)
        while not stop.is_set():
            try:
                conn, addr = listener.accept()
            except socket.timeout:
                continue
            if not limiter.allow(addr[0]):
                # Rejected BEFORE any slot/subprocess is touched, so a flood
                # costs the server one accept() and nothing else.
                conn.close()
                now = time.monotonic()
                if now - limited_logged.get(addr[0], 0) >= 60:
                    limited_logged[addr[0]] = now
                    print(f'[server] welcome rate-limited: {addr[0]} '
                          f'(> {limiter.burst:.0f} burst / {limiter.rate:g}/s)')
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
        if manager is not None:
            for session in list(manager.sessions.values()):
                session.stop.set()
                if session.tls_proc is not None:
                    try:
                        session.tls_proc.terminate()
                    except ProcessLookupError:
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