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
| 3 | **Phase 4 pre** | `openssl_wrapper` `-fd` support (accept loop in Python, per-client subprocess) | netns |
| 4 | **Phase 4** | Multi-client: `session_manager.py`, IP pool, TUN demux, status client table | netns (1 server + 3 clients) |
| 5 | **Phase 2** | Keepalive (15s), idle teardown (3×15s), exponential-backoff reconnect supervisor | netns |
| 6 | **Phase 3** | TLS session-ticket resumption, ticket cache, full-vs-resumed handshake bench | netns |
| 7 | **Phase 5** | FastAPI + WebSocket dashboard (laptop), browser panels | local |
| 8 | **Phase 6** | `BENCHMARKS.md`, Mermaid architecture diagram, demo script | netns + docs |
| 9 | **Phase 7** | **Buy x86 VPS last.** Deploy validated code + `config-vps.yaml`; ufw + cloud security group; real-internet handshake, `curl ifconfig.me`, multi-client over WAN, dashboard | real VPS |

## Key Architecture Change (Phases 2 & 4)

Current `openssl s_server` accepts a single connection inside the subprocess.
New model:

```
Python accept() → fd → openssl s_server -fd <n> → per-client relay threads → shared TUN demux
```

- `common/openssl_wrapper.py::start_tls_server()` gains a `socket_fd` argument
  (`-fd <n>` instead of `-accept`). Same env/flags/PQC logic otherwise.
- Refactored in Phase 4-pre so reconnect and multi-client both build on it.

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
- Server: persist `net.ipv4.ip_forward=1` via `/etc/sysctl.d/`; NAT
  `MASQUERADE -o eth0`; FORWARD accept in/out tun0; `dnsmasq` on tunnel interface.
- Client: after handshake `ip route add default via <gw> dev tun0 metric 50`;
  on teardown **remove route + restore DNS** (`resolvectl` primary, resolv.conf fallback).
- Control frame pushes: client IP, gateway, DNS.
- Verify: `curl ifconfig.me` = server IP; `nslookup` via pushed DNS + tcpdump;
  kill mid-session → route/DNS restored. Re-run UDP bench with `-b 0` for true ceiling.

### Phase 4 — Multi-client
- `server/session_manager.py`: `ClientSession`, IP pool allocator, TUN demux thread.
- `server/vpn_server.py`: Python accept loop → per-connection `s_server -fd`.
- `common/daemon_utils.py`: status includes client table.
- `scripts/setup_routing.sh`: add `vpn_ns3` (..ns4) client namespaces.
- Verify: 2 concurrent clients, traffic isolation, concurrent iperf3 degradation.

### Phase 2 — Keepalive + reconnect
- Control keepalive frame every 15s; server idle teardown at 3×15s.
- Client reconnect supervisor: exponential backoff 1→30s, clean-vs-unexpected
  disconnect, re-run route/DNS only after successful reconnect.
- Status states: `handshake-in-progress` / `connected` / `reconnecting`.

### Phase 3 — Session resumption
- Verify tickets with oqs-provider PQC groups (report finding either way).
- Cache tickets to `~/.pqvpn/cache/<server>.tick` (survives restarts).
- `benchmark_handshake.py`: N full + N resumed for both modes → chart.

### Phase 5 — Dashboard
- `dashboard/dashboard_server.py`: FastAPI + WebSocket `/ws/status`, `POST /rekey`,
  `POST /disconnect/{id}`.
- `dashboard/index.html`: vanilla JS + Chart.js; panels: handshake compare,
  active tunnels, throughput, event log.
- Verify ≤1s update after real events.

### Phase 6 — Report + demo
- `DEPLOYMENT.md` (from 0b), `BENCHMARKS.md`, Mermaid diagram,
  "Future Work" section, demo script.

### Phase 7 — Capstone (payment, LAST)
1. Buy x86 VPS (Ubuntu 24.04).
2. Deploy validated code + `config-vps.yaml` (`server_host` = VPS IP).
3. `DEPLOYMENT.md` checklist: ufw + cloud security group for TCP 14430.
4. Real-WAN: single handshake, `curl ifconfig.me` = VPS IP, DNS, 2 clients, dashboard.
5. Append real-WAN numbers to `BENCHMARKS.md`.