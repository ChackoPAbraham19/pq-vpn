# Post-Quantum Secure VPN — Implementation Plan

**Project type:** Major Project — Client + Server VPN prototype with PQC integration, benchmarking, and UI
**Target OS:** Linux (Ubuntu 22.04 / 24.04)
**Core crypto stack:** liboqs + oqs-provider (OpenSSL 3.x) — ML-KEM (Kyber) KEM, ML-DSA (Dilithium) signatures
**Daemon language:** Python with OpenSSL subprocess (Option B)

---

## Architecture

```
┌────────────────────┐                                   ┌────────────────────┐
│     Client Host     │                                   │     Server Host     │
│  (vpn_ns2 netns)    │                                   │  (vpn_ns1 netns)    │
│                      │                                   │                      │
│  App traffic         │                                   │  App traffic         │
│      │                │                                   │      ▲                │
│      ▼                │                                   │      │                │
│  tun0 (10.8.0.2/24)  │                                   │  tun0 (10.8.0.1/24)  │
│      │                │     stdin/stdout pipes over       │      │                │
│      ▼                │  openssl s_client / s_server       │      ▼                │
│  vpn_client daemon    │◄─────────────────────────────────►│  vpn_server daemon    │
│  (TUN ↔ TLS relay)   │    TLS 1.3 (PQC KEM/Sig)         │  (TLS ↔ TUN relay)   │
│                      │                                   │                      │
│  veth → ns1 bridge   │                                   │  NAT MASQUERADE      │
└────────────────────┘                                   └────────────────────┘
```

**Data plane:** raw IP packets from TUN, length-prefixed inside TLS stream.
**Control/handshake:** standard TLS 1.3, with PQC groups and PQC-signed certs.

---

## Stage Dependency Graph

```
Stage 1 ──► [Stage 2 ──► Stage 3] ──► Stage 4
  (setup)    (liboqs)  (oqs-prov)     (PKI)
                                         │
                                         ▼
Stage 5 ──► Stage 6 ──► Stage 7     Stage 8 ◄──┐
(common)   (classic)   (pqc swap)   (CLI)       │
                                    ▲           │
Stage 9 ──► Stage 10 ───────────────┘           │
(bench     (run full                           │
 scripts)   benchmarks) ───────────────────────┘
                                                  ▼
                                            Stage 11
                                             (docs)
```

---

## Stage-by-Stage Breakdown

### Stage 1: Foundation — Repo Structure + Build Scripts

Creates the project skeleton:
- Full directory tree (`pq-vpn/` with all subdirs)
- `requirements.txt` (click, PyYAML, matplotlib, psutil)
- `.gitignore` (excludes certs, CSVs, caches)
- `scripts/install_liboqs.sh` — automate liboqs git clone + cmake+ninja build
- `scripts/install_oqs_provider.sh` — automate oqs-provider build against `/opt/liboqs`
- `scripts/setup_routing.sh` — network namespace orchestration (vpn_ns1, vpn_ns2, veth pair, TUN, NAT)

**Verification:** `tree pq-vpn/` shows structure, `bash -n scripts/*.sh` passes, `pip3 install -r requirements.txt` succeeds.

---

### Stage 2: Build liboqs

Execute `scripts/install_liboqs.sh`:
- Clones `https://github.com/open-quantum-safe/liboqs.git` (main branch)
- cmake + ninja build, installs to `/opt/liboqs`

**Verification:**
```bash
ls /opt/liboqs/lib/liboqs.so
ls /opt/liboqs/include/oqs/
```

---

### Stage 3: Build oqs-provider

Execute `scripts/install_oqs_provider.sh`:
- Clones `https://github.com/open-quantum-safe/oqs-provider.git`
- cmake against `liboqs_DIR=/opt/liboqs/lib/cmake/liboqs`, installs to `/usr/local/lib/ossl-modules/`

**Verification:**
```bash
openssl list -providers | grep oqsprovider
openssl list -kem-algorithms -provider oqsprovider -provider default | grep mlkem
openssl list -signature-algorithms -provider oqsprovider -provider default | grep mldsa
```

---

### Stage 4: PKI — Classical + PQC Certificate Chains

Generate two parallel PKI trees:

| PKI | CA Algorithm | Server/Client Algorithm | Key size |
|---|---|---|---|
| Classical | ECDSA P-256 | ECDSA P-256 | ~256-bit security |
| PQC | ML-DSA-65 | ML-DSA-65 | NIST Level 3 |

Scripts:
- `pki/gen_classical_ca.sh` — CA + server + client certs (ECDSA P-256)
- `pki/gen_pqc_ca.sh` — CA + server + client certs (ML-DSA-65 via oqsprovider)

**Verification:**
```bash
openssl verify -CAfile pki/certs/classical/ca.crt pki/certs/classical/server.crt
openssl verify -CAfile pki/certs/pqc/ca.crt pki/certs/pqc/server.crt
```

---

### Stage 5: Shared Library Modules (`common/`)

| Module | Purpose |
|---|---|
| `common/framing.py` | `pack_frame(raw_ip_bytes)` → 4-byte BE length + bytes; `unpack_frame(stream)` → generator yielding complete packets |
| `common/tun_utils.py` | `create_tun(name, addr)` → fd + network config via `ip` commands |
| `common/openssl_wrapper.py` | `start_tls_server(ctx_config)` / `start_tls_client(ctx_config)` — launches `openssl s_server`/`s_client` subprocess with pipes for stdin/stdout. Handles `-groups`, `-provider` flags, cert/key/CA paths based on mode config |

**Verification:** Round-trip framing test, subprocess TLS connection test (standalone, no TUN yet).

---

### Stage 6: Classical VPN Daemon + Full Tunnel Test

**Daemon logic (identical for server/client, direction swapped):**
1. Create TUN interface via `tun_utils`
2. Start TLS subprocess (server listens, client connects)
3. Relay thread 1: read TUN fd → pack frame → write to TLS stdin
4. Relay thread 2: read TLS stdout → unpack frame → write to TUN fd
5. SIGTERM/SIGINT handler for clean teardown

**Config files:** `server/config.yaml` (port, TUN addr, cert paths, mode=classical) and `client/config.yaml` (server addr, TUN addr, cert paths, mode=classical).

**Networking:** `scripts/setup_routing.sh` creates two network namespaces (`vpn_ns1`=server, `vpn_ns2`=client) bridged by a veth pair, each with its own TUN. IP forwarding + NAT MASQUERADE in ns1.

**Verification:**
1. `sudo bash scripts/setup_routing.sh`
2. Start server in vpn_ns1, start client in vpn_ns2
3. `sudo ip netns exec vpn_ns2 ping -c 5 10.8.0.1` → ICMP through tunnel ✓
4. Transfer 10MB random file through tunnel, verify `sha256sum` matches both ends ✓

---

### Stage 7: PQC Swap — Same Daemon, PQC Crypto

Minimal changes — the daemon relay logic is crypto-agnostic:

**Changes needed:**
- `common/openssl_wrapper.py`: add `-provider oqsprovider -provider default` + `-groups X25519MLKEM768` flags when mode="pqc"
- `server/config.yaml` + `client/config.yaml`: set `mode: pqc`, point cert paths to `pki/certs/pqc/`

**Verification:**
- Repeat Stage 6 test suite with PQC config
- Confirm via tcpdump that ClientHello is significantly larger (~2KB+ vs ~500 bytes)

---

### Stage 8: CLI Tool (`vpnctl.py`)

Commands:

```
vpnctl connect --config client/config.yaml   # Start daemon, bring up tunnel
vpnctl disconnect                             # SIGTERM daemon, clean teardown
vpnctl status                                 # Read JSON from /tmp/pqvpn.sock
```

Implementation:
- Daemon exposes Unix domain socket (`/tmp/pqvpn.sock`) pushing JSON status every 2s
- Daemon writes PID file (`/tmp/pqvpn.pid`) and state file
- `vpnctl` wraps subprocess daemon launch, socket reads for status

---

### Stage 9: Benchmark Scripts + Tooling

Tools installed: `iperf3`, `matplotlib`, `psutil`

| Script | What it measures | Output |
|---|---|---|
| `run_handshake_bench.py` | TLS connection setup time (N trials) | CSV: trial_id, mode, ms |
| `run_latency_bench.sh` | `ping -c 100` through tunnel | CSV: rtt_min/avg/max/mdev |
| `run_throughput_bench.sh` | `iperf3` TCP and UDP through tunnel | CSV: Mbps per mode |
| `run_file_transfer_bench.sh` | Transfer time of 100MB file | CSV: seconds, sha256 ok |
| `plot_results.py` | Bar charts + summary table | PNGs in `benchmark/results/` |

---

### Stage 10: Execute Full Benchmark Suite

Clean-state runs for both classical and PQC modes, N≥20 trials each:
- Handshake time
- Latency (ping 100)
- Throughput (iperf3 TCP + UDP)
- File transfer (100MB)
- CPU/memory (psutil snapshots during steady-state)

Expected outcomes: PQC handshake 2–10x slower (larger certs + key shares), throughput near-identical, latency near-identical.

---

### Stage 11: Documentation + Final Polish

| Doc | Content |
|---|---|
| `docs/setup.md` | Fresh reproduction guide, all commands from bare Ubuntu to running tunnel |
| `docs/architecture.md` | Mermaid diagrams, component breakdown, crypto config details |
| `docs/benchmark-results.md` | Interpreted results with embedded graphs, PQC overhead analysis |
| `README.md` | Project overview, quick-start 3 commands, demo instructions |

**Final verification:** Clean-machine walkthrough following `docs/setup.md`, demo runs end-to-end in <5 minutes.

---

## Technology Stack

| Component | Technology |
|---|---|
| OS | Ubuntu 24.04 |
| PQC library | liboqs |
| TLS integration | oqs-provider on custom OpenSSL 3.4.7 (`/opt/openssl-pqc`) — system 3.0.13 / Python `ssl` cannot load ML-DSA certs |
| PQC KEM | ML-KEM-768 (Kyber768), hybrid `X25519MLKEM768` |
| PQC signatures | ML-DSA-65 (Dilithium3) |
| Tunnel daemon | Python 3.12 with `openssl s_server/s_client` subprocess |
| TUN interface | `/dev/net/tun` via `ip tuntap` |
| NAT/forwarding | `iptables`, `sysctl net.ipv4.ip_forward` |
| Network test harness | Linux network namespaces + veth pairs |
| Benchmarking | `iperf3`, `ping`, `openssl s_time`, `time`/`perf` |
| CLI | Python `click` + Unix domain socket |
| Docs | Markdown with Mermaid diagrams |

---

## Key Risks & Mitigations

| Risk | Mitigation |
|---|---|
| PQC handshake packets exceed MTU | Set tun0 MTU to 1400; test with large PQC certs early |
| Python's `ssl` module limits oqs-provider group selection | Using `openssl s_server/s_client` subprocess with `-groups` flag avoids this entirely |
| Single machine only (no second VM) | Network namespace test harness eliminates need for second machine |
| Benchmark noise | 20+ trials per metric, report median + stddev |
| liboqs/oqs-provider build failures | Scripted, idempotent build scripts; test on Ubuntu 24.04 standard |

---

## References

- liboqs: https://github.com/open-quantum-safe/liboqs
- oqs-provider: https://github.com/open-quantum-safe/oqs-provider
- Open Quantum Safe TLS integrations: https://openquantumsafe.org/applications/tls.html
- NIST FIPS 203 (ML-KEM) and FIPS 204 (ML-DSA)