import os
import json
import glob
import time
import select
import socket
import threading


def socket_path(role: str, instance: str | None = None) -> str:
    if instance:
        return f'/tmp/pqvpn-{role}-{instance}.sock'
    return f'/tmp/pqvpn-{role}.sock'


def pid_path(role: str, instance: str | None = None) -> str:
    if instance:
        return f'/tmp/pqvpn-{role}-{instance}.pid'
    return f'/tmp/pqvpn-{role}.pid'


def discover_sockets(role: str) -> list[str]:
    """All IPC sockets for a role: the legacy unsuffixed path (if present)
    plus any per-instance `-<name>.sock` variants, sorted. Lets vpnctl and the
    dashboard find every running instance of a role."""
    paths = sorted(glob.glob(f'/tmp/pqvpn-{role}-*.sock'))
    legacy = f'/tmp/pqvpn-{role}.sock'
    if os.path.exists(legacy):
        paths.insert(0, legacy)
    return paths


def instance_label(sock_or_pid_path: str) -> str:
    """Extract the instance suffix from a socket/pid path ('' for legacy)."""
    base = os.path.basename(sock_or_pid_path)
    parts = base[:-len('.sock')].split('-', 2)
    return parts[2] if len(parts) > 2 else ''


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
        self.resumed = False
        self.negotiated: dict = {}   # actual TLS negotiation result (see note_negotiated)
        self.clients = []
        self.start_time = time.time()      # daemon process start
        self.session_start = None          # set when a tunnel becomes 'connected'

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
                'session_seconds': (int(time.time() - self.session_start)
                                    if self.session_start is not None else 0),
                'tls_pid': self.tls_pid,
                'resumed': self.resumed,
                'negotiated': dict(self.negotiated),
                'groups': self.config.get('groups', 'X25519MLKEM768'),
                'clients': list(self.clients),
            }

    def set_clients(self, clients: list[dict]):
        with self._lock:
            self.clients = clients

    def reset_session(self):
        """Clear per-session values at the start of every connection attempt so
        stale negotiated/resumed data from a previous tunnel is never shown."""
        with self._lock:
            self.resumed = False
            self.negotiated = {}
            self.tls_pid = 0
            self.tun_addr = 'pending'
            self.session_start = None

    def mark_session_start(self):
        """Call when the tunnel becomes usable: session uptime counts from here,
        independent of daemon uptime (which survives reconnects)."""
        with self._lock:
            self.session_start = time.time()

    def note_negotiated(self, key: str, value):
        with self._lock:
            self.negotiated[key] = value

    def set_resumed(self, value: bool):
        with self._lock:
            self.resumed = value

    def set_tun_addr(self, value: str):
        with self._lock:
            self.tun_addr = value


def write_pid_file(role: str, instance: str | None = None):
    path = pid_path(role, instance)
    with open(path, 'w') as f:
        f.write(str(os.getpid()))


def remove_pid_file(role: str, instance: str | None = None):
    try:
        os.remove(pid_path(role, instance))
    except FileNotFoundError:
        pass


def read_pid_file(role: str, instance: str | None = None) -> int | None:
    try:
        with open(pid_path(role, instance)) as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


class IpcServer:
    def __init__(self, status: StatusTracker, role: str, command_handler=None,
                 instance: str | None = None):
        self.status = status
        self.role = role
        self.instance = instance
        self._command_handler = command_handler
        self._sock_path = socket_path(role, instance)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        remove_pid_file(self.role, self.instance)
        write_pid_file(self.role, self.instance)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        try:
            os.unlink(self._sock_path)
        except FileNotFoundError:
            pass
        remove_pid_file(self.role, self.instance)

    def _run(self):
        try:
            os.unlink(self._sock_path)
        except FileNotFoundError:
            pass
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(self._sock_path)
        # Status is world-readable and commands (disconnect) are reachable by
        # any local user. Acceptable for a single-user dev/demo tool — VPN host
        # security comes from the mTLS boundary, not socket perms. Without this,
        # daemons run as root but user-side vpnctl/dashboard get EACCES.
        try:
            os.chmod(self._sock_path, 0o666)
        except OSError:
            pass
        server.listen(1)
        server.settimeout(0.5)

        while not self._stop.is_set():
            try:
                conn, _ = server.accept()
                command = ''
                result = None
                # Wait up to 100ms for an optional command, then reply. This keeps
                # bare status polls fast (vpnctl) while still handling commands.
                try:
                    r, _, _ = select.select([conn], [], [], 0.1)
                    if r:
                        command = conn.recv(4096).decode().strip()
                        if command and self._command_handler:
                            result = self._command_handler(command)
                except (socket.timeout, UnicodeDecodeError, OSError):
                    pass
                if command:
                    reply = {'status': self.status.snapshot()}
                    if result is not None:
                        reply['result'] = result
                else:
                    reply = self.status.snapshot()
                conn.sendall((json.dumps(reply) + '\n').encode())
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


def ipc_request(role: str, command: str = '', instance: str | None = None) -> dict:
    """Connect to a daemon's IPC socket, optionally send a command, get JSON reply."""
    import socket as _socket
    s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    s.settimeout(3.0)
    s.connect(socket_path(role, instance))
    if command:
        s.sendall(command.encode())
    data = b''
    try:
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    except (OSError, _socket.timeout):
        pass
    finally:
        s.close()
    return json.loads(data.decode())
