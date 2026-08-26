# PQC-VPN ROADMAP — Making It a Proper VPN

Tracked phases for turning the working dual-mode prototype into a proper VPN: real
internet forwarding, DNS, multi-client, keepalive/reconnect, session resumption,
a web dashboard, and a capstone real-VPS deployment.

## Scope & Decisions (locked)

- **Scope:** Tier 1 (core VPN plumbing) + partial Tier 2 (multi-client, 3 max)
  + real VPS deployment + web dashboard.
- **Explicitly out of scope (report as "Future Work"):** Tier 3 (Go/C rewrite,
  splice batching, >2Gbps), PAM/production auth, rate limiting, compression,
  MTU auto-negotiation.
- **VPS:** paid x86 IBM-compatible box (Ubuntu 24.04), purchased **last** (Phase 7).
  ARM free tier rejected for build-compatibility risk.
- **Dashboard:** laptop-only, pointed at the server's status socket. VPS stays headless.
- **Client pool:** 3 simultaneous clients (10.8.0.2 – 10.8.0.4).

## Order of Work

Payment step is last; everything else runs offline against the netns harness.

| # | Phase | Summary | Tested against |
|---|-------|---------|----------------|
| 1 | **Phase 0b** | Clean-build validation on fresh Ubuntu 24.04 VM/container; `install_custom_openssl.sh` + provider verify; `DEPLOYMENT.md` skeleton | fresh local OS |
| 2 | **Phase 1** | Default route push, NAT, DNS via dnsmasq, config-push control frames, teardown restoration | netns |
| 3 | **Phase 4 pre** | Welcome accept loop + config-push control frames (`s_server -fd` is unsupported — see architecture change) | netns |
| 4 | **Phase 4** | Multi-client: `session_manager.py`, IP pool, TUN demux, status client table | netns (1 server + 3 clients) |
| 5 | **Phase 2** | Keepalive (15s), idle teardown (3×15s), exponential-backoff reconnect supervisor | netns |
| 6 | **Phase 3** | TLS session-ticket resumption, ticket cache, full-vs-resumed handshake bench | netns |
| 7 | **Phase 5** | FastAPI + WebSocket dashboard (laptop), browser panels — built, WS ≤1s verified | local |
| 8 | **Phase 6** | `BENCHMARKS.md`, Mermaid diagram, demo script, `DEPLOYMENT.md`; live netns pass verified (3 clients, PQC) | netns + docs |
| 9 | **Phase 7** | **Buy x86 VPS last.** Deploy validated code + `config-vps.yaml`; ufw + cloud security group; real-internet handshake, `curl ifconfig.me`, multi-client over WAN, dashboard | real VPS |

## Key Architecture Change (Phases 2 & 4)

`openssl s_server` accepts a single connection inside the subprocess, and neither
the system OpenSSL nor the custom oqs build supports `s_server -fd`. Instead the
server owns a small **welcome listener** and assigns each client a dedicated data
port + tunnel IP via a config-push control frame:

```
Python welcome accept() → config-push {ip, port, gateway, dns} → client connects its
own data port → per-client s_server -accept <port> + s_client → per-client relay
threads → shared TUN demux (by dst IP)
```

## Protocol Change (Phase 1)

`common/framing.py` gains a reserved frame-type byte:

```
[type:1][len:4][payload]
  0x00 = IP data
  0x01 = control (keepalive / config push)
```

Keeps keepalive frames and pushed config (assigned IP, gateway, DNS)
unambiguous vs tunnel data.

## Phase Details

### Phase 0b — Clean-build validation (no cost)
1. Fresh local Ubuntu 24.04 VM/container.
2. Run `scripts/install_custom_openssl.sh`; verify:
   `openssl list -kem-algorithms | grep mlkem` and
   `openssl list -signature-algorithms | grep mldsa`.
3. Write `DEPLOYMENT.md` skeleton (build steps + ufw/security-group checklist).

### Phase 1 — Default route, NAT, DNS
- Framing type byte (above).
- `server/net_config.py`: `setup_nat`/`teardown_nat` — ip_forward (+ optional
  `/etc/sysctl.d/99-pqvpn.conf`), `MASQUERADE -o <outbound_iface>`, FORWARD
  accept in/out tun0, idempotent adds. `start_dnsmasq` binds dnsmasq to the
  tunnel interface with upstream from config (`dns_upstream`, default 1.1.1.1).
  `outbound_iface` is config-driven (`eth0` on VPS, `veth-ns1` in netns).
- `client/net_config.py`: `apply_tunnel_net`/`restore_tunnel_net` — push
  `ip route add default via <gw> dev tun0 metric 50`, set DNS via `resolvectl`
  (systemd) or `/etc/resolv.conf` fallback; tracked state so teardown only
  restores what was changed.
- Both support `--dry-run` (print commands, no root) for testable plumbing.
- Verify: `curl ifconfig.me` = server IP; `nslookup` via pushed DNS + tcpdump;
  kill mid-session → route/DNS restored. Re-run UDP bench with `-b 0` for true ceiling.

### Phase 4 — Multi-client
- `server/session_manager.py`: `ClientSession`, IP pool allocator (10.8.0.2-.4,
  3 clients max), TUN demux thread.
- `server/vpn_server.py`: welcome accept loop → per-client data port + `s_server`.
- `client/vpn_client.py`: welcome handshake, assigned port/IP, TUN at pushed addr.
- `common/daemon_utils.py`: status includes client table.
- `scripts/setup_routing.sh`: `vpn_ns2`..`vpn_ns4` client namespaces.
- Verify: 2 concurrent clients, traffic isolation, concurrent iperf3 degradation.

### Phase 2 — Keepalive + reconnect
- Control keepalive frame every **15s**; server idle teardown at **3×15s** via
  `SessionManager.idle_sessions`. `relay_tls_to_tun` calls `session.touch()` on any
  frame (traffic or keepalive); `idle_monitor` tears down only sessions that go
  silent past the timeout, skipping ones already stopping.
- Client reconnect supervisor: exponential backoff 1→30s (`next_backoff`, cap 30s),
  branches on `run_session`'s clean/fail return so only unexpected drops trigger
  reconnect, re-runs route/DNS after successful reconnect.
- Status states: `handshake-in-progress` / `connected` / `reconnecting`.
- Testing: `common/test_keepalive.py` 5/5 (backoff growth, idle detection, touch
  reset, keepalive-keeps-alive, periodic pipe keepalive) + a full TLS-relay
  integration check that an idle client is torn down only after its keepalives stop.

### Phase 3 — Session resumption
- Verified TLS session tickets work with oqs-provider PQC groups: a second
  `s_client -sess_in <ticket>` reports `Reused, TLSv1.3` for both classical and
  `X25519MLKEM768` modes; the resumed PQC handshake carries a `pre_shared_key`
  extension and skips the KEM/certificate exchange entirely.
- `common/ticket_cache.py`: stable per-server path `~/.pqvpn/cache/<host>.<hash>.tick`
  (survives daemon restarts); `load_ticket`/`save_ticket_file`/`rotate_ticket`
  (atomic via `os.replace`)/`clear_ticket`.
- Client passes `-sess_in` (resume) + `-sess_out` (refresh) to `s_client`
  using the same cache file, so each reconnect both resumes and refreshes the
  ticket. Toggle with `resume_cache` config; status reports `resumed`.
- `benchmark/run_handshake_bench.py --resume` primes a ticket once against a
  long-lived `s_server` then interleaves full+resumed trials per mode →
  `handshake_resume_{mode}.csv`; `plot_results.py` draws `handshake_resume.png`.
- Results (n=20, loopback): classical 15.0→13.5ms median (~1.1x),
  PQC 24.4→23.7ms (~1.0x). The KEM is a small share of loopback cost — the real
  win is the saved round-trip over WAN. Verified resumed PQC genuinely skips KEM.

### Phase 5 — Dashboard
- `dashboard/dashboard_server.py`: FastAPI + WebSocket `/ws/status`, `GET /api/status`,
  `POST /disconnect/{id}`, `POST /rekey` (returns unsupported — s_server has no
  mid-stream renegotiation; reconnect re-handshakes instead).
- `dashboard/index.html`: vanilla JS + Chart.js; panels: throughput line chart,
  handshake-mode indicator + resume note, active tunnels table with per-client
  kick buttons, event log, server/client status cards.
- Status source: the server/client daemons expose `/tmp/pqvpn-{role}.sock` via
  `IpcServer`; the dashboard polls them every 0.5s and pushes diffs over WS.
  Measured end-to-end: a daemon state flip reached the browser WS in ~300ms.
- `IpcServer` gained an optional `command_handler` + `ipc_request(role, command=)`;
  a `select`-based 100ms wait for an optional command keeps bare vpnctl status
  polls fast while enabling `disconnect` / `clients` commands.
- `POST /disconnect/{id}` → IPC `disconnect` → `SessionManager.disconnect`
  (stops relay, terminates the per-client `s_server`, frees the IP).
- Deps: `fastapi`, `uvicorn[standard]`, `websockets` added to `requirements.txt`.
- Tests: `dashboard/test_dashboard.py` 9/9 + live uvicorn boot/serve check.

### Phase 6 — Report + demo
- `docs/BENCHMARKS.md`: compiled report over the raw CSVs (full handshake,
  full-vs-resumed resumption, latency, throughput, file transfer), interpretation
  table, Future Work, and exact reproduce commands. The pre-existing
  `docs/benchmark-results.md` is now superseded (kept with a redirect note).
- `docs/architecture.md`: Mermaid diagram rewritten for the current design
  (welcome loop, per-client `s_server` relays, shared TUN demux, ticket cache,
  dashboard); framing + IPC + resumption + limitations sections updated.
- `docs/DEPLOYMENT.md` (Phase 0b carryover): build steps, PKI, netns harness,
  start commands, VPS config sketch (`config-vps.yaml`), ufw + security-group
  checklist, rollback, troubleshooting.
- `scripts/demo.sh`: reproducible demo.
  - `--dry-run` (rootless): offline sanity checks, runs all 5 unit suites,
    runs a real 5-trial handshake benchmark into a temp dir (doesn't touch
    committed CSVs) — verified end-to-end.
  - Live (`sudo`): netns harness → server + up to 3 clients → ping verify →
    `vpnctl status` → optional iperf3. Flags: `--mode`, `--clients 1-3`,
    `--keep`.
- `benchmark/run_handshake_bench.py` gained `--out-dir` so demo dry-runs never
  clobber the 25-trial results.
- Added `client/config-pqc-ns3.yaml` / `config-pqc-ns4.yaml` for PQC multi-client.
- Remaining: one live netns pass of `demo.sh --mode pqc --clients 3` (sudo).

### Phase 7 — Capstone (payment, LAST)
1. Buy x86 VPS (Ubuntu 24.04).
2. Deploy validated code + `config-vps.yaml` (`server_host` = VPS IP).
3. `DEPLOYMENT.md` checklist: ufw + cloud security group for TCP 14430.
4. Real-WAN: single handshake, `curl ifconfig.me` = VPS IP, DNS, 2 clients, dashboard.
5. Append real-WAN numbers to `BENCHMARKS.md`.