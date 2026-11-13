# PQ-VPN — File Guide

Every file in the repository, what it does, and why it exists.

This is the companion to the three other reference documents. Where they
describe the system, this one describes the *repository*:

| Document | Answers |
|---|---|
| [`docs/PROJECT_REPORT.md`](PROJECT_REPORT.md) | How the system works, and what was measured |
| [`docs/architecture.md`](architecture.md) | How the components fit together |
| [`docs/setup.md`](setup.md) | How to build it from a bare Ubuntu 24.04 |
| **this file** | **What each file is, and which file to open for a given question** |

Line counts are from the current tree. The five offline test suites total
**207 checks** (92 + 36 + 7 + 14 + 58), all passing.

---

## 1. File structure

```
VPN/
├── pqvpn                           the `pqvpn` command (bash wrapper, 56 L)
├── README.md                       project front page (244 L)
├── LICENSE                         MIT licence (21 L)
├── requirements.txt                Python dependencies (21 L)
├── Dockerfile                      clean-build proof from bare ubuntu:24.04 (41 L)
├── .dockerignore                   keeps certs/venv/graph out of the build context
├── .gitignore                      keeps key material and VPS IPs out of git (57 L)
├── IMPLEMENTATION_PLAN.md          the original pre-build plan, kept for provenance (268 L)
├── TODO.md                         dated record of every verified run (386 L)
├── opencode.json                   unrelated editor-tool config (10 L)
│
├── common/                         code both daemons share
│   ├── __init__.py                 marks the package (empty)
│   ├── framing.py                  the wire format: [type][length][payload] (108 L)
│   ├── openssl_wrapper.py          builds the s_server / s_client command lines (136 L)
│   ├── tun_utils.py                TUN device create / open / destroy (66 L)
│   ├── daemon_utils.py             IPC socket server, status tracker, PID files (248 L)
│   ├── ticket_cache.py             TLS session tickets on disk, per server (56 L)
│   ├── test_common.py              92 checks: framing, TLS relay, sessions, CRL (1280 L)
│   ├── test_net_config.py          36 checks: every routing/DNS/NAT command (395 L)
│   ├── test_keepalive.py           7 checks: idle detection, backoff, keepalives (161 L)
│   └── test_resumption.py          14 checks: ticket cache + real resumption (190 L)
│
├── server/                         the server daemon
│   ├── vpn_server.py               main loop: welcome port, TUN demux, CRL preflight (478 L)
│   ├── session_manager.py          IP pool, per-client TLS port, rate limiter (358 L)
│   ├── net_config.py               IP forwarding, NAT, FORWARD rules, dnsmasq (188 L)
│   ├── config.yaml                 classical profile, netns harness
│   ├── config-pqc.yaml             post-quantum profile, netns harness
│   └── config-vps.example.yaml     template for a real VPS (the live one is gitignored)
│
├── client/                         the client daemon
│   ├── vpn_client.py               welcome handshake, relays, reconnect loop (460 L)
│   ├── net_config.py               half-route default, DNS, IPv6 guard, teardown (345 L)
│   ├── config.yaml                 classical, ns2
│   ├── config-pqc.yaml             post-quantum, ns2
│   ├── config-ns3.yaml             classical, ns3 (second concurrent client)
│   ├── config-ns4.yaml             classical, ns4 (third concurrent client)
│   ├── config-pqc-ns3.yaml         post-quantum, ns3
│   ├── config-pqc-ns4.yaml         post-quantum, ns4
│   └── config-vps.example.yaml     template for connecting to a real VPS
│
├── cli/
│   └── vpnctl.py                   `pqvpn up/down/status/dashboard` (571 L)
│
├── dashboard/                      live web dashboard
│   ├── dashboard_server.py         FastAPI + WebSocket, one shared poller (362 L)
│   ├── index.html                  the whole UI: CSS, Chart.js, control panel (836 L)
│   └── test_dashboard.py           58 checks: endpoints, auth, control mode (571 L)
│
├── pki/                            certificate authorities for both modes
│   ├── _env.sh                     shared environment; finds the PQC OpenSSL (41 L)
│   ├── ca.cnf                      `openssl ca` config shared by both PKIs (50 L)
│   ├── gen_classical_ca.sh         ECDSA P-256 CA + server + default client (39 L)
│   ├── gen_pqc_ca.sh               ML-DSA-65 CA + server + default client (45 L)
│   ├── issue_client.sh             mint one revocable per-client certificate (28 L)
│   ├── revoke_client.sh            revoke a client, regenerate the CRL, --kick (27 L)
│   ├── refresh_crl.sh              reissue the CRL without touching the CA (47 L)
│   ├── openssl-pqc.cnf             loads oqsprovider — used for key generation (22 L)
│   ├── openssl-pqc-runtime.cnf     SECLEVEL only — used for TLS at runtime (10 L)
│   └── certs/                      GITIGNORED — generated key material
│       ├── classical/              ca.*, server.*, client.*, crl.pem, db/, clients/
│       └── pqc/                    same layout, ML-DSA-65 keys
│
├── scripts/                        setup, demo, deployment, diagnostics
│   ├── install_custom_openssl.sh   OpenSSL 3.4 + liboqs + oqs-provider → /opt (150 L)
│   ├── install_liboqs.sh           liboqs alone, system OpenSSL path (38 L)
│   ├── install_oqs_provider.sh     oqs-provider alone, system OpenSSL path (36 L)
│   ├── enable_oqsprovider.sh       writes pki/openssl-pqc.cnf (46 L)
│   ├── install_cli.sh              symlinks `pqvpn` into /usr/local/bin (55 L)
│   ├── setup_routing.sh            the four-namespace test harness (167 L)
│   ├── demo.sh                     the reproducible demo, live or dry-run (283 L)
│   ├── demo_vps.sh                 one-command demo against the deployed VPS (92 L)
│   ├── deploy_vps.sh               rsync + build + systemd on a real VPS (128 L)
│   ├── dashboard.sh                launch the dashboard, local or over SSH (84 L)
│   ├── run_all_benchmarks.sh       regenerate every published measurement (313 L)
│   ├── test_file_transfer.sh       early TCP/UDP transfer check through the tunnel (181 L)
│   ├── diagnose_tunnel.sh          step-by-step: where exactly TCP breaks (111 L)
│   ├── backup_restore_dns.sh       save/restore /etc/resolv.conf (33 L)
│   ├── clean_build_check.sh        build the Docker image and run the suites in it (57 L)
│   └── rollback.sh                 undo every system change this project makes (47 L)
│
├── benchmark/                      measurement harness and raw data
│   ├── run_handshake_bench.py      loopback TLS handshake + resumption timing (285 L)
│   ├── run_wan_handshake_bench.py  the same over a real WAN, via SSH to a VPS (263 L)
│   ├── measure_handshake_bytes.py  bytes per handshake message, from -msg (255 L)
│   ├── transfer_bench.py           bulk transfer, hashed in flight, never stored (230 L)
│   ├── run_file_transfer_bench.sh  places transfer_bench.py's two ends in netns (57 L)
│   ├── run_latency_bench.sh        ping RTT through an idle tunnel, repeated (51 L)
│   ├── run_throughput_bench.sh     iperf3 TCP + UDP, in two phases (125 L)
│   ├── run_scaling_bench.sh        parallel streams, latency under load, N clients (174 L)
│   ├── _scaling_parse.py           sum one multi-client iperf3 round, honestly (45 L)
│   ├── validate_results.py         PASS/FAIL every CSV before anyone believes it (334 L)
│   ├── plot_results.py             all eight published figures (568 L)
│   └── results/                    raw per-trial CSVs (tracked) + plots/ (8 PNGs)
│
├── deploy/
│   └── pqvpn-server.service        systemd unit for the VPS server (29 L)
│
├── docs/
│   ├── PROJECT_REPORT.md           the full technical report (1043 L)
│   ├── BENCHMARKS.md               the canonical benchmark report (614 L)
│   ├── architecture.md             component diagrams and data flow (278 L)
│   ├── setup.md                    bare-Ubuntu reproduction guide (289 L)
│   ├── DEPLOYMENT.md               netns harness and real-VPS deployment (342 L)
│   ├── ROADMAP.md                  what each phase delivered (204 L)
│   ├── benchmark-results.md        superseded first write-up, kept for provenance (7 L)
│   └── FILE_GUIDE.md               this file
│
├── .github/workflows/
│   └── clean-build.yml             CI: rebuild the whole stack on every push (58 L)
│
├── .venv/                          GITIGNORED — Python virtualenv
└── graphify-out/                   GITIGNORED — knowledge-graph tooling output
```

Also present in the author's working tree but **gitignored**, so absent from a
clone: the PowerPoint decks (`PQ-VPN-Presentation*.pptx`,
`PQ_VPN_Project_Presentation*.pptx`), their rendered PDFs, and the viva
question bank (`PQ-VPN-Viva-Question-Bank.html` / `.pdf`).

---

## 2. Reading order

If you are opening this repository for the first time, this is the shortest
path to understanding it:

1. **`common/framing.py`** (108 L) — the wire format. Everything else moves
   these frames around.
2. **`common/openssl_wrapper.py`** (136 L) — how the TLS is actually done. The
   single most consequential design decision in the project lives here.
3. **`server/vpn_server.py`** then **`client/vpn_client.py`** — the two daemons.
4. **`server/session_manager.py`** — how one server holds three clients.
5. **`client/net_config.py`** — the part that makes it a *VPN* and not a demo.

---

## 3. `common/` — shared code

Both daemons import from here. Nothing in `common/` knows whether it is running
on the server or the client, which is why the same framing and the same TLS
setup code is provably used by both ends.

### `common/__init__.py` — 0 lines

Empty, deliberately. Its only job is to make `common` an importable package so
`from common.framing import ...` works from any entry point.

### `common/framing.py` — 108 lines

**The wire format.** A TLS stream is a byte stream with no message boundaries,
so the tunnel defines its own:

```
[type:1][length:4 big-endian][payload:length]
```

- `TYPE_DATA` (`0x00`) carries one IP packet.
- `TYPE_CONTROL` (`0x01`) carries a control message: `KA` for a keepalive, a
  JSON config-push, or a JSON hello.

Functions: `pack_frame`, `unpack_frames` (returns complete frames plus the
leftover partial tail, so a short read is never lost), `pack_config` /
`unpack_config`, `pack_keepalive` / `is_keepalive`, `pack_hello` / `hello_name`,
and `validate_config`.

`validate_config` is the security-relevant one. The server pushes `ip`,
`gateway` and `dns` over TLS and the client applies them **system-wide**, so the
client validates the shape — required keys present, IPv4 addresses parseable —
before touching the routing table. A malformed or hostile config-push is
rejected rather than executed.

### `common/openssl_wrapper.py` — 136 lines

**Why this project uses `openssl` subprocesses instead of Python's `ssl`
module.** Python links the system OpenSSL, and `ssl3_set_cert` hardcodes
`EVP_PKEY` type IDs — so it cannot load an ML-DSA-65 certificate at all.
OpenSSL ≥ 3.2 dispatches certificate key loading to the provider interface,
which is what makes a pure post-quantum certificate usable in TLS 1.3. So the
daemons spawn `openssl s_server` / `openssl s_client` and relay through their
stdin/stdout pipes.

- `_make_env(mode, config_dir)` — for `pqc`, puts `/opt/openssl-pqc/bin` on
  `PATH`, both `lib64` and `lib` on `LD_LIBRARY_PATH` (OpenSSL installs to
  `lib64`, liboqs to `lib`), drops `LD_PRELOAD`, and points `OPENSSL_CONF` at
  `pki/openssl-pqc-runtime.cnf`.
- `start_tls_server(...)` / `start_tls_client(...)` — build the command line:
  mutual TLS (`-Verify`), the CRL flags (`-CRL`, `-crl_check`,
  `-verify_return_error`), `-groups X25519MLKEM768`, `-brief`, and for the
  client the resumption flags `-sess_in` / `-sess_out`.
- `parse_brief_line(line)` — reads the negotiated protocol, cipher, group and
  signature type back out of `-brief` output on stderr. This is the function
  that lets the dashboard display `Signature type: mldsa65` from a *live*
  handshake rather than from configuration.

### `common/tun_utils.py` — 66 lines

The TUN device, via `ioctl(TUNSETIFF)` on `/dev/net/tun` with `IFF_TUN |
IFF_NO_PI` (no 4-byte packet-info prefix, so what is read is a bare IP packet).

`create_tun` is idempotent: a stale device left by a crashed prior run may
already hold the address, and `ip addr add` returning "File exists" is treated
as success while any other error is real. `_open_tuns` keeps a reference to the
Python file object alive next to the raw fd, because letting it be garbage
collected closes the device out from under the relay.

### `common/daemon_utils.py` — 248 lines

**The IPC layer** — how `pqvpn status`, `pqvpn down` and the dashboard talk to a
running daemon. A Unix socket per daemon at `/tmp/pqvpn-<role>[-<instance>].sock`,
with a PID file beside it.

- `socket_path` / `pid_path` / `write_pid_file` / `remove_pid_file` /
  `read_pid_file` — the naming and lifecycle.
- `discover_sockets(role)` — every running instance of a role: the legacy
  unsuffixed path plus every `-<name>.sock`. This is how one dashboard shows
  three concurrent client daemons.
- `instance_label` — turns a socket path back into the instance name.
- `StatusTracker` — the snapshot every consumer reads: state, bytes in/out,
  negotiated suite, uptime, idle seconds, per-client rows.
- `IpcServer` — the listener. It serves a JSON request/response envelope and is
  built to survive a client that hangs up mid-request, which is directly a
  response to a real failure: the listener once died and `pqvpn status` kept
  reporting plausible-looking stale data for four days.
- `ipc_request(role, command, instance)` — the client side, used by `vpnctl` and
  the dashboard.

### `common/ticket_cache.py` — 56 lines

TLS 1.3 session tickets on disk, one file per server, under `~/.pqvpn/cache/`.
The filename is the host plus a SHA-256 prefix, so it is deterministic per
server and two servers never collide.

`rotate_ticket` is atomic (write-then-rename): a ticket half-written by a
killed process would make the next handshake fail in a way that looks like a
server problem. `*.tick` is gitignored — these are `SSL SESSION PARAMETERS`
blobs, i.e. key material, and two of them were committed once via `git add -A`
(since purged from history).

### `common/test_common.py` — 1280 lines, 92 checks

The main suite, and the one that does real cryptography rather than mocking it.
Notable groups:

- **Framing** — round-trips, partial frames, control frames, keepalives.
- **Real TLS relay** — `test_tls_relay`, `test_tls_relay_fd`: start a genuine
  `s_server`, connect a genuine `s_client`, push bytes through.
- **The full welcome→relay path** — `test_phase4_welcome_to_relay`.
- **Session management** — IP pool allocation, pool pressure reclaim, port
  recycling across reconnects, `tun_net` honouring, stale-handshake reaping,
  welcome-slot release on a failed send.
- **CRL behaviour** — `test_revoked_client_rejected_real` revokes a certificate
  and confirms a live `s_server` refuses it; `test_crl_expiry_warning` and
  `test_crl_next_update_parses_openssl_format` cover the preflight that catches
  an expired CRL *before* it rejects every client.
- **Honest failure** — `test_vpnctl_eacces_handling`,
  `test_kill_by_role_eacces_honest`: a permission error must be reported, not
  silently swallowed into a false success.
- **Status correctness** — `test_status_negotiated_lifecycle`,
  `test_drain_stderr_resumed_evidence`: the displayed suite must come from the
  handshake, and "resumed" must be evidence-backed.
- **Hostile input** — `test_validate_config_rejects_garbage`.

### `common/test_net_config.py` — 395 lines, 36 checks

Every routing, DNS and NAT command, asserted as text. Both `net_config` modules
accept `dry_run=True` and *print* the command they would run instead of running
it, which is what makes this suite possible without root — and it is the only
way the exact `ip` and `iptables` argument order gets checked at all.

Covers: the two half-routes and their ordering, the protect-route for the server
host, the case where there is no prior default route, the systemd
`/etc/resolv.conf` **symlink** being preserved and restored (a plain-file
restore would leave a static file that no longer follows systemd), the
`PQVPN_SKIP_DNS` short-circuit, resolved liveness probing, NAT rule order,
`persist_sysctl`, the MSS clamp, the IPv6 blackhole, teardown idempotence and
`destroy_tun` safety.

### `common/test_keepalive.py` — 161 lines, 7 checks

Exponential backoff growth and cap, session idle detection, `touch` resetting
idle, keepalives *preventing* an idle teardown, a keepalive actually reaching
the far end over a pipe, the relay exiting on session stop, and a
`BrokenPipeError` on keepalive write setting the session stop flag rather than
raising into the loop.

### `common/test_resumption.py` — 190 lines, 14 checks

Deterministic ticket paths, atomic rotation, no-op rotation, clearing, the
`-sess_in`/`-sess_out` flags appearing in the built command line, those flags
being wired through the client, and `test_openssl_resumption_real` — a real
handshake, a real resumption, verified against OpenSSL's own output.

---

## 4. `server/` — the server daemon

### `server/vpn_server.py` — 478 lines

The server's entry point and main loop.

1. **Preflight.** Load config; read the CRL and count its serials
   (`crl_serial_count`); parse its `nextUpdate` (`crl_next_update`) and warn if
   it is near or past expiry (`crl_expiry_warning`). An expired CRL rejects
   *every* client, and OpenSSL reports it only as "error 12 … CRL has expired"
   from inside a failed handshake — so this check exists to turn a baffling
   symptom into a clear message at startup.
2. **Set up the data plane.** Create the shared TUN device, then `setup_nat`
   (IP forwarding, MASQUERADE, FORWARD rules, MSS clamp) and `start_dnsmasq`.
3. **Listen on the welcome port** (TCP 14430). `handle_client` assigns the
   client an id, an IP from the pool and a data port, pushes that over the
   welcome connection, and spawns a dedicated `openssl s_server` for the client
   on its data port.
4. **Relay.** `relay_tls_to_tun` per client (TLS → TUN), and one
   `relay_tun_to_tls_loop` shared across all clients: it reads the TUN device,
   looks at each packet's destination IPv4 (`ipv4_dst`) and writes it to the
   matching client's TLS pipe. One reader, N writers.
5. **`idle_monitor`** — reaps sessions that went idle past the timeout, and
   slot claims that never completed mutual TLS (`handshake_timeout`).
6. **IPC + teardown.** An `IpcServer` serves `status`, `disconnect` and
   `shutdown`; `SIGTERM` runs the full teardown — NAT rules removed, dnsmasq
   stopped, socket unlinked.

### `server/session_manager.py` — 358 lines

- `IpPool` — hands out tunnel IPs from `tun_net`, honouring reservations, and
  reclaims addresses under pressure.
- **Slot-based ports.** A client's data port is derived from its **IP slot**,
  not from its monotonically increasing client id: slot 0 → `data_port_base`,
  slot 1 → `base+1`, and so on. A reconnecting client therefore never drifts
  outside the `base … base+max_clients-1` range that the firewall opens. This is
  the detail that makes the deployment's security-group rules correct
  indefinitely rather than for the first few connections.
- `WelcomeLimiter` — a token bucket per source IP on the welcome port
  (`welcome_rate`, `welcome_burst`), sized so a genuine client reconnecting on
  1–30 s backoff is never throttled.
- `ClientSession` — one client: its `s_server` process, pipes, byte counters,
  negotiated suite, uptime and idle tracking.
- `SessionManager` — the registry, the destination-IP → session map used by the
  shared TUN reader, and `_drain_proc` for clean subprocess shutdown.

### `server/net_config.py` — 188 lines

All server-side system state, every function taking `dry_run`:

- `setup_nat` — `net.ipv4.ip_forward`, MASQUERADE for the tunnel subnet on
  `outbound_iface`, the two FORWARD rules, and optionally persisting the sysctl
  to `/etc/sysctl.d/99-pqvpn.conf`.
- `_mss_specs` / the MSS clamp — clamps TCP MSS on the tunnel forward path so a
  path-MTU blackhole does not silently stall large transfers.
- `_rule_exists` — makes every rule insertion idempotent.
- `teardown_nat` — removes exactly what was added.
- `start_dnsmasq` / `stop_dnsmasq` / `drain_dnsmasq` — a dnsmasq bound to the
  tunnel interface only, forwarding to `dns_upstream`.

### `server/config.yaml`, `server/config-pqc.yaml`

The two profiles. They are **identical except for two lines** — `mode:` and
`cert_dir:` — which is the whole apples-to-apples argument of the project: the
same tunnel, the same data plane, the same AES-256-GCM, with only the
certificate and key-exchange algorithms swapped.

Keys: `host`/`port` (welcome port), `tun_name`/`tun_addr`/`tun_net`/`tun_mtu`,
`gateway`/`dns`, `groups: X25519MLKEM768`, `nat`, `outbound_iface`,
`persist_sysctl`, `dnsmasq`/`dns_upstream`, `keepalive_interval`,
`handshake_timeout`, `welcome_rate`/`welcome_burst`, `mss_clamp`.

### `server/config-vps.example.yaml`

The template for a real VPS. Differs from `config-pqc.yaml` in exactly the
places a real deployment must: `outbound_iface: eth0` (a real NIC, not a veth),
`persist_sysctl: true` so forwarding survives a reboot, and explicit
`max_clients` / `data_port_base` so the ports to open in the cloud security
group are written down. The live `server/config-vps.yaml` is **gitignored** —
it holds a real public IP.

---

## 5. `client/` — the client daemon

### `client/vpn_client.py` — 460 lines

1. `welcome_handshake(server_host, server_port)` — a short plaintext exchange on
   the welcome port that returns the data port assigned to this client.
2. `client_cert_paths(cert_dir, client_name)` — resolves this client's own
   certificate and key under `clients/`, so each client has a distinct,
   individually revocable identity.
3. Start `openssl s_client` against the assigned data port, offering any cached
   session ticket.
4. `read_pushed_config` — read and validate the server's config-push, then
   `apply_tunnel_net` to install routes and DNS.
5. **Two relay threads** — `relay_tls_to_tun` and `relay_tun_to_tls`, plus
   `keepalive_loop` every `keepalive_interval` seconds.
6. `drain_stderr` — consumes `s_client`'s stderr, parses the `-brief` lines into
   the live status, and records whether the session was *actually* resumed.
7. **Reconnect.** `next_backoff` gives exponential backoff from `backoff_min` to
   `backoff_max`; `run_session` returns and `main` reconnects.
8. On exit, `restore_tunnel_net` puts routes, DNS and the IPv6 default back.

### `client/net_config.py` — 345 lines

The file that makes this a VPN rather than a tunnel demo.

**Routing.** All IPv4 goes through `tun0` as the two half-routes `0.0.0.0/1` and
`128.0.0.0/1`. Both are more specific than any default route, so they win
regardless of the existing default's metric — the technique OpenVPN and
WireGuard use. `current_default_route` records the prior default first, and a
**protect route** keeps the path to the server itself off the tunnel, which
would otherwise route the TLS connection into its own tunnel.

**DNS**, in a strict order the tests pin down: the `PQVPN_SKIP_DNS` environment
variable short-circuits first; then `resolvectl` if systemd-resolved is actually
live (`_resolved_active`); then the `/etc/resolv.conf` fallback. Each dead path
is probed **once** and the outcome memoized, so a broken path prints exactly one
line instead of one per attempt.

**The systemd symlink problem**, handled by `_save_resolv_link` /
`_restore_resolv_link`: on a systemd machine `/etc/resolv.conf` is a *symlink*
into `/run/systemd/resolve/`. It cannot be written through — `fs.protected_regular`
gives `EPERM` even for root — so the file at that path is replaced, which
destroys the symlink. Where it pointed is recorded in a sidecar file next to the
content backup, so teardown restores the *link*. A sidecar rather than in-memory
state, because a daemon that is killed never runs its teardown and the next run
has to repair what the last one left.

**IPv6 guard.** The tunnel is IPv4-only, so with `block_ipv6: true` the IPv6
default route is blackholed while connected. Without it, a dual-stack host
would quietly send IPv6 traffic *outside* the tunnel — a leak that looks like
nothing is wrong.

### The client config files

| File | Mode | Namespace | Client cert |
|---|---|---|---|
| `config.yaml` | classical | ns2 (default) | `client` |
| `config-pqc.yaml` | pqc | ns2 (default) | `client` |
| `config-ns3.yaml` | classical | ns3 | `ns3` |
| `config-ns4.yaml` | classical | ns4 | `ns4` |
| `config-pqc-ns3.yaml` | pqc | ns3 | `ns3` |
| `config-pqc-ns4.yaml` | pqc | ns4 | `ns4` |
| `config-vps.example.yaml` | pqc | real host | `laptop` |

The `instance:` key in the ns3/ns4 files is what gives each daemon its own IPC
socket, and therefore what lets one dashboard show three concurrent clients
separately. `client_name:` selects the per-client certificate — the mechanism
behind individual revocation.

`client/config-vps.yaml` (the live one) is **gitignored**; only the `.example`
is tracked.

---

## 6. `cli/` and `pqvpn` — the command

### `pqvpn` — 56 lines, bash

The command users actually type. It removes three pieces of ceremony that have
nothing to do with the VPN: finding the project directory, finding its
virtualenv, and remembering `sudo`.

It resolves itself with `readlink -f` rather than `dirname "$0"` — reached
through the `/usr/local/bin` symlink, `$0`'s dirname is `/usr/local/bin` and the
project would be unfindable. Resolving to the real file means a moved checkout
still works with no hardcoded path.

### `cli/vpnctl.py` — 571 lines

The Click application behind `pqvpn`.

**Commands:** `up [profile]`, `down`, `status [server|client]`,
`disconnect-client <name>`, `dashboard`, and the lower-level `connect`,
`connect-server`, `connect-client`.

**Profile resolution** — `profile_config_name` / `resolve_profile`: `pqvpn up
vps` means `client/config-vps.yaml`. `_detect_mode` reads the YAML and decides
server-or-client from the presence of `server_host`, so one command drives both
daemons without a flag.

**`up` does not return until routing is actually live.**
`wait_until_connected` polls the daemon's status, with a liveness check so a
daemon that died is reported as dead rather than waited on until timeout. It
then does a public-IP lookup (`_public_ip`, skippable with `--no-ip-check`) —
the one check that proves traffic is leaving through the server rather than the
local link.

**Diagnostics on failure.** `_daemon_log` / `_log_tail` print the tail of the
daemon log when a start fails, so the error is in front of you instead of in a
file you have to be told about. `_kill_by_role` reports a permission error
honestly rather than claiming success.

`dashboard` takes `--control`, which re-execs under sudo so the dashboard's
Connect/Disconnect buttons can actually drive `vpnctl up`/`down`. The GUI
calling the CLI is deliberate: it is what keeps the button and the command from
diverging in behaviour.

---

## 7. `dashboard/` — the live web dashboard

### `dashboard/dashboard_server.py` — 362 lines

FastAPI with a WebSocket.

**One poller, N viewers.** A single background thread (`StatusFeed`) polls the
server and client IPC sockets; every browser — WebSocket or `GET /api/status` —
reads that cached snapshot. Ten open tabs cost one IPC poll per interval, not
ten.

**Endpoints:** `GET /` (the page), `GET /api/status`, `WS /ws/status`,
`POST /disconnect/{client_id}`, `POST /rekey`, `POST /connect`, `POST /down`.
`/rekey` is answered honestly rather than faked: it reports that mid-stream
rekey is unsupported and that reconnecting is what re-handshakes. There is no
corresponding server IPC command, by design.

**Auth.** Mutating endpoints require the token in an `X-PQVPN-Token` header.
The custom header is the point: it forces a CORS preflight, which closes CSRF
from any other origin, and the shared secret keeps a LAN-exposed dashboard from
being driven by strangers. `resolve_token` takes `--token`, then
`$PQVPN_DASH_TOKEN`, else generates a random one and prints it once.

**Token injection.** `GET /` is an explicit route declared *above* the
`StaticFiles` mount (mount order decides precedence) and embeds the live token
into the page **when, and only when, the server is bound to loopback**. A local
operator never copies a token out of the terminal, and the guard is not
weakened: reading the injected token requires same-origin JavaScript, and a
cross-origin POST still arrives without the header and is still refused. On a
non-loopback bind nothing is injected — that is precisely the case where the
token is the only thing standing between a stranger and the control endpoints.

**`with_control(snap)`** — attaches `control` and `profiles` to a poller
snapshot and returns a **copy**. The copy matters: the WebSocket compares
snapshots by identity to decide whether to push, so mutating the cached
snapshot in place would suppress updates. This function is also the fix for a
real bug — the page reads only `/ws/status`, which did not carry the
control-mode fields, so the Connect button was dead even when the dashboard was
launched correctly with `--control`.

`_vpnctl(*args)` shells out to `cli/vpnctl.py` and returns its stderr verbatim,
so a connect failure shows the daemon's own words rather than a generic error.

### `dashboard/index.html` — 836 lines

The entire UI in one file — CSS custom properties, Chart.js throughput chart,
per-tunnel sparklines drawn on canvas, and the control panel.

Layout: a **stage** at the top whose single word is the state (the one thing a
judge should be able to read in a second), then the negotiated-suite hero
(`renderHero`, which reads the live `X25519MLKEM768` / `mldsa65` / AES-256-GCM
values and captions them with FIPS 203/204), the throughput chart, the tunnel
table (`renderTunnels`), the client-daemon list, and an event log.

`authHeaders()` is defined once and used by **every** POST path; the bug it
replaced was two call sites with two different, drifting notions of how to
attach the token. `applyTheme` / `rethemeChart` give the page a light theme as
well as dark, with the chart re-themed to match — a projector in a lit room
being the likely demo surface. Accessibility and motion are handled:
`aria-labelledby` on every section, `:focus-visible` rings, and a
`prefers-reduced-motion` guard on the transitions.

### `dashboard/test_dashboard.py` — 571 lines, 58 checks

`isolated_sockets` points every IPC path at a private temp directory for the
duration — without it, tests that assert on "which sockets exist" read the real
`/tmp/pqvpn-*.sock` and fail whenever a genuine daemon happens to be running.

Covers: the IPC command envelope, every endpoint, the control endpoints,
control mode reaching both `/api/status` **and** `/ws/status`
(`test_ws_carries_control_mode` — 5 checks, all 5 of which fail without
`with_control`), token resolution precedence, token embedding for loopback only,
the page's auth wiring as a static check on `index.html`, profile listing,
WebSocket snapshot streaming, change detection, behaviour with missing sockets,
multi-instance discovery, and `test_single_poller` — that N viewers really do
share one poll.

`test_profile_listing` derives its expectations from what is actually on disk
(`glob` of `client/config-*.yaml`, excluding `.example.yaml`) rather than from a
hardcoded list. The hardcoded version asserted `'vps' in names`, but
`client/config-vps.yaml` is gitignored — so CI was red on every push for three
weeks while the local suite stayed green.

---

## 8. `pki/` — the two certificate authorities

Two complete, independent PKIs — one ECDSA P-256, one ML-DSA-65 — so the same
tunnel can be run under either and compared.

### `pki/_env.sh` — 41 lines

Sourced by every other script here: validates the mode argument, exports
`CERT_DIR=pki/certs/<mode>`, and for `pqc` puts `/opt/openssl-pqc/bin` on `PATH`
with `OPENSSL_CONF=pki/openssl-pqc.cnf`. The effect is that every PKI script
runs as a bare `bash pki/<script>.sh` with no caller setup, which is why none of
them can be run with the wrong OpenSSL by mistake.

### `pki/ca.cnf` — 50 lines

The shared `openssl ca` configuration: database under `$CERT_DIR/db`, the
`[leaf]` extensions for client certificates (`CA:FALSE`,
`keyUsage=digitalSignature`, `extendedKeyUsage=clientAuth`), and the policy.

Two choices are documented at length in the file itself:

- `default_md = default` — required for ML-DSA, which has no separate hash, and
  harmless for ECDSA (it picks sha256).
- `default_crl_days = 3650`, matching `default_days`. A short CRL lifetime is a
  genuine safety property *when something refreshes the CRL from a distribution
  point or OCSP*. This project ships `crl.pem` as a file beside the deployment
  and re-reads it per session — no CDP, no OCSP — so nothing refreshes it
  automatically and a short window buys no security. At the previous 30 days the
  entire VPN stopped accepting clients one month after `gen_*_ca.sh`, reporting
  only "error 12 … CRL has expired" from inside a failed handshake.

### `pki/gen_classical_ca.sh` — 39 lines

ECDSA P-256: CA, server certificate, the CA database and an empty CRL, and the
default client certificate `client` — issued through `issue_client.sh` so that
even the default client is revocable. Default OpenSSL provider only.

### `pki/gen_pqc_ca.sh` — 45 lines

The same for ML-DSA-65. The CA is minted in **one** `req -new -x509` step with
explicit `basicConstraints`, `keyUsage` and `subjectKeyIdentifier` extensions.
The earlier two-step `req` + `x509 -req -signkey` produced a bare **v1**
certificate that a released OpenSSL 3.4.7 rejects as "invalid CA certificate"
(error 79) — only the dev build on the original laptop tolerated it. The
extensions have to be given here because `openssl-pqc.cnf` has no `[req]`
section to inherit them from.

### `pki/issue_client.sh` — 28 lines

`issue_client.sh <classical|pqc> <name>` — one per-client certificate, issued by
`openssl ca` so it is **recorded in the CA database and can later be revoked**.
Validates the name against `[A-Za-z0-9._-]+`, refuses to overwrite an existing
certificate (revoke it first), `chmod 600` on the key, and verifies the result
against the CA and the CRL before exiting.

### `pki/revoke_client.sh` — 27 lines

`revoke_client.sh <mode> <name> [--kick]` — marks the certificate revoked and
regenerates `crl.pem`. Every *new* TLS session the server accepts re-reads
`crl.pem`, so the ban takes effect on the client's next connection with no
daemon restart. `--kick` additionally tears down the client's live session right
now, through the server's IPC socket.

### `pki/refresh_crl.sh` — 47 lines

Reissues the CRL from the existing CA: same key, same certificates, same serial
numbers, same revocation list — only the dates move. It exists to stop someone
reaching for `gen_<mode>_ca.sh` to fix an expired CRL, which mints a brand-new
CA and silently invalidates every certificate already distributed, including the
copy on a deployed server, which then rejects every client with "self-signed
certificate in certificate chain".

### `pki/openssl-pqc.cnf` — 22 lines, and `pki/openssl-pqc-runtime.cnf` — 10 lines

Two configs, deliberately not one. The first activates **both** the default and
`oqsprovider` and is used for key and certificate generation. The second sets
only `CipherString = DEFAULT:@SECLEVEL=0` and is what the daemons use at
runtime, where the provider is already loaded by the custom OpenSSL build.
Neither touches a system file — both are reached through `OPENSSL_CONF`.

### `pki/certs/` — gitignored

Generated: `ca.crt`/`ca.key`, `server.crt`/`server.key`, `client.crt`/`client.key`,
`crl.pem`, `db/` (index, serial, crlnumber, newcerts) and `clients/<name>.{crt,key}`,
under `classical/` and `pqc/`. **No private key material is ever committed.**

---

## 9. `scripts/` — setup, demo, deployment, recovery

### Build and install

**`scripts/install_custom_openssl.sh`** — 150 lines. The one that matters. Builds
OpenSSL 3.4.7 + liboqs + oqs-provider into `/opt/openssl-pqc`, leaving the
system OpenSSL untouched. The system OpenSSL 3.0.13 **cannot** use
provider-based signature algorithms in TLS 1.3 at all (`ssl_set_cert` hardcodes
`EVP_PKEY` type IDs), which is the reason this build exists.

The upstream versions are **pinned, and the pinning is load-bearing**: `main` of
liboqs and oqs-provider drift apart — the Docker clean build once hit an
oqs-provider `main` referencing algorithm names a same-day liboqs `main` did not
export. Releases must also be *paired*: liboqs 0.16.0 renamed the SPHINCS+
symbols that oqs-provider 0.11.0 still uses, so the newest **matching** released
pair is liboqs 0.15.0 + oqs-provider 0.11.0. ML-KEM-768, ML-DSA-65 and
X25519MLKEM768 are identical across these, so nothing is lost by pinning. All
three tags are overridable by environment variable.

**`scripts/install_liboqs.sh`** (38 L) and **`scripts/install_oqs_provider.sh`**
(36 L) — the earlier, separate installs against the *system* OpenSSL, into
`/opt/liboqs`. Both skip cleanly if already installed. Kept because they are
what `docs/setup.md`'s non-Docker path and `rollback.sh` refer to.

**`scripts/enable_oqsprovider.sh`** — 46 lines. Generates `pki/openssl-pqc.cnf`
with the correct `oqsprovider.so` path for this machine, trying both the
Debian/Ubuntu and `/usr/local` module directories. No system file is touched —
the config is reached through `OPENSSL_CONF`.

**`scripts/install_cli.sh`** — 55 lines. `sudo bash scripts/install_cli.sh` puts
`pqvpn` on `PATH`. A **symlink, not a copy**: the command always runs the script
in this checkout, so `git pull` updates it and there is no second stale copy to
debug. `/usr/local/bin` is on sudo's `secure_path` on Debian/Ubuntu, so `pqvpn`
resolves both before and after the wrapper elevates itself. `--uninstall`
removes it.

### The test harness

**`scripts/setup_routing.sh`** — 167 lines. The network-namespace harness that
makes the whole project testable on one laptop: `vpn_ns1` as the server and
`vpn_ns2` / `vpn_ns3` / `vpn_ns4` as up to three clients, each bridged to the
host by its own veth pair, with the server reachable from every client. It backs
up DNS before changing anything. `--teardown` removes all of it.

### Demos

**`scripts/demo.sh`** — 283 lines. The reproducible demo, and the project's main
proof-of-work artifact.

- **Live** (`sudo bash scripts/demo.sh [--mode pqc] [--clients 2]`) — builds the
  harness, starts the daemons, verifies the tunnel end-to-end, tears everything
  down. `--keep` leaves it up; `--egress` additionally curls a public IP-echo
  service *through* the tunnel from each client.
- **Dry-run** (`bash scripts/demo.sh --dry-run`) — no root, fully offline,
  CI-safe. Prints the plan and runs the five offline suites: **207 checks**
  (`common/test_common.py` 92, `common/test_net_config.py` 36,
  `common/test_keepalive.py` 7, `common/test_resumption.py` 14,
  `dashboard/test_dashboard.py` 58).

Each suite is run with `|| rc=$?` rather than letting `set -e` abort, because a
failing suite must be **reported**, not silently end the run — and on failure
the script now prints the suite's own `✗` lines. An earlier version discarded
them, so a failure surfaced as nothing but a changed pass count.

**`scripts/demo_vps.sh`** — 92 lines. The same idea against the deployed VPS:
connect, prove it works, disconnect. Everything runs from inside the script
specifically so there are no multi-line shell pastes to get mangled during a
live demo.

**`scripts/dashboard.sh`** — 84 lines. Opens the dashboard either for daemons on
this machine or, with `--vps`, for the real server over an SSH tunnel. The
distinction is real and worth knowing: the dashboard reads daemons' Unix
sockets, so it only ever sees daemons on the machine it runs on — local shows
the client here, `--vps` shows every connected tunnel from anywhere. Ctrl-C with
`--vps` also closes the SSH tunnel and the remote process, leaving nothing
running on the server. Note the default token here is the literal string `demo`.

### Deployment

**`scripts/deploy_vps.sh`** — 128 lines. Pushes the project to a VPS and starts
the server: rsync the code, install the systemd unit, build the PQC OpenSSL if
`/opt/openssl-pqc` is missing. **Idempotent** — re-running syncs code and config
and restarts the service, and the 15-minute OpenSSL build only happens once.
`--dry-run` prints every command and touches nothing.

### Verification and recovery

**`scripts/clean_build_check.sh`** — 57 lines. Builds the Docker image and runs
the offline suites inside it, proving the stack builds from a bare Ubuntu 24.04
and that `docs/setup.md` is true. `--live` additionally attempts the netns demo
in a privileged container; `--no-cache` forces a full rebuild.

**`scripts/diagnose_tunnel.sh`** — 111 lines. A step-by-step diagnostic that
shows exactly where TCP breaks, writing a report to `/tmp/pqvpn-diag/report.txt`
and tearing the harness down on every exit path including Ctrl-C.

**`scripts/test_file_transfer.sh`** — 181 lines. The earlier transfer check: a
1 KB HTTP transfer to prove TCP passes, and a 1 MB netcat UDP transfer for bulk.
Superseded for *measurement* by `benchmark/transfer_bench.py`, but still the
quickest way to answer "does anything go through at all".

**`scripts/backup_restore_dns.sh`** — 33 lines. `backup` / `restore` for
`/etc/resolv.conf` via `/tmp/pqvpn.resolv.conf.bak`. Backup refuses to overwrite
an existing backup — the common failure is backing up an *already broken*
resolv.conf over the good one.

**`scripts/rollback.sh`** — 47 lines. Reverts every system change this project
makes: leftover namespaces and veths, DNS, liboqs, oqs-provider. It deliberately
does **not** remove apt packages or Python dependencies.

**`scripts/run_all_benchmarks.sh`** — 313 lines. Regenerates every published
measurement in one command. The ordering is deliberate:

1. Rootless benchmarks first, so a mistake in the harness cannot cost the
   handshake numbers.
2. The namespace harness goes up; the in-tunnel benchmarks run against a live
   tunnel, per mode.
3. The harness comes down — on **every** exit path, including failure and
   Ctrl-C, because a half-built harness leaves stale `tun0` devices, namespaces
   and listening ports that make the next run fail for an unrelated reason.
4. Plots are regenerated last, from whatever was written.
5. `validate_results.py` checks the output and the script exits non-zero if
   anything is incomplete or inconsistent.

`--quick` runs short sizes and one rep; `--mode` restricts to one mode. WAN
numbers are **not** regenerated here — they need the deployed VPS.

---

## 10. `benchmark/` — measurement

### `benchmark/run_handshake_bench.py` — 285 lines

Loopback TLS handshake timing, and resumption. Starts a real `openssl s_server`,
waits for the port, then times `s_client` handshakes — `run_benchmark` for full
handshakes, `run_resumption_benchmark` for full-vs-resumed pairs using
`-sess_out` then `-sess_in`. Writes `handshake_{mode}.csv` (`trial_id,mode,ms`)
and `handshake_resume_{mode}.csv` (`trial_id,mode,kind,ms`).

This isolates the **computational** cost of post-quantum TLS: no network
latency, so the larger ML-DSA-65 certificates and the X25519MLKEM768 key share
show up as CPU time.

### `benchmark/run_wan_handshake_bench.py` — 263 lines

The same measurement over the real internet, and the project's most interesting
result. The intuition that a WAN would *dilute* the post-quantum penalty is
wrong: against a Mumbai VPS at ~99 ms RTT, PQC cost **+124 ms** rather than the
loopback +12 ms — about one extra round trip, not extra compute.

It starts a plain `s_server` on the VPS over SSH, times handshakes from this
machine, and tears the server down. The VPN daemon is not involved, which is
what makes this directly comparable to the loopback benchmark.
`_tcp_rtt` measures the round-trip time alongside, for context.

The file is careful about what it does **not** establish: the cause of that
round trip is not settled here.

### `benchmark/measure_handshake_bytes.py` — 255 lines

The timing benchmarks say post-quantum TLS costs an extra round trip; they
cannot say *why*. This measures the sizes any explanation has to work with, by
driving a real handshake under `openssl s_client -msg` and totalling the
handshake messages in each direction, full and resumed, both modes.

Three things it establishes:

- **No HelloRetryRequest occurs.** The ClientHello already carries an
  X25519MLKEM768 key share, because both ends pass `-groups X25519MLKEM768` as
  their only group. An HRR would have cost exactly one round trip and would have
  been the cheap explanation.
- **The flight exceeds TCP's initial congestion window.** The PQC server's
  flight is ~15.8 KB against the Linux default of 10 × 1460 = 14.6 KB, so it
  cannot be sent in one burst. Under mutual TLS the client's flight is ~14.3 KB,
  at the same limit.
- **Resumption removes less than expected.** A resumed PQC ClientHello is
  ~7.5 KB against ~1 KB classical, because the PSK identity *is* a 5.8 KB
  ticket — and the server still sends another one.

Sizes are properties of the messages, not of the path, so loopback is the right
place to measure them. Writes `handshake_bytes.csv`
(`mode,phase,direction,message,bytes`).

### `benchmark/transfer_bench.py` — 230 lines

Bulk transfer through the tunnel, across sizes, hashed in flight. It replaced a
download-to-a-file benchmark with two problems: it wrote received bytes to disk,
so at the measured 0.5–1.3 Gbps the *filesystem* could be the bottleneck and the
figure was partly a disk benchmark; and it measured one size (100 MB) once,
which cannot separate the fixed cost of opening a connection from the sustained
rate of the relay.

Here the server streams generated bytes and the client hashes and discards them
— nothing is stored, so a 1 GB transfer needs no free space. Both ends derive
the same stream from one seed (`_block`, `expected_digest`), so the client
verifies what it received against what should have arrived **without the hash
ever crossing the wire**.

Connect time and transfer time are recorded **separately**, because small
transfers are expected to be dominated by setup (where post-quantum is worse)
and large ones by the relay (where the modes should converge). Reporting one
total would hide exactly that. Writes
`mode,size_bytes,rep,connect_ms,transfer_ms,total_ms,mbps,sha256_ok`.

### `benchmark/run_file_transfer_bench.sh` — 57 lines

Places `transfer_bench.py`'s two ends in the right namespaces, and nothing more.
Sizes default to `1K 100K 1M 10M 100M 500M 1G`, overridable via
`PQVPN_BENCH_SIZES` / `PQVPN_BENCH_REPS`.

### `benchmark/run_latency_bench.sh` — 51 lines

`PQVPN_LAT_REPS` independent runs of `PQVPN_LAT_COUNT` pings each. The previous
version did **one** run, so the published figure had no repetition behind it at
all — and a single cold first ping was enough to drag its mean from ~1.2 ms to
2.3 ms. Repeating makes that visible instead of silent. Explicitly an *idle*
tunnel measurement; the honest hard case is in `run_scaling_bench.sh`.

### `benchmark/run_throughput_bench.sh` — 125 lines

iperf3 through the tunnel, in **two phases**, because one measurement is
destructive. `main` runs TCP and UDP-1000m and is safe to run before anything
else. `flood` runs UDP at line rate — ~60% lost by design — and leaves the relay
draining a full TUN queue. Running the flood before the scaling and transfer
benchmarks put every later measurement in its shadow, and a run taken that way
disagreed with the previous run in ways cryptography cannot explain. So the
orchestrator runs `flood` **last**, after everything else for that mode.
`PQVPN_TPUT_UDP_MAX=0` skips it entirely.

### `benchmark/run_scaling_bench.sh` — 174 lines

The three questions the other benchmarks do not answer. Run from the **host**,
not inside a namespace, because it drives several client namespaces at once.

1. **Parallel streams.** The report claims throughput is bounded by the Python
   relay rather than by OpenSSL. If the ceiling were per-stream, more streams
   would raise the total; if it is the relay, the total is flat however many
   streams are used. Until this existed the claim was asserted, never measured.
   `iperf3 -P 1,2,4,8`.
2. **Latency under load.** The published latency figure is from an idle tunnel,
   which is the easy case. A VPN that holds 2 ms idle but collapses to seconds
   under a saturating transfer is a bad VPN, and nothing else here would catch
   it. Ping *while* iperf3 runs.
3. **Multi-client scaling.** The server demultiplexes every client's return
   traffic through **one** shared TUN reader. Whether that is a bottleneck is
   the obvious question about the design.

Writes `test,variant,rep,value,unit,detail`.

### `benchmark/_scaling_parse.py` — 45 lines

Sums one multi-client iperf3 round and says **how it was composed**. It lives in
its own file rather than a heredoc inside the shell script because the previous
inline version was rewritten twice and broke silently both times — once because
`iperf3 -J` emits multi-line JSON that a line-splitting parser never
reassembled. A parser that reports nothing looks exactly like a tunnel that
carried nothing, so this one prints `<total_mbps> <clients_found> <per_client>`
with `x` marking any client that produced no result.

### `benchmark/validate_results.py` — 334 lines

**Checks a benchmark sweep before anyone believes it.** Every bad run this
project has had was silent: the IPC listener died and status kept looking fine
for four days; a line-splitting JSON parser reported nothing, indistinguishable
from a tunnel carrying nothing; `--quick` overwrote a seven-size sweep with
three sizes; a multi-client total came from an unknown number of clients. In
every case the data looked plausible and was wrong.

So this runs at the end of every sweep and prints PASS or FAIL per file, with
reasons, across three kinds of check:

- **Structure** — the rows that should exist, exist (right sizes, right reps).
- **Integrity** — nothing failed or verified incorrectly, no empty measurements.
- **Sanity** — results that physics says must hold, hold.

The sanity checks are the ones that catch a disturbed measurement. Both modes
run identical data-plane code with the same AES-256-GCM cipher, so a large
classical-vs-PQC gap in **bulk throughput** is not a finding about cryptography
— it is evidence the measurement was disturbed. `check_handshake_agreement` and
`check_cross_consistency` compare files against each other. Exit 0 = all passed,
1 = at least one FAIL; warnings are printed for a human to judge and do not fail
the run.

### `benchmark/plot_results.py` — 568 lines

Every published figure, from the committed CSVs, with `matplotlib` on the `Agg`
backend. One `_read_*` per data shape and one `_plot_*` per figure:
`handshake.png`, `handshake_resume.png`, `handshake_bytes.png`, `latency.png`,
`throughput.png`, `transfer_sweep.png`, `scaling.png`, `wan_handshake.png`.

`_clients_verdict` and `_streams_verdict` put the *conclusion* on the scaling
plot — whether the shared TUN reader or the per-stream path is the limit —
rather than leaving a reader to infer it from the bars.

### `benchmark/results/` — the raw data, tracked on purpose

Benchmark CSVs are **not** gitignored, and `.gitignore` says why: every table in
`docs/BENCHMARKS.md` must be reproducible from these files by anyone who clones
the repository. Summaries without the per-trial data behind them are not
checkable.

| File | Columns | Rows |
|---|---|---|
| `handshake_classical.csv` / `handshake_pqc.csv` | `trial_id,mode,ms` | 125 each |
| `handshake_resume_classical.csv` / `_pqc.csv` | `trial_id,mode,kind,ms` | 120 each |
| `handshake_bytes.csv` | `mode,phase,direction,message,bytes` | 34 |
| `wan_handshake_classical.csv` / `_pqc.csv` | `trial_id,mode,ms` | 25 each |
| `wan_handshake_resume_classical.csv` / `_pqc.csv` | `trial_id,mode,kind,ms` | 40 each |
| `latency_classical.csv` / `_pqc.csv` | `mode,rep,min_ms,avg_ms,max_ms,mdev_ms` | 3 each |
| `throughput_classical.csv` / `_pqc.csv` | `mode,protocol,rep,mbps,loss_pct` | 9 each |
| `file_transfer_classical.csv` / `_pqc.csv` | `mode,size_bytes,rep,connect_ms,transfer_ms,total_ms,mbps,sha256_ok` | 21 each |
| `scaling_classical.csv` / `_pqc.csv` | `test,variant,rep,value,unit,detail` | 27 each |

`benchmark/results/plots/` holds the eight PNGs, also tracked, so the figures in
the report and the deck exist in the repository rather than only on the machine
that last ran the sweep.

---

## 11. `deploy/`

### `deploy/pqvpn-server.service` — 29 lines

The systemd unit for the VPS server. `KillSignal=SIGTERM` is load-bearing —
SIGTERM is what runs the daemon's clean teardown, removing NAT rules, stopping
dnsmasq and unlinking the IPC socket, so a `systemctl stop` does not leave the
host's forwarding and NAT state modified. `Restart=on-failure` with
`RestartSec=3`. The `AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE`
line is honest about itself in a comment: the daemon runs as root today, so it
is documentation of intent rather than an active sandbox.

---

## 12. `docs/`

| File | What it is |
|---|---|
| **`PROJECT_REPORT.md`** (1043 L) | The complete technical report: what the project is, how every part works, what was used and why, what was measured, what was learned. Self-contained — readable without opening the repository — but every claim names the file that implements it, so anything in it can be checked against the code. §13 covers the WAN result, §14 the findings, §15 the limitations. |
| **`BENCHMARKS.md`** (614 L) | The canonical benchmark report, with measurement variance and exact software versions. Every table is reproducible from the committed CSVs, and the published run passes `validate_results.py` with 0 failures and 0 warnings. |
| **`architecture.md`** (278 L) | Components and data flow, with Mermaid diagrams: the dashboard, the welcome/data-port split, the shared TUN demux, the frame format. Explains the `s_server`/`s_client`-subprocess decision up front. |
| **`setup.md`** (289 L) | Reproduce the whole stack from a bare Ubuntu 24.04, 45–60 minutes. Step 0 is the Docker shortcut for anyone who only wants proof it works. |
| **`DEPLOYMENT.md`** (342 L) | Two deployment contexts: the local netns harness, and the real VPS. |
| **`ROADMAP.md`** (204 L) | The locked scope and what each phase actually delivered, including what was explicitly ruled **out** of scope and reported as future work. |
| **`benchmark-results.md`** (7 L) | **Superseded.** The original 2026-08-26 write-up, reduced to a pointer. Its environment block describes that run only, so nothing in it should be quoted; it is kept for provenance. |
| **`FILE_GUIDE.md`** | This file. |

---

## 13. Root-level files

### `README.md` — 244 lines

The front page: what the project is, why post-quantum matters
("harvest now, decrypt later"), the one-line mode switch, the headline numbers,
the quick start, and an honest limitations section. Carries the CI badge for the
clean-build workflow.

### `LICENSE` — 21 lines

The MIT licence. Added when the repository was made public; without it the
code would be "all rights reserved" by default.

### `requirements.txt` — 21 lines

`click`, `PyYAML`, `matplotlib`, `psutil`, `fastapi`, `uvicorn[standard]`,
`websockets`, `httpx` (for Starlette's `TestClient`, used by the dashboard
suite). Comments record the apt dependencies that are not pip-installable —
`build-essential cmake ninja-build libssl-dev iperf3` — and note that
`matplotlib` and `psutil` need the virtualenv on PEP 668 systems.

### `Dockerfile` — 41 lines

The clean-build proof, in two stages. Stage 1 builds the post-quantum crypto
stack from a bare `ubuntu:24.04` using **the same script a VPS would run**.
Stage 2 layers the project on top and then gates on three things in order:

1. **The verify gate** — `openssl list` must actually show `mlkem768` and
   `mldsa65` via `oqsprovider`. If the provider does not expose the algorithms
   the project depends on, the build fails here rather than three steps later
   inside a confusing handshake error.
2. Both PKIs are generated, and `openssl verify` is run against the PQC chain.
3. `CMD` runs the dry-run demo — every offline suite plus the real handshake
   benchmark.

If this image builds, `docs/setup.md` is true.

### `.dockerignore` — 9 lines

Keeps `.venv`, `.git`, `graphify-out`, `pki/certs` and benchmark CSVs out of the
build context. `pki/certs` matters most: the image must generate its own PKI, or
the build would be testing the host's certificates rather than its own.

### `.gitignore` — 57 lines

Three entries are the interesting ones, and both are comment-annotated in the file:

- **`*.tick`** — TLS session tickets written by `s_client -sess_out`. These are
  `SSL SESSION PARAMETERS` blobs: key material, not results. Two of them were
  committed once via `git add -A`, and were purged from history before the
  repository was made public.
- **`server/config-vps.yaml` / `client/config-vps.yaml`** — the live VPS
  configs, which hold a real public IP and NIC name. Only the `.example` files
  are tracked. (This is also, indirectly, what broke CI for three weeks: a test
  asserted on the `vps` profile, which does not exist in a clean checkout.)
- **`*.pptx`, root-level `*.pdf` / `*.jpeg` / `*.jpg`, and the viva question
  bank** — presentation material that lives in the working tree but is not part
  of the program. Ignored for the same reason as `*.tick`: so `git add -A`
  cannot sweep it in.

It also records a deliberate **non**-exclusion: benchmark raw data *is* tracked.

### `IMPLEMENTATION_PLAN.md` — 268 lines

The original plan, kept for provenance, with a banner saying so. It records what
was intended before anything was built — which is why it is worth keeping, and
why nothing in it should be read as a description of the system as it stands.
Where it and the current documents disagree, the current documents are right.

### `TODO.md` — 386 lines

The dated record of every stage and every verified run, including the
investigations that turned out to be dead ends. Closer to a lab notebook than a
task list, and the right place to look for *when* something was done and what
was observed at the time.

### `opencode.json` — 10 lines

Configuration for an unrelated editor tool. Nothing in the VPN reads it.

### `.github/workflows/clean-build.yml` — 58 lines

CI on every push and pull request to `main`. It is `clean_build_check.sh` split
into separate steps so that a failure is **attributable** to one of them:

1. Build the image from `ubuntu:24.04`, with the crypto stage (~3 min) cached
   across runs via GitHub Actions cache.
2. The provider verify gate, run again in CI.
3. Every unit suite plus the real handshake benchmark inside the image.
4. A second job, `live-netns`, runs the 2-client PQC tunnel demo in a privileged
   container. It is `continue-on-error: true` and checks for `/dev/net/tun`
   first, because hosted runners do not guarantee a TUN device — so it is
   informational rather than gating.

### Gitignored working files

`PQ-VPN-Presentation-v2/v3.pptx` and `.pdf`,
`PQ_VPN_Project_Presentation-1.pptx`,
`PQ_VPN_Project_Presentation_Final_Font_Fix.pptx`, `PQ-VPN-Presentation.pptx`,
`PQ-VPN-Viva-Question-Bank.html` and `.pdf` — presentation and viva material,
not part of the program and absent from a clone. `.venv/` and `graphify-out/`
are generated and gitignored.

---

## 14. Where to look for a given question

| Question | File |
|---|---|
| What goes on the wire? | `common/framing.py` |
| Why subprocesses instead of Python `ssl`? | `common/openssl_wrapper.py`, and `docs/architecture.md` §Overview |
| How does one server hold three clients? | `server/session_manager.py` |
| How does all traffic get routed through the tunnel? | `client/net_config.py` |
| What does `pqvpn up` actually do? | `cli/vpnctl.py` → `up`, `wait_until_connected` |
| Where does the dashboard get `mldsa65` from? | `common/openssl_wrapper.py` → `parse_brief_line` |
| How is a client banned? | `pki/revoke_client.sh`, and `server/vpn_server.py` CRL preflight |
| Why is the PQC handshake slower on a WAN than on loopback? | `benchmark/run_wan_handshake_bench.py`, `benchmark/measure_handshake_bytes.py` |
| Can I trust a benchmark number? | `benchmark/validate_results.py`, then the CSV it validates |
| How do I prove the whole thing builds? | `Dockerfile`, `scripts/clean_build_check.sh` |
| How do I undo everything? | `scripts/rollback.sh` |
| What was tried and abandoned? | `TODO.md`, `docs/ROADMAP.md` |
