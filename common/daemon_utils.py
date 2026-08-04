import os
import json
import time
import socket
import threading


def socket_path(role: str) -> str:
    return f'/tmp/pqvpn-{role}.sock'


def pid_path(role: str) -> str:
    return f'/tmp/pqvpn-{role}.pid'


class StatusTracker:
    def __init__(self, config: dict, tun_name: str, tun_addr: str):
        self._lock = threading.Lock()
        self.config = config
        self.tun_name = tun_name
        self.tun_addr = tun_addr
        self.state = 'starting'
        self.bytes_in = 0
        self.bytes_out = 0
        self.tls_pid = 0
        self.start_time = time.time()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                'mode': self.config.get('mode', 'classical'),
                'state': self.state,
                'tun_name': self.tun_name,
                'tun_addr': self.tun_addr,
                'bytes_in': self.bytes_in,
                'bytes_out': self.bytes_out,
                'uptime_seconds': int(time.time() - self.start_time),
                'tls_pid': self.tls_pid,
                'groups': self.config.get('groups', 'X25519MLKEM768'),
            }


def write_pid_file(role: str):
    path = pid_path(role)
    with open(path, 'w') as f:
        f.write(str(os.getpid()))


def remove_pid_file(role: str):
    try:
        os.remove(pid_path(role))
    except FileNotFoundError:
        pass


def read_pid_file(role: str) -> int | None:
    try:
        with open(pid_path(role)) as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


class IpcServer:
    def __init__(self, status: StatusTracker, role: str):
        self.status = status
        self.role = role
        self._sock_path = socket_path(role)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        remove_pid_file(self.role)
        write_pid_file(self.role)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        try:
            os.unlink(self._sock_path)
        except FileNotFoundError:
            pass
        remove_pid_file(self.role)

    def _run(self):
        try:
            os.unlink(self._sock_path)
        except FileNotFoundError:
            pass
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(self._sock_path)
        server.listen(1)
        server.settimeout(0.5)

        while not self._stop.is_set():
            try:
                conn, _ = server.accept()
                data = json.dumps(self.status.snapshot()) + '\n'
                conn.sendall(data.encode())
                conn.close()
            except socket.timeout:
                continue
            except OSError:
                break

        server.close()
        try:
            os.unlink(self._sock_path)
        except FileNotFoundError:
            pass
