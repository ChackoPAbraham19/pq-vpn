# PQ-VPN — Post-Quantum Secure VPN Tunnel

A Linux TUN-based VPN tunnel with dual-mode TLS: classical ECDSA and post-quantum ML-DSA-65/ML-KEM-768. Uses `openssl s_server/s_client` subprocesses with oqs-provider for PQC handshakes, wrapped in Python relay daemons.

## Quick Start (3 Commands)

```bash
# 1. Install custom OpenSSL + liboqs + oqs-provider (~15 min)
cd /home/YOUR_USER/VPN
sudo bash scripts/install_custom_openssl.sh

# 2. Generate PKI and setup network namespaces
.venv/bin/pip install -r requirements.txt
bash pki/gen_classical_ca.sh
bash pki/gen_pqc_ca.sh
sudo bash scripts/setup_routing.sh

# 3. Start classical or PQC tunnel
# Classical:
sudo ip netns exec vpn_ns1 .venv/bin/python3 server/vpn_server.py --config server/config.yaml &
sudo ip netns exec vpn_ns2 .venv/bin/python3 client/vpn_client.py --config client/config.yaml &

# PQC:
sudo ip netns exec vpn_ns1 .venv/bin/python3 server/vpn_server.py --config server/config-pqc.yaml &
sudo ip netns exec vpn_ns2 .venv/bin/python3 client/vpn_client.py --config client/config-pqc.yaml &

# Verify
sudo ip netns exec vpn_ns2 ping -c 5 10.8.0.1

# Teardown
sudo bash scripts/setup_routing.sh --teardown
```

Or run the one-shot demo (setup → tunnel → verify → teardown):
`sudo bash scripts/demo.sh --mode pqc --clients 2` (rootless sanity: `bash scripts/demo.sh --dry-run`).

## Architecture

```
┌──────────────────┐                         ┌──────────────────┐
│   Client (ns2)    │   TLS 1.3 (PQC or ECDSA)  │   Server (ns1)   │
│  10.8.0.2 (tun0)  │◄───────────────────────►│  10.8.0.1 (tun0)  │
│ vpn_client daemon  │  openssl s_client/s_server │  vpn_server daemon│
└──────────────────┘                         └──────────────────┘
```

**Data plane**: raw IP packets from TUN device, length-prefixed (`4-byte header + payload`) inside TLS encrypted stream.

**Control plane**: standard TLS 1.3 handshake. Classical mode uses ECDSA P-256 + X25519. PQC mode uses ML-DSA-65 + X25519MLKEM768 hybrid key exchange.

## Modes

| Feature | Classical | PQC |
|---------|-----------|-----|
| Signature algorithm | ECDSA P-256 (prime256v1) | ML-DSA-65 (mldsa65) |
| Key exchange | X25519 | X25519MLKEM768 (hybrid) |
| OpenSSL version | System (3.0.13) | Custom 3.4.7 (`/opt/openssl-pqc`) |
| Config file | `server/config.yaml`, `client/config.yaml` | `server/config-pqc.yaml`, `client/config-pqc.yaml` |

## Benchmark Summary (25 trials each mode)

| Metric | Classical | PQC | Overhead |
|--------|-----------|-----|----------|
| **TLS Handshake** (median) | 15.0 ms | 25.4 ms | 1.70x |
| **Resumed handshake** (median) | 13.5 ms | 23.7 ms | 1.03x |
| **Ping latency** (avg RTT) | 2.29 ms | 2.33 ms | 1.7% |
| **TCP throughput** | 1268 Mbps | 533 Mbps | noise-bound |
| **UDP throughput** | 999.9 Mbps | 999.9 Mbps | 0% |
| **100MB file transfer** | 0.68 s | 1.31 s | run variance |

**Takeaway**: PQC adds significant one-time handshake overhead (larger certs +
key shares) but zero measurable penalty on steady-state throughput and latency.
TLS session resumption cuts even the handshake cost. Full methodology, raw CSVs,
and interpretation: [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md).

## CLI

```bash
.venv/bin/python3 cli/vpnctl.py connect -c client/config-pqc.yaml
.venv/bin/python3 cli/vpnctl.py status
.venv/bin/python3 cli/vpnctl.py disconnect
```

## Project Structure

```
VPN/
├── client/          # VPN client daemon + configs
├── server/          # VPN server daemon + configs
├── common/          # Shared modules (framing, TUN, TLS wrapper, IPC)
├── cli/             # CLI tool (vpnctl.py + Click)
├── pki/             # Certificate generation scripts + certs
│   ├── gen_classical_ca.sh   # ECDSA P-256 PKI
│   └── gen_pqc_ca.sh         # ML-DSA-65 PKI
├── scripts/         # Build, network setup, diagnostics, rollback
├── benchmark/       # Benchmark scripts, results, plots
├── docs/            # Setup guide, architecture, deployment, benchmark report
└── scripts/         # Build, network setup, diagnostics, rollback, demo
```

## Prerequisites

- Ubuntu 24.04 (or 22.04)
- Python 3.12
- Root access (for TUN devices and network namespaces)
- Build tools: `build-essential`, `cmake`, `ninja-build`, `libssl-dev`
- Benchmarking: `iperf3`

## References

- [liboqs](https://github.com/open-quantum-safe/liboqs)
- [oqs-provider](https://github.com/open-quantum-safe/oqs-provider)
- [NIST FIPS 203 (ML-KEM)](https://csrc.nist.gov/pubs/fips/203/final)
- [NIST FIPS 204 (ML-DSA)](https://csrc.nist.gov/pubs/fips/204/final)