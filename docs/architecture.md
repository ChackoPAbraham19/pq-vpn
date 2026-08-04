# PQ-VPN Architecture

## Overview

PQ-VPN is a Linux TUN-based Layer-3 VPN tunnel that encrypts IP packets inside TLS 1.3 streams with dual-mode crypto support (classical ECDSA and post-quantum ML-DSA-65/ML-KEM-768).

The design decision to use `openssl s_server/s_client` subprocesses instead of Python's `ssl` module solves a fundamental limitation: Python's `ssl3_set_cert` hardcodes `EVP_PKEY` type IDs, making it unable to load OID-based signature algorithms like ML-DSA-65. OpenSSL >= 3.2 dispatches certificate signature verification to the provider interface, enabling pure PQC certificates.

```mermaid
flowchart TB
    subgraph ClientHost["Client Host (vpn_ns2 netns)"]
        C_APP["Application (ping, curl, iperf3)"]
        C_TUN["tun0 (10.8.0.2/24)"])
        C_SEAL["vpn_client daemon"]
        C_TLS["openssl s_client\n(TLS 1.3, PQC/classical)"]
        C_APP --> C_TUN
        C_TUN --"raw IP packets"--> C_SEAL
        C_SEAL --"pipe stdin/stdout"--> C_TLS
        C_TLS <--"veth pair to host"--> |"192.168.200.2"|
    end

    subgraph Host_["Host (192.168.100.1, 192.168.200.1)"]
        HOST_NET["ip_forward=1<br/>rp_filter=0<br/>FORWARD links all veth's"]
    end

    subgraph Server["Server Host (vpn_ns1 netns)"]
        S_VETH["veth-ns1 (192.168.100.2)"]
        S_TLS["subprocess s_server - TLS 1.3 (PQC/classical)"]
        S_PROXY["vpn_server daemon"]
        S_TUN["tun0 (10.8.0.1/24)"]
        S_NAT["NAT MASQUERADE<br/>TUN ↔ veth"]
        S_VETH <--pipe--> S_TLS
        S_TLS <--stdin/stdout--> S_PROXY
        S_PROXY <--raw IP packets--> S_TUN
        S_TUN --> S_NAT
        S_NAT --> S_VETH
    end

%% Connections
    Host_ <--> Client
    Host_ <--> Server
    Client --TLS encrypted tunnel--> Server
```

## Data Plane

The tunnel carries raw IP packets (no Ethernet headers) through the TUN device 11.

```
┌────────────┬────────────────┐
│ 4-byte BE  │ raw IP packet  │
│   length   │ (IPv4 20+bytes) │
└────────────┴────────────────┘
```

The framing protocol (`common/framing.py`):

```python
HEADER_LEN = 4          # 4-byte big-endian uint32
pack_frame(data):        # prepend length header
unpack_frames(buffer):   # generator yielding complete frames
```

Packets are relayed by two daemon threads:
1. **TUN → TLS**: `select()` + `os.read(tun_fd)` → pack → `os.write(tls_stdFd)`
2. **TLS → TUN**: accumulate TCP stream bytes in buffer → unpack → `os.write(tun_fd)`

Both use 500ms polling intervals and exit on `BrokenPipeError` or `Eof` (stopping signal).

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

Daemons expose a Unix domain socket for IPC with the CLI:

```python
# common/daemon_utils.py
class StatusTracker:   # thread-safe state tracking
class IpcServer:       # Unix domain socket server on daemon thread
class Writer:          # PID/socket file operations
```

P protocol:
-client->socket connect → send JSON query → recv JSON response → show status (including bytes in/out, uptime, TUN address, crypto mode, group)

## Security Considerations

| Concern | Mitigation |
|---------|------------|
| Memory exposure | OpenSSL subprocess isolation; child process can crash but data is confined to pipe buffer |
| Certs stored in plain | `pki/certs/` in `.gitignore`, never committed |
| PQC cert fingerprinting | `-groups X25519MLPp` hides ML-KEM from non-PQC peer |
| Compromised PKI | Both modes always require mutual TLS authentication: client and server both present certs |
| Network namespace-level censoring | `setup_routing.sh` creates isolated netns; no exposed interfaces except via defined rules |

### Limitations

- Single-client model (server accepts exactly one client)
- No key rotation or session resumption in the current benchmark mode
- Python GIL-bound CPU limit, ~700 Mbps TCP/UDP throughput measured (IP limits, not crypto)
- No DNS routing implemented in daemon (depends on external DNS namespace resolv.conf configuration)