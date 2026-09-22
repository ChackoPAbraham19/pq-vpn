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

## Stage 7: PQC Swap — Same Daemon, PQC Crypto [completed]
- [x] `OPENSSL_CONF=pki/openssl-pqc-runtime.cnf` + custom `/opt/openssl-pqc` in `openssl_wrapper._make_env`
- [x] `-groups` flag from config (X25519MLKEM768)
- [x] `server/config-pqc.yaml` + `client/config-pqc.yaml`
- [x] Stage 6 verification suite repeated in PQC mode (ping + file transfer)
- [x] `s_client -brief` shows `Signature type: mldsa65` + hybrid group (see architecture.md)

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

## Stage 10: Execute Full Benchmark Suite [completed — 25 trials/mode, see docs/BENCHMARKS.md]
- [x] Classical handshake N>=20 trials → CSV
- [x] Classical latency (ping 100) → CSV
- [x] Classical throughput (iperf3 TCP+UDP) → CSV
- [x] Classical file transfer (100MB) → CSV
- [x] PQC handshake N>=20 trials → CSV
- [x] PQC latency (ping 100) → CSV
- [x] PQC throughput (iperf3 TCP+UDP) → CSV
- [x] PQC file transfer (100MB) → CSV
- [x] Run `plot_results.py` → comparison graphs
- [x] Sanity-check all numbers (PQC handshake slower, throughput similar)

## Stage 11: Documentation + Final Polish [completed]
- [x] Write `docs/setup.md` (fresh reproduction guide)
- [x] Write `docs/architecture.md` (Mermaid diagrams)
- [x] Write `docs/benchmark-results.md` (interpreted results + graphs)
- [x] Write `README.md` (quick start + overview)
- [x] Full clean-environment test — `Dockerfile` + `scripts/clean_build_check.sh` from bare ubuntu:24.04 (2026-09-16): 126/126 tests + live 2-client PQC tunnel inside a privileged container
- [x] Demo script runs end-to-end in <5 minutes (live 3-client PQC run: 48 s)

---

# Proper VPN Expansion (see docs/ROADMAP.md)

## Phase 0b: Clean-build validation [completed]
- [x] Validate `install_custom_openssl.sh` on fresh Ubuntu 24.04 container — found + fixed: missing LD_LIBRARY_PATH for self-check, unpinned `main` (liboqs/oqs-provider drift), liboqs in `lib/` unreachable without ldconfig, no default openssl.cnf (`install_ssldirs`), sudo-less root, PKI scripts `cd` into git-ignored dir, v1 PQC CA rejected by released 3.4.7
- [x] Verify oqs-provider lists ML-KEM / ML-DSA — Dockerfile verify gate (`mlkem768 @ oqsprovider`, `mldsa65 @ oqsprovider`)
- [x] `docs/DEPLOYMENT.md` skeleton (build steps + ufw/security-group checklist) — written in Phase 6

## Phase 1: Default route, NAT, DNS [in progress]
- [x] Add frame-type control byte to `common/framing.py` (+ tests)
- [x] Control frame pushes client IP / gateway / DNS (welcome config-push, Phase 4-pre)
- [x] `server/net_config.py`: setup/teardown NAT + FORWARD + ip_forward (idempotent, `--dry-run`)
- [x] `server/net_config.py`: dnsmasq on tun0 bound to gateway, upstream from config
- [x] `client/net_config.py`: default route via gw metric 50 + DNS (resolvectl → resolv.conf fallback), tracked restore
- [x] Daemons wired: `setup_nat`/`start_dnsmasq` on server start, teardown on stop; client apply/restore around relay
- [x] `common/test_net_config.py` 8/8 dry-run command assertions pass
- [x] netns run (2026-08-26, live): NAT MASQUERADE + dnsmasq bound on 10.8.0.1; default-route push + /32 transport protection + restore verified across 3 namespaces. DNS client application blocked by harness env (no systemd-resolved in netns; resolv.conf EPERM) — documented limitation, IP connectivity unaffected

## Phase 4-pre: per-client ports (replaces -fd; neither OpenSSL has `s_server -fd`) [completed]
- [x] Pivot: welcome accept loop + config-push control frame (assigned IP / data port / gateway / dns)
- [x] `common/framing.py`: `pack_config` / `unpack_config` control-frame helpers (+tests)
- [x] Test: welcome handshake -> assigned port -> framed TLS relay end-to-end

## Phase 4: Multi-client [in progress]
- [x] `server/session_manager.py`: ClientSession, IP pool (10.8.0.2-4, 3 max), TUN demux
- [x] `server/vpn_server.py`: welcome accept loop -> per-client `s_server -accept <port>`
- [x] `client/vpn_client.py`: welcome handshake, assigned IP/port, TUN at pushed address
- [x] `common/daemon_utils.py`: status client table (+ `vpnctl status` shows it)
- [x] `scripts/setup_routing.sh` multi-client namespaces (vpn_ns2..ns4)
- [x] 10/10 tests pass incl. welcome->relay end-to-end + 2 concurrent relays
- [x] netns run (2026-08-26, live): 3 concurrent clients on 10.8.0.2-.4 / ports 14431-33; isolated tunnels verified via per-client pings + concurrent iperf3 (491/926/1014 Mbps)

## Phase 4b: Multi-client hardening (pre-VPS) [completed]
- [x] Ports bound to IP slots (`data_port_base + slot`), pool capped at `max_clients` — reconnects never leave the firewall range (was: every reconnect burned a new port)
- [x] `s_server -naccept 1`: process exits when its client hangs up → slot freed immediately, no ghost attach to a dead session's pipes
- [x] `SessionManager.remove`/`reclaim_idle` drain the subprocess before releasing the slot
- [x] Config-push (ip/gateway/dns) moved INSIDE the mTLS channel as the first frame; plaintext welcome carries only `{port}`; client validates the push (`framing.validate_config`)
- [x] Fixed 5 s startup wait removed on both sides (config-push doubles as handshake-complete signal); client acks with an immediate keepalive
- [x] `handshake_timeout` (10 s) reaps welcome slot claims that never finish mTLS (bounds welcome-port DoS)
- [x] Addresses derived from `tun_net`; demux uses a packed-address dict instead of a linear scan
- [x] `common/test_common.py` 52/52 (port recycling, welcome-send-failure slot release, tun_net, -naccept, stale reap, validate_config, real config-push over TLS)
- [x] netns run (2026-09-16, live, PQC, 3 clients): reconnect landed on 10.8.0.4 / port 14433 as client #4 with s_server count still 3; restarted client `connected` at 3 s uptime (old code: still inside its 5 s wait); dashboard 401 → kick ok with token
- [x] netns run (2026-09-16, 2 clients): bare `nc` to 14430 claimed `#3 10.8.0.4/14433 in 0/out 0`, reaped at 10 s (`no mTLS handshake`), real clients unaffected; ns3 restart → `#4` on 14433 (FIFO slot reuse, in range), connected at 3 s, `s_server` count 2; dashboard 401 → kick `ok`
- [x] Found by the kick test: an idle client never reconnected after a server-side teardown (TUN→TLS relay only noticed on its next write). Fixed with a per-session stop event cascaded from the TLS→TUN thread + keepalive `BrokenPipe`; `test_keepalive.py` 7/7
- [x] netns re-run of the kick (2026-09-16): `#1` gone, client 1 back as `#3` on 10.8.0.4/14433 within 4 s; client log `connection lost` → `reconnecting in 1s` → `connected; assigned 10.8.0.4`

## Phase 2: Keepalive + reconnect [in progress]
- [x] 15s keepalive control frame; server idle teardown 3×15s
- [x] Client reconnect supervisor with exponential backoff (1-30s)
- [x] Status states: handshake-in-progress / connected / reconnecting
- [x] `common/test_keepalive.py` 5/5: backoff, idle detection, touch reset, keepalive-keeps-alive, pipe keepalive
- [x] Integration: client keepalives → relay `session.touch()` → idle teardown fires only after silence
- [x] Fix: `idle_monitor` skips already-stopping sessions (no repeated teardown logs)
- [x] Fix: client main loop branches on `run_session` clean/fail (reconnect only on unexpected drop)
- [x] Configs: `keepalive_interval`, `backoff_min`, `backoff_max` in server + all 4 client yamls
- [x] Verify auto-recovery after network drop (netns run): reconnect supervisor observed live (backoff 1→30s across 3 clients); TUN lifecycle fix (`destroy_tun` + EBUSY retry) makes reconnects actually succeed; idle teardown fired at 45s for zombie sessions

## Phase 3: Session resumption [in progress]
- [x] Verify TLS tickets with oqs-provider PQC groups (X25519MLKEM768) — `Reused, TLSv1.3` confirmed for both modes; resumed PQC handshake skips the KEM (ServerHello `00 29` pre_shared_key accepted, no Certificate/KeyExchange)
- [x] `common/ticket_cache.py`: stable per-server path `~/.pqvpn/cache/<host>.<sha16>.tick`, `load_ticket`/`save_ticket_file`/`rotate_ticket` (atomic)/`clear_ticket`
- [x] `common/openssl_wrapper.py`: `start_tls_client` accepts `sess_in`/`sess_out` (verified same-file read+write refresh works)
- [x] `client/vpn_client.py`: `sess_in` from cache (resume) + `sess_out` to cache (persists across restarts); `resume_cache: true` config toggle; status `resumed` flag
- [x] `common/daemon_utils.py`: status snapshot includes `resumed`; `vpnctl status` prints `Session: resumed`
- [x] `benchmark/run_handshake_bench.py --resume`: primes a ticket once, then interleaves full+resumed trials per mode → `handshake_resume_{mode}.csv` (full/resumed/ms)
- [x] `benchmark/plot_results.py`: `_plot_resume` → `handshake_resume.png`
- [x] `common/test_resumption.py` 11/11: cache paths, atomic rotate, wrapper flags, real OpenSSL prime→`Reused` end-to-end, client wiring
- [x] Full suite: 11/11 + 8/8 + 5/5 + 11/11 pass
- [x] netns run (2026-08-26, live): tickets offered + cached across reconnects; `resumed` now evidence-based (fresh per-client `s_server` regenerates ticket keys → full-handshake fallback is the honest outcome; see architecture.md limitations). Benchmark-level prime→`Reused` verified separately.

## Phase 5: Dashboard [in progress]
- [x] `dashboard/dashboard_server.py`: FastAPI — `/ws/status` WS stream, `GET /api/status`, `POST /disconnect/{id}`, `POST /rekey` (returns unsupported)
- [x] `dashboard/index.html`: Chart.js panels (throughput line chart, handshake-mode bar, tunnels table with kick button, event log, server/client KV)
- [x] Status source: polls `/tmp/pqvpn-{server,client}.sock` every 0.5s; state flip propagates to WS in ~300ms (≤1s goal)
- [x] `common/daemon_utils.py`: `IpcServer` accepts optional commands (`command_handler`); `select`-based 100ms command wait keeps bare vpnctl polls fast; `ipc_request(role, command=)` helper
- [x] `server/vpn_server.py`: IPC command handler — `disconnect` (SessionManager.disconnect), `clients`
- [x] `server/session_manager.py`: `disconnect(client_id)` force-teardown (idempotent)
- [x] `requirements.txt`: fastapi, uvicorn[standard], websockets (installed in venv)
- [x] `dashboard/test_dashboard.py` 9/9: IPC envelope compat, FastAPI endpoints, WS stream, feed missing-socket safety, uvicorn live boot + serve
- [x] Full suite: 11/11 + 8/8 + 5/5 + 11/11 + 9/9 pass
- [x] Verified vpnctl `_read_status` still works with new envelope-compatible IPC
- [x] Hardening: `X-PQVPN-Token` on `POST /disconnect`/`/rekey` (CSRF-safe via preflight + secret, never served by GET); single background poller (`feed.latest()`) so N tabs = 1 IPC poll; `lifespan` replaces deprecated `on_event`
- [x] UI: every client instance rendered, aggregate throughput from server counters, per-tunnel kB/s columns, all values escaped
- [x] `dashboard/test_dashboard.py` 25/25 (401/401/503 auth path, no-GET token, reader-does-not-poll)
- [x] Live browser session (2026-09-16): both clients rendered, throughput chart under iperf3, kick from the button works with the token; kicked client drops and reappears with a new id

## Phase 6: Report + demo prep [completed]
- [x] `docs/BENCHMARKS.md` — compiled all CSV results (full + resumed handshake, latency, throughput, file transfer), interpretation, Future Work, reproduction commands
- [x] Mermaid architecture diagram updated (welcome loop, per-client relays, TUN demux, ticket cache, dashboard)
- [x] `docs/DEPLOYMENT.md` — build steps + ufw/security-group checklist, VPS config sketch (Phase 7 ready)
- [x] `scripts/demo.sh` — `--dry-run` (offline: unit suites + 5-trial handshake bench to tmp) and live netns path (`--mode`, `--clients 1-3`, `--keep`); dry-run verified end-to-end
- [x] `benchmark/run_handshake_bench.py` gains `--out-dir` so dry-runs don't clobber committed 25-trial CSVs
- [x] Added `client/config-pqc-ns3.yaml` / `config-pqc-ns4.yaml` (PQC multi-client demo configs)
- [x] netns run (2026-08-26, live): `scripts/demo.sh --mode pqc --clients 3` full pass — 3 tunnels stable through pings (0% loss, ~1ms) + concurrent iperf3, clean teardown. Fixed en route: iptables arg order (tests had blessed it), server tun0 never addressed/up'd, tunnel-self-blackhole (missing /32 transport route), TUN EBUSY reconnect wedge, relay teardown on transient TUN-write errors

## Phase 4c: live checks [completed 2026-09-17]
- [x] Welcome rate limiter on the wire: 8 rapid `nc -z` from vpn_ns4 → one `welcome rate-limited: 192.168.220.2 (> 3 burst / 1/s)`, real clients unaffected (ping 0% loss), the allowed claims released instantly via the welcome-send-failure guard; restarted ns3 reconnected normally
- [x] `vpnctl status` shows `Uptime <daemon>s (session <n>s)` live; session reset after a process restart shown (4s/4s); the daemon≠session split after a kick is covered by `test_session_uptime_tracking`

## Phase 7a: VPS deployment prep (no purchase) [completed]
- [x] `server/config-vps.example.yaml` / `client/config-vps.example.yaml` (real configs git-ignored)
- [x] `deploy/pqvpn-server.service` — systemd unit, SIGTERM → clean NAT/dnsmasq teardown, restart on failure
- [x] `scripts/deploy_vps.sh [--dry-run] user@host` — rsync, build stack if missing + provider verify, venv, ufw for welcome + slot port range, enable service, print status (idempotent)
- [x] `docs/DEPLOYMENT.md` §6 rewritten around the script

## Phase 8: CI [completed]
- [x] `.github/workflows/clean-build.yml`: Docker build from ubuntu:24.04 with GHA layer cache → provider verify gate → all suites + bench; `live-netns` job runs the 2-client PQC demo in a privileged container (allowed to fail: hosted runners may lack /dev/net/tun)
- [x] README badge
- [x] First green run on GitHub after push (2026-09-22)

## Phase 9: Per-client certificates + revocation [completed]
- [x] `pki/ca.cnf` + `pki/_env.sh`: `openssl ca` database per PKI (index/serial/crlnumber), `default_md = default` so the ML-DSA CA signs CRLs
- [x] `pki/issue_client.sh <mode> <name>` / `pki/revoke_client.sh <mode> <name> [--kick]`; `gen_*_ca.sh` create the DB, an empty CRL, and the default `client` cert (legacy `client.crt` kept as a copy)
- [x] Server: every `s_server` gets `-CRL crl.pem -crl_check -verify_return_error` (fresh per session → revoke needs no restart); startup logs the revoked count
- [x] Client: `client_name` → `clients/<name>.{crt,key}`; post-mTLS hello frame carries the name; `vpnctl status` + dashboard show it; `vpnctl disconnect-client <name>` / IPC `disconnect_client`
- [x] `common/test_common.py` 71/71 incl. real s_server: revoked client → alert 44, nothing relayed; unrevoked client through the same CRL relays fine
- [x] Item 3 (cross-restart resumption) deferred with the precise reason in architecture.md: `s_server` cannot load a ticket key → needs a custom TLS server
- [x] netns run (2026-09-22): `revoke_client.sh pqc ns3 --kick` → CRL serial 1001, `Disconnected 'ns3': session(s) [2]`, ns3 reconnect got `SSL alert number 44 … certificate revoked` and stayed in backoff, `client` pinged at 0% loss, reissue refused until the revoked file is removed

## Phase 7a2: internet-egress hardening (pre-VPS) [completed]
- [x] Server TCP MSS clamp on the forward path both ways (`mss_clamp`, default on) — PMTU blackholes through the 1400-byte tunnel
- [x] Client IPv6 blackhole while connected (`block_ipv6`, default on) — no v6 leak around the IPv4-only tunnel
- [x] `deploy_vps.sh`: `ufw route allow` tunnel↔egress both ways (durable across `ufw reload`)
- [x] `setup_routing.sh`: host NATs the server namespace out the uplink; `demo.sh --egress` curls a public IP-echo from each client through the tunnel
- [x] `common/test_net_config.py` 25/25
- [x] netns run (2026-09-22): mangle TCPMSS rules present in vpn_ns1; v6 blackhole present in vpn_ns2 while up, gone after teardown
- [x] Found by the first `--egress` run: the client's `default … metric 50` lost to the harness's metric-0 default → internet traffic bypassed the tunnel while pings to the server worked. Fixed: client now installs `0.0.0.0/1` + `128.0.0.0/1` via tun0 (beats any default regardless of metric)
- [x] netns run (2026-09-22): `demo.sh --mode pqc --clients 2 --egress` → both clients see the host's public IP through the tunnel; `curl https://ifconfig.me/ip` from vpn_ns2 (DNS + HTTPS by hostname, inside the tunnel) prints it too; /1 half-routes above the metric-0 default in `ip route`

## Phase 7b: Capstone — run on a real VPS (payment, LAST) [pending]
- [ ] Buy x86 VPS (Ubuntu 24.04); `bash scripts/deploy_vps.sh user@<ip>`
- [ ] Cloud security group: TCP 14430 + 14431–14433 from client IPs
- [ ] Real-WAN handshake, `curl ifconfig.me` = VPS IP, DNS via tunnel, 2 clients, dashboard
- [ ] Append real-WAN numbers to BENCHMARKS.md