#!/usr/bin/env python3
import os
import sys
import time
import signal
import threading
import struct
import select
import yaml
import argparse

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from common.tun_utils import open_tun_by_name
from common.openssl_wrapper import start_tls_client
from common.framing import pack_frame, HEADER_LEN
from common.daemon_utils import IpcServer, StatusTracker

POLL_INTERVAL = 0.5


def parse_args():
    parser = argparse.ArgumentParser(description='PQ-VPN Client')
    parser.add_argument('--config', default='config.yaml',
                        help='Config file (default: config.yaml)')
    return parser.parse_args()


def load_config(config_path):
    with open(config_path) as f:
        return yaml.safe_load(f)


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


def relay_tls_to_tun(tls_stdout_fd, tun_fd, stop, status):
    buf = b''
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
                length = struct.unpack('!I', buf[:HEADER_LEN])[0]
                frame_len = HEADER_LEN + length
                if len(buf) < frame_len:
                    break
                os.write(tun_fd, buf[HEADER_LEN:frame_len])
                status.bytes_in += length
                buf = buf[frame_len:]
        except (BrokenPipeError, OSError):
            break


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


def main():
    ipc = None
    try:
        args = parse_args()
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.path.basename(args.config))
        config = load_config(config_path)
        stop = threading.Event()

        os.chdir(PROJECT_DIR)
        cert_dir = config.get('cert_dir', 'pki/certs/classical')

        print(f'[client] loading config from {config_path}, mode={config.get("mode", "classical")}')
        print(f'[client] TUN name: {config.get("tun_name", "tun0")}')
        print(f'[client] Opening TUN...')
        tun_fd = open_tun_by_name(config.get('tun_name', 'tun0'))
        print(f'[client] TUN fd={tun_fd}')

        status = StatusTracker(
            config,
            tun_name=config.get('tun_name', 'tun0'),
            tun_addr=config.get('tun_addr', '10.8.0.2/24'),
        )
        ipc = IpcServer(status, role='client')
        ipc.start()

        print(f'[client] Connecting to {config["server_host"]}:{config["server_port"]}...')
        MAX_RETRIES = 5
        RETRY_DELAY = 1.0
        tls_proc = None
        for attempt in range(MAX_RETRIES):
            tls_proc = start_tls_client(
                host=config['server_host'],
                port=config['server_port'],
                cert_path=os.path.join(cert_dir, 'client.crt'),
                key_path=os.path.join(cert_dir, 'client.key'),
                ca_path=os.path.join(cert_dir, 'ca.crt'),
                mode=config.get('mode', 'classical'),
                groups=config.get('groups', 'X25519MLKEM768'),
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

        tls_stdin_fd = tls_proc.stdin.fileno()
        tls_stdout_fd = tls_proc.stdout.fileno()

        def signal_handler(*_):
            stop.set()

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

        t1 = threading.Thread(target=relay_tun_to_tls, args=(tun_fd, tls_stdin_fd, stop, status), daemon=True)
        t2 = threading.Thread(target=relay_tls_to_tun, args=(tls_stdout_fd, tun_fd, stop, status), daemon=True)
        t3 = threading.Thread(target=drain_stderr, args=(tls_proc.stderr, stop), daemon=True)
        t1.start()
        t2.start()
        t3.start()
        print(f'[client] Relaying... (TUN fd={tun_fd} <-> TLS pid={tls_proc.pid})')
        t1.join()
        t2.join()

        status.state = 'disconnected'
        tls_proc.terminate()
        tls_proc.wait()
        print(f'[client] Shutdown complete')
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
