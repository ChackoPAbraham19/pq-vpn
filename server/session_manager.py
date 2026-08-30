#!/usr/bin/env python3
"""session_manager.py -- multi-client sessions for the PQ-VPN server.

Each accepted welcome connection is assigned a client id, a tunnel IP, and a
dedicated TLS data port. The server then runs one `openssl s_server` subprocess
per client on that data port, and a shared TUN reader demuxes inbound packets
to the correct client by destination IP.
"""
import ipaddress
import threading
import time

DATA_PORT_BASE = 14431  # welcome port = base - 1; per-client ports start here


def ipv4_dst(packet: bytes):
    """Return the destination IPv4 address of a tunnel packet, or None."""
    if len(packet) < 20 or (packet[0] >> 4) != 4:
        return None
    return bytes(packet[16:20])


class IpPool:
    def __init__(self, network: str, reserved: list[str] | None = None):
        """Allocate tunnel addresses from network (e.g. '10.8.0.0/24').

        Optionally exclude `reserved` addresses (like the server's own .1).
        """
        self._net = ipaddress.ip_network(network, strict=False)
        reserved = {ipaddress.ip_address(a) for a in (reserved or [])}
        self._free = [
            a for a in self._net.hosts() if a not in reserved
        ]
        self._lock = threading.Lock()
        self._in_use: dict[int, ipaddress.IPv4Address] = {}

    def alloc(self) -> int:
        """Allocate the next free host id (e.g. 2 for 10.8.0.2)."""
        with self._lock:
            while self._free and self._free[0] in self._in_use.values():
                self._free.pop(0)
            if not self._free:
                raise RuntimeError('IP pool exhausted')
            addr = self._free.pop(0)
            host_id = int(addr.packed[-1])
            self._in_use[host_id] = addr
            return host_id

    def release(self, host_id: int):
        with self._lock:
            addr = self._in_use.pop(host_id, None)
            if addr is not None:
                self._free.insert(0, addr)

    def list(self) -> list[str]:
        with self._lock:
            return [str(a) for a in sorted(self._in_use.values())]


class ClientSession:
    def __init__(self, client_id: int, host_id: int, data_port: int, gateway: str, dns: str):
        self.client_id = client_id
        self.host_id = host_id
        self.ip = f'10.8.0.{host_id}'
        self.data_port = data_port
        self.gateway = gateway
        self.dns = dns
        self.stop = threading.Event()
        self.tls_proc = None
        self.relay_in_thread = None
        self.relay_out_thread = None
        self.bytes_in = 0
        self.bytes_out = 0
        self.last_seen = time.monotonic()
        self._lock = threading.Lock()

    def touch(self):
        with self._lock:
            self.last_seen = time.monotonic()

    def idle_seconds(self) -> float:
        with self._lock:
            return time.monotonic() - self.last_seen

    def snapshot(self) -> dict:
        return {
            'id': self.client_id,
            'ip': self.ip,
            'port': self.data_port,
            'gateway': self.gateway,
            'dns': self.dns,
            'bytes_in': self.bytes_in,
            'bytes_out': self.bytes_out,
            'idle_seconds': round(self.idle_seconds(), 1),
        }


class SessionManager:
    def __init__(self, config: dict):
        self.config = config
        self.gateway = config.get('gateway', '10.8.0.1')
        self.dns = config.get('dns', '10.8.0.1')
        net = config.get('tun_net', '10.8.0.0/24')
        self.network_ip = f'{self.gateway}/24'
        self.pool = IpPool(net, reserved=[self.gateway])
        self.session_port_base = config.get('data_port_base', DATA_PORT_BASE)
        self.max_clients = int(config.get('max_clients', 3))
        self.sessions: dict[int, ClientSession] = {}
        self._lock = threading.Lock()
        self._next_id = 1

    def welcome_payload(self) -> dict | None:
        """Allocate a client slot. Returns config-push dict or None if full.

        On a full pool, idle sessions (>15s silent) are reclaimed in-place so a
        reconnecting client isn't bounced for the full 45s idle teardown window.
        """
        host_id = None
        with self._lock:
            if len(self.sessions) < self.max_clients:
                try:
                    host_id = self.pool.alloc()
                except RuntimeError:
                    host_id = None
        if host_id is None:
            # Pool full (or alloc failed): try reclaiming idle slots, then retry.
            self.reclaim_idle(15.0)
            with self._lock:
                if len(self.sessions) >= self.max_clients:
                    return None
                try:
                    host_id = self.pool.alloc()
                except RuntimeError:
                    return None
        with self._lock:
            client_id = self._next_id
            self._next_id += 1
            data_port = self.session_port_base + client_id - 1
            session = ClientSession(client_id, host_id, data_port, self.gateway, self.dns)
            self.sessions[client_id] = session
        return {
            'id': client_id,
            'ip': session.ip,
            'gateway': self.gateway,
            'dns': self.dns,
            'port': data_port,
            'subnet': self.network_ip,
        }

    def get(self, client_id: int) -> ClientSession | None:
        with self._lock:
            return self.sessions.get(client_id)

    def remove(self, client_id: int, host_id: int):
        with self._lock:
            self.sessions.pop(client_id, None)
        self.pool.release(host_id)

    def disconnect(self, client_id: int) -> bool:
        """Force-teardown a client session (idempotent). Returns True if found."""
        session = self.get(client_id)
        if session is None:
            return False
        session.stop.set()
        if session.tls_proc is not None:
            try:
                session.tls_proc.terminate()
            except ProcessLookupError:
                pass
        return True

    def route_client_for(self, dst_ip: ipaddress.IPv4Address):
        """Return the session whose tunnel IP matches dst_ip, or None."""
        target = str(dst_ip)
        with self._lock:
            for session in self.sessions.values():
                if session.ip == target:
                    return session
        return None

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [s.snapshot() for s in sorted(self.sessions.values(), key=lambda s: s.client_id)]

    def idle_sessions(self, timeout: float) -> list[ClientSession]:
        """Sessions with no traffic/keepalive for >= timeout seconds."""
        with self._lock:
            return [s for s in self.sessions.values() if s.idle_seconds() >= timeout]

    def reclaim_idle(self, idle_threshold: float) -> list[int]:
        """Reclaim sessions idle for >= idle_threshold seconds.

        Inline (no calls to locking helpers — self._lock is not reentrant).
        Returns the list of reclaimed client_ids.
        """
        reclaimed = []
        with self._lock:
            idle = [s for s in self.sessions.values()
                    if s.idle_seconds() >= idle_threshold]
            for session in idle:
                self.sessions.pop(session.client_id, None)
                self.pool.release(session.host_id)
                session.stop.set()
                reclaimed.append(session.client_id)
        # Terminate TLS subprocesses after releasing the lock (subprocess ops
        # can be slow and must not hold the manager lock).
        for session in idle:
            if session.tls_proc is not None:
                try:
                    session.tls_proc.terminate()
                except ProcessLookupError:
                    pass
        return reclaimed