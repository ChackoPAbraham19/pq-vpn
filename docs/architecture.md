# PQ-VPN Architecture

## Overview

PQ-VPN is a Linux TUN-based Layer-3 VPN tunnel that encrypts IP packets inside TLS 1.3 streams with dual-mode crypto support (classical ECDSA and post-quantum ML-DSA-65/ML-KEM-768).

The design decision to use `openssl s_server/s_client` subprocesses instead of Python's `ssl` module solves a fundamental limitation: Python's `ssl3_set_cert` hardcodes `EVP_PKEY` type IDs, making it unable to load OID-based signature algorithms like ML-DSA-65. OpenSSL >= 3.2 dispatches certificate signature verification to the provider interface, enabling pure PQC certificates.

```mermaid
flowchart TB
    subgraph Browser["Laptop browser (optional)"]
        UI["Dashboard UI (Chart.js)"]
        DB["dashboard_server.py (FastAPI+WS)"]
        UI <--"/ws/status"--> DB
    end

    subgraph ClientHost["Client Host (vpn_ns2 netns)"]
        C_APP["Application (ping, curl, iperf3)"]
        C_TUN["tun0 (10.8.0.2/24)"]
        C_SEAL["vpn_client daemon"]
        C_TLS["openssl s_client\n(TLS 1.3, PQC/classical, ticket cache)"]
        C_APP --> C_TUN
        C_TUN --"raw IP packets"--> C_SEAL
        C_SEAL --"frame type+len+payload"--> C_TLS
        C_TLS <--"veth pair to host 192.168.200.2"--> HOSTROUTE["host bridge/forward"]
    end

    subgraph Server["Server Host (vpn_ns1 netns)"]
        S_WELCOME["python welcome accept loop :14430"]
        S_POOL["SessionManager\nIP pool 10.8.0.2-.4 / ports 14431+"]
        S_TLS1["s_server #1 -accept 14431"]
        S_TLS2["s_server #2 -accept 14432"]
        S_PROXY["vpn_server daemon"]
        S_TUN["tun0 (10.8.0.1/24)"]
        S_DEMUX["shared TUN reader → per-client relay\ndemux by dst IP"]
        S_NAT["NAT MASQUERADE + dnsmasq"]
        S_VETH["veth-ns1 (192.168.100.2)"]

        S_WELCOME --> S_POOL
        S_POOL --> S_TLS1
        S_POOL --> S_TLS2
        S_TLS1 --"per-client pipes"--> S_PROXY
        S_TLS2 --"per-client pipes"--> S_PROXY
        S_PROXY --> S_DEMUX
        S_DEMUX --> S_TUN
        S_TUN --> S_NAT
        S_NAT --> S_VETH
    end

    DB --"poll /tmp/pqvpn-*.sock\nPOST /disconnect/{id}"--> C_SEAL
    DB --"poll /tmp/pqvpn-*.sock\nassigns IP/port, idle teardown"--> S_PROXY

    C_TLS --"TLS 1.3 (classical / X25519MLKEM768)\nframed tunnel data + keepalives"--> S_TLS1
    C_TLS --"welcome config-push {ip,port,gw,dns}"--> S_WELCOME
    HOSTROUTE <--> S_VETH
```

The client opens the welcome channel first (thin TCP), receives a config-push
control frame assigning its tunnel IP, gateway, DNS, and a dedicated data port;
it then opens the real TLS data connection to that port. The server runs one
`openssl s_server` per client and a shared TUN reader demuxes inbound tunnel
packets to the right client by destination IP.

## Data Plane

The tunnel carries raw IP packets (no Ethernet headers) through the TUN device. Every byte on the wire is wrapped in a typed frame (control vs data) so keepalives, config-push, and tunnel packets share one stream:

```
┌─────────┬────────────┬──────────────────┐
│ 1 byte  │ 4-byte BE  │      payload     │
│  type   │  length    │ (data or control)│
└─────────┴────────────┴──────────────────┘
```

The framing protocol (`common/framing.py`):

```python
TYPE_DATA = 0x00; TYPE_CONTROL = 0x01
HEADER_LEN = 1 + 4   # [type:1][length:4][payload]
pack_frame(data, ftype=TYPE_DATA)      # prepend type+length header
unpack_frames(buffer)                  # yields (ftype, payload)
pack_config(config)                    # JSON config-push control frame
pack_keepalive() / is_keepalive()      # 'KA' control frame
unpack_config(buffer)                  # config-push from stream head
```

Packets are relayed by two daemon threads:
1. **TUN → TLS**: `select()` + `os.read(tun_fd)` → pack → `os.write(tls_stdFd)`
2. **TLS → TUN**: accumulate TCP stream bytes in buffer → unpack → `os.write(tun_fd)`

Both use 500ms polling intervals and exit on `BrokenPipeError` or `Eof` (stopping signal). Idle sessions are torn down by a keepalive-based idle monitor, and the client reconnects with exponential backoff (1→30s).

## TLS Configuration

### www.openssl.cnf (via OpenSSL flags)

**Classical mode** (default OpenSSL, no provider flags):
```
openssl s_server -accept 0.0.0.0:14430 -cert server.crt -key server.key -CAfile ca.crt -Verify 1 -quiet
```

**PQC mode** (custom OpenSSL 3.4.7):
```
/opt/openssl-pqc/bin/openssl s_server \
  -provider oqsprovider -provider default \
  -groups X25519MLKEM768 \
  -accept 0.0.0.0:14430 -cert server.crt -key server.key -CAfile ca.crt -Verify 1 -quiet
```

Environment setup (`common/openssl_wrapper.py:`:

```python
def _make_env(mode, config_dir):
    if mode == 'pqc':
        env['PATH'] = '/opt/openssl-pqc/bin:' + env['PATH']
        env['LD_LIBRARY_PATH'] = '/opt/openssl-pqc/lib64:' + env.get('LD_LIBRARY_PATH', '')
        env.pop('LD_PRELOAD', None)
        env['OPENSSL_CONF'] = 'pki/openssl-pqc-runtime.cnf'  # SECLEVEL=0 only
    return env
```

## Certificate Chains

Both PKI trees use a 3-certificate chain (CA → server, CA → client). All certs are generated with 10-year expiry (3650 days).

### Classic PKI (`pki/certs/classical/`)

| Component | Algorithm | Key Size |
|-----------|-----------|----------|
| CA certificate | ECDSA P-256 Self-signed | ~128-bit security |
| Server cert | ECDSA P-256 Signed by CA | ~128-bit |
| Client cert | ECDSA P-256 Signed by CA | ~128-bit |
| TLS group | X25519 (default) | ~128-bit |

### PQC PKI (`pki/certs/pqc/`)

| Component | OpenSSL Version | Algorithm | Security Level |
|-----------|----------------|-----------|----------------|
| CA cert | Custom 3.4.7 | ML-DSA-65 (dilithium3) | NIST Level 3 |
| Server cert | Custom 3.4.7 | ML-DSA-65 | NIST Level 3 |
| Client cert | Custom 3.4.7 | ML-DSA-65 | NIST Level 3 |
| TLS handshake | Custom 3.4.7 | X25519MLKEM768 | NIST Level 3 |

Cert algorithms verified empirically (`openssl x509 -text`): CA, server, and
client certs are all `mldsa65` (OID 2.16.840.1.101.3.4.3.18), and the TLS 1.3
CertificateVerify therefore also uses `mldsa65` — the scheme must match the
cert key type, so the live `Signature type: mldsa65` in s_client brief output
is genuine post-quantum authentication, not a negotiation artifact.

## Network Namespace Test Harness

Testing entirely within a single Linux machine:

```
                    ┌──────────────────┐
                    │   Host (default)  │
                    │                    │
              ┌─────── veth-h (10.0)  │◄──── veth-h2 (192.168.200.2)────┐
              │     │  veth-p1 (direct) │                        │     │
              │     └─────────────────┘                        │     │
              │                                                │     │
      ┌───────▼──────┐                                           ┌───────▼──────┐
      │  vpn_ns1      │    veth peer 10.0.10.0/24             │  vpn_ns2      │
      │ (server)     │◄──────────────────────────────────────►│ (client)     │
      │ 192.168.100.2 │     veth-p1 <-> veth-p2                │ 192.168.200.2 │
      │ tun0: 10.8.0.1 │                                           │ tun0: 10.8.0.2 │
      │ NAT+FORWARD  │                                           │ no nat       │
      └──────────────┘                                           └──────────────┘
```

Three veth pairs:
1. **host↔ns1**: Host forward to 192.168.100.0/24 (server's network)
2. **host↔ns2**: Host forward to 192.168.200.0/24 (client's network)
3. **ns1□ns2**: Direct peer crossing 192.168.10.0/24 for direct icless ping

Forwarding rules:
- All veth interfaces have `rp_filter=0`
- `ip_v4_forward=1` on host
- Host bridge `iptables` allow all forward between veth-h and veth-h2
- vpn_ns1 has MASQUERADE NET for TUN subnet 10.8.0.0/24

## IPC Layer

Daemons expose a Unix domain socket (`/tmp/pqvpn-server.sock`, `/tmp/pqvpn-client.sock`) for IPC with the CLI and dashboard:

```python
# common/daemon_utils.py
class StatusTracker:   # thread-safe state tracking (bytes, uptime, crypto mode, group, resumed)
class IpcServer:       # Unix domain socket server on daemon thread
class Writer:          # PID/socket file operations
```

Two request shapes:

- **Bare request** (vpnctl): `connect → send JSON query → recv JSON response` returning the status snapshot.
- **Command request**: `{'command': 'disconnect', 'id': ...}` → the daemon's `command_handler` runs it and replies with `{'status': {...}, 'result': {...}}`. Implemented commands: `disconnect` (force session teardown), `clients` (list tunnels), `status`.

The server uses a 100ms `select()` wait so bare status polls never block on a full 2s recv timeout. The dashboard polls both sockets every 0.5s and additionally exposes `POST /disconnect/{id}` to kick a tunnel from the browser.

## Security Considerations

| Concern | Mitigation |
|---------|------------|
| Memory exposure | OpenSSL subprocess isolation; child process can crash but data is confined to pipe buffer |
| Certs stored in plain | `pki/certs/` in `.gitignore`, never committed |
| PQC cert fingerprinting | `-groups X25519MLKEM768` hides ML-KEM from non-PQC peer |
| Compromised PKI | Both modes always require mutual TLS authentication: client and server both present certs |
| Network namespace-level censoring | `setup_routing.sh` creates isolated netns; no exposed interfaces except via defined rules |

### Limitations

- No mid-stream key rotation (s_server has no renegotiation); rekey = reconnect (ticket resumption keeps it cheap)
- **Session resumption is connection-lifetime only**: each client session gets a
  fresh `s_server` process, which generates fresh TLS ticket-encryption keys.
  A cached ticket from a previous server lifetime therefore never resumes
  (the handshake silently falls back to full). Status reports `resumed` only
  on hard evidence (cert exchange skipped), so it reads `false` across
  restarts even with a populated ticket cache. Fixing this needs a persistent
  ticket-key broker (Future Work).
- Python GIL-bound CPU limit, ~700 Mbps TCP/UDP throughput measured (IP limits, not crypto)
- NAT/dnsmasq/default-route plumbing is config-driven (`server/net_config.py`,
  `client/net_config.py`) and validated via `--dry-run` unit tests; the live
  netns run is pending
- Dashboard live-browser verification pending (needs sudo for the netns test harness)

## Session Resumption

The client caches the server's TLS 1.3 session ticket per server (`common/ticket_cache.py`, `~/.pqvpn/cache/<host>.<sha16>.tick`). On reconnect it passes the ticket via `-sess_in` and refreshes it with `-sess_out`, so the resumed handshake skips the KEM and certificate exchange (verified `Reused, TLSv1.3` for both classical and `X25519MLKEM768`). Measured over loopback: classical 15.0→13.5ms, PQC 24.4→23.7ms median handshake; the real saving is one WAN RTT. Toggle with `resume_cache` in the client config.