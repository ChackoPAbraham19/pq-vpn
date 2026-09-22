#!/usr/bin/env python3
"""session_manager.py -- multi-client sessions for the PQ-VPN server.

Each accepted welcome connection is assigned a client id, a tunnel IP, and a
dedicated TLS data port. The server then runs one `openssl s_server` subprocess
per client on that data port, and a shared TUN reader demuxes inbound packets
to the correct client by destination IP.

Ports are bound to IP *slots*, not to the monotonically increasing client id:
slot 0 (first pool address) always uses data_port_base, slot 1 uses base+1, and
so on. A reconnecting client therefore never drifts outside the
`base .. base+max_clients-1` range that the firewall opens.
"""
import ipaddress
import subprocess
import threading
import time

DATA_PORT_BASE = 14431  # welcome port = base - 1; per-client ports start here


def ipv4_dst(packet: bytes):
    """Return the destination IPv4 address of a tunnel packet, or None."""
    if len(packet) < 20 or (packet[0] >> 4) != 4:
        return None
    return bytes(packet[16:20])


class IpPool:
    def __init__(self, network: str, reserved: list[str] | None = None,
                 size: int | None = None):
        """Allocate tunnel addresses from network (e.g. '10.8.0.0/24').

        Optionally exclude `reserved` addresses (like the server's own .1) and
        cap the pool at `size` addresses so slot indices (and therefore data
        ports) stay within a fixed range.
        """
        self._net = ipaddress.ip_network(network, strict=False)
        reserved = {ipaddress.ip_address(a) for a in (reserved or [])}
        hosts = [a for a in self._net.hosts() if a not in reserved]
        if size is not None:
            hosts = hosts[:size]
        if not hosts:
            raise ValueError(f'IP pool {network} has no usable addresses')
        # Slot index = position in this fixed list; host_id = last octet key.
        self._slots: dict[int, int] = {int(a.packed[-1]): i for i, a in enumerate(hosts)}
        self._addrs: dict[int, ipaddress.IPv4Address] = {int(a.packed[-1]): a for a in hosts}
        self._free = list(hosts)
        self._lock = threading.Lock()
        self._in_use: dict[int, ipaddress.IPv4Address] = {}

    @property
    def size(self) -> int:
        return len(self._addrs)

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
            # FIFO: a just-freed slot goes to the back so its old s_server
            # (already drained by the caller) is the last thing reused.
            if addr is not None:
                self._free.append(addr)

    def slot_of(self, host_id: int) -> int:
        """Stable 0-based slot index for a host id."""
        return self._slots[host_id]

    def address_for(self, host_id: int) -> ipaddress.IPv4Address:
        return self._addrs[host_id]

    def list(self) -> list[str]:
        with self._lock:
            return [str(a) for a in sorted(self._in_use.values())]


class WelcomeLimiter:
    """Per-source-IP token bucket for the plaintext welcome port.

    `handshake_timeout` bounds what ONE fake claim costs (a slot for ~10 s);
    this bounds how many claims a single source can make, so a looping `nc`
    cannot keep the pool full. A real client's reconnects arrive at the
    backoff cadence (1 -> 30 s), far under the default 1/s with burst 3.
    """

    def __init__(self, rate: float = 1.0, burst: int = 3, idle_expiry: float = 60.0):
        self.rate = float(rate)
        self.burst = float(burst)
        self.idle_expiry = idle_expiry
        self._buckets: dict[str, tuple[float, float]] = {}  # ip -> (tokens, last)
        self._lock = threading.Lock()

    def allow(self, ip: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            tokens, last = self._buckets.get(ip, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            ok = tokens >= 1.0
            if ok:
                tokens -= 1.0
            self._buckets[ip] = (tokens, now)
            if len(self._buckets) > 256:
                self._prune(now)
            return ok

    def _prune(self, now: float):
        dead = [ip for ip, (_, last) in self._buckets.items()
                if now - last >= self.idle_expiry]
        for ip in dead:
            del self._buckets[ip]

    def __len__(self) -> int:
        with self._lock:
            return len(self._buckets)


class ClientSession:
    def __init__(self, client_id: int, host_id: int, ip: str, data_port: int,
                 gateway: str, dns: str):
        self.client_id = client_id
        self.host_id = host_id
        self.ip = ip
        self.data_port = data_port
        self.gateway = gateway
        self.dns = dns
        self.stop = threading.Event()
        self.tls_proc = None
        self.relay_in_thread = None
        self.relay_out_thread = None
        self.bytes_in = 0
        self.bytes_out = 0
        self.created = time.monotonic()
        self.last_seen = self.created
        # Set once the first TLS application-data chunk arrives, i.e. the
        # client completed mTLS. Until then the session is only a slot claim.
        self.connected = False
        self.name = ''     # from the client's post-mTLS hello frame (display only)
        self._lock = threading.Lock()

    def touch(self):
        with self._lock:
            self.last_seen = time.monotonic()

    def mark_connected(self):
        with self._lock:
            self.connected = True
            self.last_seen = time.monotonic()

    def idle_seconds(self) -> float:
        with self._lock:
            return time.monotonic() - self.last_seen

    def age_seconds(self) -> float:
        return time.monotonic() - self.created

    def snapshot(self) -> dict:
        return {
            'id': self.client_id,
            'ip': self.ip,
            'port': self.data_port,
            'gateway': self.gateway,
            'dns': self.dns,
            'bytes_in': self.bytes_in,
            'bytes_out': self.bytes_out,
            'connected': self.connected,
            'name': self.name,
            'idle_seconds': round(self.idle_seconds(), 1),
        }


def _drain_proc(proc, timeout: float = 2.0):
    """Make sure a per-client s_server has exited so its port is free.

    terminate() alone is not enough: the slot (and port) must not be handed
    to a new client while the old process could still be bound to it.
    """
    if proc is None:
        return
    try:
        proc.terminate()
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=timeout)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass


class SessionManager:
    def __init__(self, config: dict):
        self.config = config
        self.gateway = config.get('gateway', '10.8.0.1')
        self.dns = config.get('dns', '10.8.0.1')
        net = config.get('tun_net', '10.8.0.0/24')
        self.max_clients = int(config.get('max_clients', 3))
        self.pool = IpPool(net, reserved=[self.gateway], size=self.max_clients)
        prefix = ipaddress.ip_network(net, strict=False).prefixlen
        self.network_ip = f'{self.gateway}/{prefix}'
        self.session_port_base = config.get('data_port_base', DATA_PORT_BASE)
        self.sessions: dict[int, ClientSession] = {}
        self._by_ip: dict[bytes, ClientSession] = {}  # packed dst -> session
        self._lock = threading.Lock()
        self._next_id = 1

    @property
    def port_range(self) -> tuple[int, int]:
        """Inclusive (first, last) data port ever handed out."""
        return self.session_port_base, self.session_port_base + self.pool.size - 1

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
        addr = self.pool.address_for(host_id)
        data_port = self.session_port_base + self.pool.slot_of(host_id)
        with self._lock:
            client_id = self._next_id
            self._next_id += 1
            session = ClientSession(client_id, host_id, str(addr), data_port,
                                    self.gateway, self.dns)
            self.sessions[client_id] = session
            self._by_ip[addr.packed] = session
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

    def lookup(self, dst: bytes) -> ClientSession | None:
        """Session owning a packed 4-byte destination address, or None."""
        with self._lock:
            return self._by_ip.get(dst)

    def _forget(self, session: ClientSession):
        """Drop a session from the tables (caller holds self._lock)."""
        self.sessions.pop(session.client_id, None)
        packed = ipaddress.ip_address(session.ip).packed
        if self._by_ip.get(packed) is session:
            self._by_ip.pop(packed, None)

    def remove(self, client_id: int, host_id: int):
        """Forget a session and free its slot once its s_server has exited."""
        with self._lock:
            session = self.sessions.get(client_id)
            if session is not None:
                self._forget(session)
        if session is not None:
            session.stop.set()
            _drain_proc(session.tls_proc)
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

    def disconnect_by_name(self, name: str) -> list[int]:
        """Force-teardown every session that announced `name`. Returns ids.

        Used after a certificate revocation: the CRL stops the next handshake,
        this stops the session that is already up.
        """
        with self._lock:
            targets = [s for s in self.sessions.values() if s.name == name]
        for session in targets:
            self.disconnect(session.client_id)
        return [s.client_id for s in targets]

    def route_client_for(self, dst_ip: ipaddress.IPv4Address):
        """Return the session whose tunnel IP matches dst_ip, or None."""
        return self.lookup(ipaddress.ip_address(dst_ip).packed)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [s.snapshot() for s in sorted(self.sessions.values(), key=lambda s: s.client_id)]

    def idle_sessions(self, timeout: float) -> list[ClientSession]:
        """Sessions with no traffic/keepalive for >= timeout seconds."""
        with self._lock:
            return [s for s in self.sessions.values() if s.idle_seconds() >= timeout]

    def stale_handshakes(self, timeout: float) -> list[ClientSession]:
        """Sessions that claimed a slot but never completed mTLS within timeout.

        This is what bounds the cost of an unauthenticated welcome connection.
        """
        with self._lock:
            return [s for s in self.sessions.values()
                    if not s.connected and s.age_seconds() >= timeout]

    def reclaim_idle(self, idle_threshold: float) -> list[int]:
        """Reclaim sessions idle for >= idle_threshold seconds.

        Inline (no calls to locking helpers — self._lock is not reentrant).
        Returns the list of reclaimed client_ids.
        """
        with self._lock:
            idle = [s for s in self.sessions.values()
                    if s.idle_seconds() >= idle_threshold]
            for session in idle:
                self._forget(session)
                session.stop.set()
        # Drain the TLS subprocesses after releasing the lock (subprocess ops
        # can be slow and must not hold the manager lock), and only THEN free
        # the slot so its port cannot be reused while the old process lives.
        for session in idle:
            _drain_proc(session.tls_proc)
            self.pool.release(session.host_id)
        return [s.client_id for s in idle]
