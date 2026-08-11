# PQ-VPN Implementation TODO

## Stage 1: Foundation — Repo Structure + Build Scripts [completed]
- [x] Create directory tree
- [x] Write `requirements.txt`
- [x] Write `.gitignore`
- [x] Write `scripts/install_liboqs.sh`
- [x] Write `scripts/install_oqs_provider.sh`
- [x] Write `scripts/setup_routing.sh`

## Stage 2: Build liboqs [completed]
- [x] Execute `scripts/install_liboqs.sh`
- [x] Verify `/opt/liboqs/lib/liboqs.so` exists
- [x] Verify `/opt/liboqs/include/oqs/` populated

## Stage 3: Build oqs-provider [completed]
- [x] Execute `scripts/install_oqs_provider.sh`
- [x] Write `scripts/enable_oqsprovider.sh` → generates project-scoped `pki/openssl-pqc.cnf` (no system files touched)
- [x] Verify `openssl list -providers | grep oqsprovider` → v0.12.0-dev, active
- [x] Verify `openssl list -kem-algorithms -provider oqsprovider` shows ML-KEM (mlkem512/768/1024, X25519MLKEM768)
- [x] Verify `openssl list -signature-algorithms -provider oqsprovider` shows ML-DSA (mldsa44/65/87)

## Stage 3b: Security Hardening [completed]
- [x] Reverted system-wide openssl.cnf modification (no system config touched)
- [x] Created `pki/openssl-pqc.cnf` — project-scoped OpenSSL config, used via OPENSSL_CONF
- [x] `scripts/rollback.sh` — full uninstall (remove liboqs, oqsprovider, clean namespaces, restore DNS)
- [x] `scripts/setup_routing.sh` — hardened: trap auto-cleanup, stale namespace check, DNS backup/restore
- [x] `scripts/enable_oqsprovider.sh` — repurposed to generate project config only (no system patching)
- [x] `scripts/install_oqs_provider.sh` — only installs module file, never touches openssl.cnf

## Stage 4: PKI — Classical + PQC Certificate Chains [completed]
- [x] Write + execute `pki/gen_classical_ca.sh` (CA + server + client certs, ECDSA P-256)
- [x] Write + execute `pki/gen_pqc_ca.sh` (CA + server + client certs, ML-DSA-65, uses `pki/openssl-pqc.cnf`)
- [x] Verify `openssl verify` passes for both chains

## Stage 5: Shared Modules (`common/`) [completed]
- [x] Implement `common/__init__.py`, `common/framing.py`, `common/tun_utils.py`, `common/openssl_wrapper.py`
- [x] Write `common/test_common.py` — tests framing round-trip, TLS bidirectional relay, imports
- [x] Verify: 4/4 tests pass (framing, TLS relay, tun_import, imports)

## Stage 6: Classical VPN Daemon + Full Tunnel Test [completed]
- [x] Implement `server/vpn_server.py`
- [x] Implement `client/vpn_client.py`
- [x] Write `server/config.yaml` + `client/config.yaml`
- [x] Finalize `scripts/setup_routing.sh` (netns orchestration)
- [x] End-to-end test: ping through tunnel
- [x] End-to-end test: file transfer with sha256 verification

## Stage 7: PQC Swap — Same Daemon, PQC Crypto [pending]
- [ ] Set `OPENSSL_CONF=pki/openssl-pqc.cnf` in openssl_wrapper for PQC mode
- [ ] Add `-groups` flag based on config (e.g. X25519MLKEM768)
- [ ] Update config yamls for PQC cert paths + mode flag
- [ ] Repeat Stage 6 verification suite (ping + file transfer)
- [ ] Confirm via tcpdump that handshake uses PQC groups

## Stage 8: CLI Tool (`vpnctl.py`) [completed]
- [x] Implement `common/daemon_utils.py` — `IpcServer`, `StatusTracker`, role-based socket/PID paths
- [x] Implement `vpnctl connect` / `connect-server` / `connect-client`
- [x] Implement `vpnctl disconnect`
- [x] Implement `vpnctl status` (combined server + client)
- [x] Test connect → status → disconnect cycle

## Stage 9: Benchmark Scripts + Tooling [completed]
- [x] Installed `matplotlib`, `psutil` (need `sudo apt install iperf3` for throughput bench)
- [x] Implemented `benchmark/run_handshake_bench.py`
- [x] Implemented `benchmark/run_latency_bench.sh`
- [x] Implemented `benchmark/run_throughput_bench.sh`
- [x] Implemented `benchmark/run_file_transfer_bench.sh`
- [x] Implemented `benchmark/plot_results.py`
- [x] Validated: handshake bench produces CSVs, plots generate correctly

## Stage 10: Execute Full Benchmark Suite [pending]
- [ ] Classical handshake N>=20 trials → CSV
- [ ] Classical latency (ping 100) → CSV
- [ ] Classical throughput (iperf3 TCP+UDP) → CSV
- [ ] Classical file transfer (100MB) → CSV
- [ ] PQC handshake N>=20 trials → CSV
- [ ] PQC latency (ping 100) → CSV
- [ ] PQC throughput (iperf3 TCP+UDP) → CSV
- [ ] PQC file transfer (100MB) → CSV
- [ ] Run `plot_results.py` → comparison graphs
- [ ] Sanity-check all numbers (PQC handshake slower, throughput similar)

## Stage 11: Documentation + Final Polish [completed]
- [x] Write `docs/setup.md` (fresh reproduction guide)
- [x] Write `docs/architecture.md` (Mermaid diagrams)
- [x] Write `docs/benchmark-results.md` (interpreted results + graphs)
- [x] Write `README.md` (quick start + overview)
- [ ] Full clean-environment test (follow `docs/setup.md` from scratch)
- [ ] Demo script runs end-to-end in <5 minutes

---

# Proper VPN Expansion (see docs/ROADMAP.md)

## Phase 0b: Clean-build validation [pending]
- [ ] Validate `install_custom_openssl.sh` on fresh Ubuntu 24.04 VM/container
- [ ] Verify oqs-provider lists ML-KEM / ML-DSA
- [ ] Write `docs/DEPLOYMENT.md` skeleton (ufw + security-group checklist)

## Phase 1: Default route, NAT, DNS [pending]
- [ ] Add frame-type control byte to `common/framing.py` (+ tests)
- [ ] Server: persist ip_forward, MASQUERADE -o eth0, FORWARD accept, dnsmasq
- [ ] Client: push default route metric 50; restore route + DNS on teardown
- [ ] Control frame pushes client IP / gateway / DNS
- [ ] Verify `curl ifconfig.me` = server IP; DNS via tunnel; UDP `-b 0` true ceiling

## Phase 4-pre: `openssl_wrapper` -fd support [pending]
- [ ] `start_tls_server()` accepts pre-connected `socket_fd` (`s_server -fd`)
- [ ] Unit test classical + PQC handshake through -fd path

## Phase 4: Multi-client [pending]
- [ ] `server/session_manager.py`: ClientSession, IP pool (10.8.0.2-4), TUN demux
- [ ] `server/vpn_server.py`: Python accept loop → per-client `s_server -fd`
- [ ] status includes client table
- [ ] `scripts/setup_routing.sh` adds vpn_ns3/ns4 client namespaces
- [ ] Verify 2 concurrent clients + traffic isolation + iperf3 degradation curve

## Phase 2: Keepalive + reconnect [pending]
- [ ] 15s keepalive control frame; server idle teardown 3×15s
- [ ] Client reconnect supervisor with exponential backoff (1-30s)
- [ ] Status states: handshake-in-progress / connected / reconnecting
- [ ] Verify auto-recovery after network drop

## Phase 3: Session resumption [pending]
- [ ] Verify TLS tickets with oqs-provider PQC groups
- [ ] Ticket cache to ~/.pqvpn/cache/<server>.tick
- [ ] Full vs resumed handshake benchmark → chart

## Phase 5: Dashboard [pending]
- [ ] `dashboard/dashboard_server.py` (FastAPI + WS)
- [ ] `dashboard/index.html` (handshake/tunnels/throughput/log panels)
- [ ] Verify ≤1s UI updates on connect/drop/reconnect

## Phase 6: Report + demo prep [pending]
- [ ] `docs/BENCHMARKS.md` (full/resumed, multi-client degradation, UDP ceiling)
- [ ] Mermaid architecture diagram update
- [ ] Demo script

## Phase 7: Capstone — VPS deployment (payment, LAST) [pending]
- [ ] Buy x86 VPS; deploy validated code + config-vps.yaml
- [ ] ufw + cloud security group for TCP 14430
- [ ] Real-WAN handshake, curl ifconfig.me, 2 clients, dashboard
- [ ] Append real-WAN numbers to BENCHMARKS.md