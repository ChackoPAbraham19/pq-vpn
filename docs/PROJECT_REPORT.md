# PQ-VPN — Project Report

A complete technical description of the project: what it is, how every part
works, what was used and why, what was measured, and what was learned.

This document is self-contained — it can be read without opening the repository
— but every claim names the file that implements it, so anything here can be
checked against the code.

---

## 1. Overview

PQ-VPN is a Linux Layer-3 virtual private network whose TLS 1.3 control plane
can run in either of two cryptographic modes, selected by a single line of
configuration:

- **Classical** — ECDSA P-256 certificates with X25519 key exchange, the
  cryptography deployed VPNs use today.
- **Post-quantum** — ML-DSA-65 certificates with the X25519MLKEM768 hybrid key
  exchange, the algorithms NIST standardised in 2024.

Everything else — the tunnel device, the framing, the relay, the routing, the
session management — is identical between the two modes. That is deliberate: it
makes the cost of post-quantum cryptography measurable under otherwise
identical conditions.

### Why this matters

Public-key cryptography in use today (RSA, elliptic curves) is breakable by a
sufficiently large quantum computer. The threat is already relevant because of
**harvest now, decrypt later**: an adversary can record encrypted traffic today
and decrypt it years later once such a machine exists. Anything with a long
confidentiality lifetime is exposed now, not in the future.

NIST published the replacement algorithms in 2024 — **FIPS 203 (ML-KEM)** for
key establishment and **FIPS 204 (ML-DSA)** for signatures, both based on
lattice problems believed hard for quantum computers. Little deployed VPN
software uses them, and measurements of what they cost inside a working tunnel
are scarce.

### Result in one line

Post-quantum protection costs a **one-time penalty at connection setup and
nothing afterwards** — but on a real network that penalty takes a different
form than a loopback benchmark suggests (§13).

---

## 2. Objectives and how each was met

| Objective | Status | Evidence |
|---|---|---|
| **Functional prototype on Linux** — client + server, verified connectivity, traffic routing, secure file transfer | Met | `client/vpn_client.py` and `server/vpn_server.py` drive a real TUN device. Live runs recorded in `TODO.md`: 0 % packet loss, three concurrent clients. 100 MB transfer with `sha256_ok = yes` in both modes (`benchmark/results/file_transfer_*.csv`). Also deployed to a real AWS VPS carrying live internet traffic. |
| **Quantum-safe security integration** — lattice-based PQC via liboqs / Open Quantum Safe, replacing or augmenting RSA/ECC | Met | `scripts/install_custom_openssl.sh` builds OpenSSL + liboqs + oqs-provider. ML-DSA-65 certificates (`pki/gen_pqc_ca.sh`), X25519MLKEM768 key exchange (`common/openssl_wrapper.py`). Verified from live handshake output rather than configuration: `Signature type: mldsa65`. |
| **Benchmark + evaluation report** — PQ vs classical latency, throughput, connection setup time | Met | `docs/BENCHMARKS.md`, with raw per-trial CSVs committed under `benchmark/results/`. Covers loopback and real-WAN handshakes, resumption, latency, throughput and file transfer, with measurement variance stated. |
| **Usability + documentation** — connect/disconnect/status utility plus full documentation | Met | `cli/vpnctl.py` (six commands) and a live web dashboard (`dashboard/`), documented across six files covering setup, architecture, deployment and results. |

---

## 3. Technology stack and the reasoning behind it

| Component | Version / choice | Why |
|---|---|---|
| **OpenSSL** | 3.4.7, private prefix `/opt/openssl-pqc` | Post-quantum certificates need OpenSSL ≥ 3.2, which dispatches certificate key loading to providers. The system OpenSSL (3.0.13) cannot load ML-DSA certificates at all. A private prefix leaves the system copy untouched. |
| **liboqs** | 0.15.0 (pinned) | Open Quantum Safe's reference implementation of the NIST algorithms. |
| **oqs-provider** | 0.11.0 (pinned) | Exposes liboqs algorithms to OpenSSL as a provider, so ordinary TLS machinery can negotiate them. |
| **Signature** | ML-DSA-65 (FIPS 204, NIST Level 3) | Lattice-based signature replacing ECDSA for certificate authentication. |
| **Key exchange** | X25519MLKEM768 (FIPS 203, hybrid) | Combines classical X25519 with ML-KEM-768. The session stays secure if **either** component holds, so adopting it cannot be worse than classical alone. |
| **TLS engine** | `openssl s_server` / `s_client` subprocesses | Python's `ssl` module hardcodes `EVP_PKEY` type IDs and cannot load OID-based signature algorithms such as ML-DSA. Driving the OpenSSL binaries over pipes was the only way to use pure post-quantum certificates. **This constraint shaped the entire architecture.** |
| **Tunnel device** | Linux TUN (`/dev/net/tun`) | Delivers raw IP packets at layer 3 — a real VPN data path, not an application proxy. |
| **Daemon language** | Python 3.12 | Fast to build and reason about; its cost is the throughput ceiling (§15). |
| **Dashboard** | FastAPI + WebSocket + Chart.js | Live status push with little code; the page is one static file. |
| **Test harness** | Linux network namespaces + veth pairs | Simulates a server and three clients on one machine using real kernel networking — no mocks. |
| **Reproducibility** | Docker + GitHub Actions | Rebuilds the whole stack from a bare `ubuntu:24.04` image on every push, proving the documented setup actually works. |

Upstream versions are pinned in `scripts/install_custom_openssl.sh` because the
`main` branches of liboqs and oqs-provider drift apart — a same-day pair once
failed to compile because the provider referenced algorithm names the library
no longer exported.

---

## 4. System architecture

```
  Client host                                        Server host (VPS)
  +--------------------+  welcome: {port}  +------------------------------+
  | applications       | ----------------> | welcome listener :14430      |
  |      |             |                   | SessionManager: 3 slots      |
  |      v             |  TLS 1.3 mTLS     | s_server -accept 1443N  x N  |
  | tun0  10.8.0.N     | <===============> |        |                     |
  |      |             |  1st frame:       |        v                     |
  | vpn_client daemon  |  {ip, gw, dns}    | shared TUN reader -> demux   |
  | openssl s_client   |  then framed      | tun0 10.8.0.1                |
  | ticket cache       |  IP packets       | NAT + dnsmasq -> internet    |
  +--------------------+                   +------------------------------+
           |                                             |
           +---- /tmp/pqvpn-*.sock -- vpnctl, dashboard --+
```

### Component map

| Path | Lines | Responsibility |
|---|---|---|
| `client/vpn_client.py` | 460 | Client daemon: welcome handshake, TLS session, TUN setup, relay threads, keepalive, reconnect supervisor |
| `server/vpn_server.py` | 426 | Server daemon: welcome accept loop, per-client TLS processes, shared TUN demux, idle and stale-handshake reaping, IPC commands |
| `server/session_manager.py` | 358 | IP pool, slot-to-port binding, `ClientSession` lifecycle, welcome rate limiter |
| `cli/vpnctl.py` | 350 | Control CLI over Unix-socket IPC |
| `client/net_config.py` | 280 | Client routing and DNS: half-routes, transport-protection route, IPv6 guard, `resolvectl`/`resolv.conf` |
| `common/daemon_utils.py` | 248 | `StatusTracker`, `IpcServer`, socket and PID paths shared by both daemons |
| `dashboard/dashboard_server.py` | 211 | FastAPI status API, WebSocket stream, token-guarded actions |
| `server/net_config.py` | 188 | Server networking: NAT, forwarding, MSS clamp, dnsmasq |
| `common/openssl_wrapper.py` | 136 | Builds and spawns the `s_server` / `s_client` command lines per mode |
| `common/framing.py` | 108 | Wire format and its helpers |
| `common/tun_utils.py` | 66 | TUN device creation and teardown |

Supporting directories: `pki/` (certificate authority scripts), `scripts/`
(build, harness, demo, deployment), `benchmark/` (measurement tools and raw
results), `deploy/` (systemd unit).

---

## 5. How it works, end to end

### 5.1 Life of a connection

1. **Welcome handshake (plaintext).** The client opens a plain TCP connection to
   port 14430. The server allocates a slot and replies with a single control
   frame containing **only a port number**. Nothing sensitive crosses this
   channel (`welcome_handshake` in `client/vpn_client.py`).
2. **Per-client TLS listener.** Before replying, the server spawns
   `openssl s_server -accept <port> -naccept 1` for this client and writes the
   full configuration into that process's stdin, where it waits in the pipe.
3. **Mutual TLS.** The client connects `openssl s_client` to the assigned port.
   Both sides present certificates and verify them against the CA; either side
   failing aborts the connection. In PQC mode this is where ML-DSA-65
   authentication and the X25519MLKEM768 key exchange happen.
4. **Configuration push — inside the tunnel.** Because `s_server` only reads
   stdin once a connection exists, the configuration frame queued in step 2 is
   delivered as the *first application record after mTLS succeeds*. The client
   receives `{id, ip, gateway, dns, port, subnet}`, validates it
   (`framing.validate_config`), and only then touches the system.
   **An attacker on the plaintext welcome channel can therefore never influence
   the client's routing or DNS.**
5. **Tunnel setup.** The client creates `tun0` at the assigned address, installs
   routes and DNS (§8), and sends a hello frame naming itself.
6. **Relaying.** Four threads run per session: TUN→TLS, TLS→TUN, a keepalive
   sender, and a stderr parser that records what OpenSSL actually negotiated.
7. **Liveness.** The client sends a keepalive every 15 s. The server tears down
   any session silent for 45 s. If the TLS side dies, the client's reconnect
   supervisor retries with exponential backoff from 1 s to 30 s.
8. **Teardown.** On exit the client removes its routes, restores DNS and deletes
   the TUN device; the server drains the TLS process and frees the slot.

### 5.2 Life of a packet

**Outbound (client application → internet).** An application sends a packet; the
kernel routes it to `tun0` because of the tunnel routes; `relay_tun_to_tls`
reads it, prepends a 5-byte header and writes it to the `s_client` stdin pipe;
OpenSSL encrypts it into the TLS stream. On the server, `relay_tls_to_tun` reads
from that client's `s_server` stdout, reassembles frames and writes the raw
packet to `tun0`. The kernel forwards it, NAT rewrites the source to the
server's public address, and it leaves for the internet.

**Inbound (internet → client application).** The reply arrives at the server,
NAT maps it back to the client's tunnel address, and the kernel puts it on
`tun0`. A **single shared reader** (`relay_tun_to_tls_loop`) picks it up, looks
at the destination IP, finds the owning session in a dictionary keyed by the
packed address, frames it and writes it to that client's TLS pipe. The client
unwraps it and writes it to its own `tun0`, where the kernel delivers it to the
waiting application.

The demux loop is written so it can never die: a failed write to one dying
session logs and drops that packet rather than taking down every other client.

---

## 6. Protocol design

Every byte on the wire is wrapped in a typed frame:

```
+---------+-----------+------------------------+
| 1 byte  | 4 bytes   |        payload         |
|  type   | length BE | (IP packet or control) |
+---------+-----------+------------------------+
```

**Why explicit framing is required.** TCP is a byte stream with no message
boundaries, and a single read can return part of a packet or several at once.
This matters more here than in a classical VPN: post-quantum handshake
artefacts are far larger — the server's certificate message is 11 168 bytes in
PQC mode against 882 classical (§13.1) — so relying on read boundaries would
corrupt traffic. The
receiver accumulates bytes and only processes a frame once `length` bytes have
arrived (`common/framing.py`).

Frame types:

| Type | Value | Purpose |
|---|---|---|
| `TYPE_DATA` | `0x00` | A raw IP packet from the TUN device |
| `TYPE_CONTROL` | `0x01` | Configuration push, keepalive (`KA`), or client hello (`HELLO <name>`) |

Control frames carry three messages: the **config push** (JSON, sent once by the
server inside TLS), the **keepalive** (every 15 s), and the **hello** (the
client naming itself after mTLS, used for display and targeted disconnects —
never for authorisation).

`validate_config` rejects a malformed push before anything is applied: all
required keys present, `ip`/`gateway`/`dns` parse as IPv4, `port` an integer in
range, `subnet` a valid network.

---

## 7. Cryptography and PKI

Two independent certificate trees live side by side, one per mode, generated by
`pki/gen_classical_ca.sh` and `pki/gen_pqc_ca.sh`:

| Element | Classical | Post-quantum |
|---|---|---|
| CA | ECDSA P-256, self-signed, v3 with `CA:TRUE` | ML-DSA-65, v3 with `basicConstraints`, `keyUsage`, `subjectKeyIdentifier` |
| Server certificate | ECDSA P-256 | ML-DSA-65 |
| Client certificates | ECDSA P-256, one per client | ML-DSA-65, one per client |
| Key exchange | X25519 | X25519MLKEM768 hybrid |

**Mutual TLS on every connection.** The server runs with `-Verify 1` and the
client with `-verify 1`, so both ends must present a certificate signed by the
CA. There is no password or pre-shared key anywhere in the system: possession
of a valid certificate is the only way in.

**Per-client certificates and revocation.** Certificates are issued through a
real `openssl ca` database (`pki/ca.cnf`, `pki/issue_client.sh`), so each client
has its own identity and serial number. `pki/revoke_client.sh <mode> <name>`
marks a certificate revoked and regenerates `crl.pem`; with `--kick` it also
ends the client's live session through the server's IPC socket.

The server passes `-CRL crl.pem -crl_check -verify_return_error` to **every
per-session TLS process**, and the CRL is read when that process starts. A
revocation therefore takes effect on the client's very next handshake with no
daemon restart. Verified live: a revoked client receives TLS alert 44
(`certificate revoked`) and relays nothing, while other clients are unaffected.
In PQC mode the CRL itself is ML-DSA-65-signed.

---

## 8. Networking

**Address and port allocation.** The IP pool is capped at `max_clients`, and
each address is a fixed *slot*: slot 0 always uses `data_port_base`, slot 1
`base+1`, and so on (10.8.0.2–.4 on ports 14431–14433 by default). A
reconnecting client is therefore always reassigned inside the range the
firewall opens. A slot is only released after its TLS process has actually
exited, so a port is never reissued while the previous listener is alive.

**Server side** (`server/net_config.py`): enables IP forwarding, installs NAT
MASQUERADE for the tunnel subnet out the egress interface, adds FORWARD rules,
and optionally runs `dnsmasq` bound to the tunnel address so clients' DNS
queries never leave via their local network.

**Client side** (`client/net_config.py`), three measures that each address a
real leak:

- **`0.0.0.0/1` + `128.0.0.0/1` half-routes.** These cover all of IPv4 but are
  more specific than any default route, so they win regardless of the existing
  default's metric. The original implementation installed
  `default … metric 50`, which silently lost to a metric-0 default route —
  tunnel pings worked while internet traffic bypassed the tunnel entirely.
- **A pinned `/32` route** to the VPN server through the original gateway, so
  the TLS connection carrying the tunnel is not itself routed into the tunnel.
- **IPv6 default blackholed** while connected, because the tunnel is IPv4-only
  and v6-capable sites would otherwise be reached outside it with the real
  address. Removed on disconnect.

**DNS restoration.** On a systemd machine `/etc/resolv.conf` is a *symlink*
into `/run/systemd/resolve/`. The client cannot write through it —
`fs.protected_regular` returns EPERM even for root — so the fallback path
replaces the file at that path, which destroys the link. Teardown therefore
restores the **link**, not only the bytes: the target is recorded in a sidecar
file beside the content backup, chosen over in-memory state because a daemon
that is killed never runs its teardown and the next run has to repair what it
left. Restoring contents alone would leave a static `/etc/resolv.conf` that
still resolves — so nothing looks wrong — but no longer follows
`systemd-resolved`.

**MTU handling.** The tunnel MTU is 1400 against a typical 1500 upstream. The
server clamps TCP MSS to the path MTU in both directions; without it, sites
sending full-size segments stall on path-MTU discovery — pages half-load and
downloads hang.

---

## 9. Security design

| Threat | Mitigation |
|---|---|
| Passive eavesdropping, now or after quantum computers exist | TLS 1.3 with AES-256-GCM; in PQC mode the key exchange is hybrid X25519 + ML-KEM-768, so recorded traffic stays protected unless both are broken |
| Impersonating the server | Mutual TLS: the client verifies the server certificate against its CA |
| Unauthorised clients | Mutual TLS: the server requires a CA-signed client certificate (`-Verify 1`) |
| Tampering with pushed configuration | The plaintext welcome channel carries only a port number; tunnel IP, gateway and DNS arrive as the first frame *inside* mTLS and are validated before use |
| Compromised or lost client key | Per-client certificates plus a CRL checked by every new session; `revoke_client.sh --kick` also terminates the live session |
| Denial of service on the welcome port | Slot claims that never complete mTLS are reaped after 10 s; a per-source token bucket (1/s, burst 3) is applied before any slot or process is allocated |
| Unauthorised dashboard actions | `POST /disconnect` and `/rekey` require the `X-PQVPN-Token` header; the custom header forces a CORS preflight, which also blocks cross-site requests. (`/rekey` is guarded but deliberately declines — `s_server` cannot renegotiate, so it answers `ok: false` and says to reconnect; see §15.) |
| DNS leaking to the local network | Server-side `dnsmasq` on the tunnel address is pushed to the client and applied to the system resolver |
| IPv4 traffic escaping the tunnel | `/1` half-routes beat any default route regardless of metric |
| IPv6 traffic escaping the tunnel | v6 default route blackholed while connected |

Two deliberate design choices are worth stating plainly. The welcome port is
**unauthenticated by design** — it must be, since certificate verification
happens in the TLS layer that follows — and its cost is bounded by the reap
timer and the rate limiter rather than eliminated. The client's hello name is
**display metadata only**; authorisation is always the certificate and the CRL.

---

## 10. Operations and tooling

**`vpnctl`** (`cli/vpnctl.py`) talks to the daemons over Unix-domain sockets:

| Command | Purpose |
|---|---|
| `up [profile]` | Connect by profile name (`vps`, `pqc`), waiting until routing is actually live, then report the negotiated crypto and the new public IP |
| `down` | Disconnect and restore routes and DNS |
| `dashboard [--vps]` | Open the live web dashboard, locally or over an SSH forward to a deployed server. Runs unprivileged. |
| `connect` | Start a daemon, auto-detecting server or client from the config |
| `connect-server` / `connect-client` | Start a specific role |
| `status [role]` | Mode, state, negotiated cryptography, tunnel address, session and daemon uptime, byte counters, per-client table |
| `disconnect` | Stop the server and every client instance |
| `disconnect-client <name>` | End the session(s) of one named client |

**Dashboard** (`dashboard/`) — FastAPI with a WebSocket stream. It leads with the
cryptographic suite actually negotiated for the session, then live aggregate
throughput, a per-tunnel table with sparklines and rates, the client daemons on
the host, and an event log. Mutating actions require a token header. One
background poller feeds every browser, so extra tabs cost nothing.

It reads Unix sockets, so **it only sees daemons on the machine it runs on**:
run it on the client to see the client's view, or on the server (reached over an
SSH port-forward) to see every connected tunnel. In the namespace harness both
live on one machine, so a single dashboard shows everything.

**`scripts/demo.sh`** — the reproducible demo. `--dry-run` runs every unit suite
plus a real handshake benchmark with no root and no network. The live path
builds the namespace harness, starts a server and up to three clients, verifies
each tunnel with pings, prints status, runs `iperf3`, and tears everything down.
`--egress` additionally proves a client reaches the public internet through the
tunnel.

**Deployment** — `scripts/deploy_vps.sh <user@host>` rsyncs the project, builds
the PQC stack if absent, creates the virtualenv, configures the firewall,
installs the systemd unit (`deploy/pqvpn-server.service`) and starts the
service. It is idempotent and has a `--dry-run` mode.

**Reproducibility** — `Dockerfile` plus `scripts/clean_build_check.sh` rebuild
everything from bare `ubuntu:24.04`, gate on the provider actually exposing
ML-KEM-768 and ML-DSA-65, generate both PKIs and run the suites inside the
image. `.github/workflows/clean-build.yml` runs the same thing on every push.

---

## 11. Testing and validation

Three independent layers.

**Unit suites — 193 checks across five files.** Most drive *real* `openssl`
processes rather than mocks, so they test the actual integration:

| Suite | Checks | Covers |
|---|---|---|
| `common/test_common.py` | 92 | Framing round-trips, welcome-to-relay end to end over real TLS, slot and port recycling, stale-handshake reaping, rate limiter, CRL revocation against a live `s_server`, config validation, IPC |
| `common/test_net_config.py` | 36 | NAT rule construction, MSS clamp, route protection and restoration, IPv6 guard, DNS fallback, safe TUN teardown |
| `common/test_keepalive.py` | 7 | Backoff growth, idle detection, keepalive behaviour, session-stop cascade |
| `common/test_resumption.py` | 14 | Ticket cache paths, atomic rotation, wrapper flags, real OpenSSL prime-then-`Reused` |
| `dashboard/test_dashboard.py` | 44 | IPC envelope compatibility, endpoints, token auth (401/503), single-poller behaviour, WebSocket delivery |

**Clean-build check.** The Docker image proves the documented installation works
on a machine with no prior state. This caught seven portability bugs the
development laptop had been masking (§14).

**Live integration runs.** Each recorded with its date in `TODO.md`: three
concurrent PQC clients with isolated tunnels, reconnect after a forced
disconnect, revocation refusing a banned client, welcome-port rate limiting,
and full internet egress through the tunnel.

---

## 12. Results

### Measurement environment

Ubuntu 24.04 (kernel 7.0.0-42), Python 3.12, tunnel MTU 1400. Handshake
measurements over loopback; tunnel latency, throughput, transfer and scaling
inside the network namespace harness; real-WAN measurements from a laptop in
India to an AWS Lightsail instance in `ap-south-1` (Mumbai).

**Everything in §12.1–§12.5 and §12.8 comes from a single run on 2026-11-04**
(`sudo bash scripts/run_all_benchmarks.sh`, about nine minutes), so those tables
may be compared with one another — which earlier revisions of this report had to
warn against, because the figures were stitched together from three different
days. Only the WAN results (§12.6) are from a separate, earlier session. Classical mode uses the system
OpenSSL 3.0.13; PQC mode used OpenSSL 3.4.7-dev with liboqs 0.16.0 and
oqs-provider 0.12.0-dev.

These differ from the versions in §3 for a reason.
`scripts/install_custom_openssl.sh` pins OpenSSL 3.4.7, liboqs 0.15.0 and
oqs-provider 0.11.0 so that clean builds and CI are reproducible, but those pins
were added *after* the stack on the measurement machine was built. The numbers
below therefore come from the slightly newer versions named here. Re-measuring
on the pinned set is outstanding; the classical-to-PQC comparison is not
expected to move, since both modes traverse identical code paths.

**A note on variance.** The measurement host is an interactive desktop, not a
quiet benchmark machine. Across five identical repetitions the classical median
ranged 14.5–18.9 ms and the PQC median 27.3–31.5 ms, and the *per-repetition*
ratio ranged 1.64–2.16x. **The ratio between modes
is far more stable than either absolute value**, so the tables below pool
repetitions and state the spread. Single-run figures from this host should not
be quoted to two decimal places as if repeatable.

### 12.1 Full TLS handshake, loopback (5 repetitions x 25 trials, n = 125)

| Mode | Median (ms) | Min (ms) | Max (ms) | Repetition medians (ms) |
|------|-------------|----------|----------|-------------------------|
| Classical (P-256 + X25519) | 16.84 | 11.13 | 20.59 | 14.54 – 18.85 |
| PQC (ML-DSA-65 + X25519MLKEM768) | 30.08 | 19.96 | 36.18 | 27.33 – 31.45 |

PQC is **1.79x** slower (per-repetition ratios 1.64–2.16x). Cause: size. An
ML-DSA-65 certificate is 5577 bytes DER against 386 for ECDSA P-256, its
signature is 3309 bytes against about 72, and the X25519MLKEM768 key share is
1222 bytes against 32. Measured message by message in §13.1.

### 12.2 Session resumption, loopback (3 repetitions x 20 trials, n = 60 per kind)

| Mode | Full median (ms) | Resumed median (ms) | Speedup |
|------|------------------|---------------------|---------|
| Classical | 16.98 | 13.20 | 1.29x |
| PQC | 28.06 | 24.34 | 1.15x |

The full-handshake medians here now agree closely with §12.1 (16.98 vs 16.84
classical). In earlier revisions they diverged, and the report had to explain
the gap as differing host load; both tables now come from the same run, which
removes the explanation along with the gap.

### 12.3 Tunnel ping latency (100 pings, namespace harness)

| Mode | Min (ms) | Avg (ms) | Max (ms) | Mdev (ms) |
|------|----------|----------|----------|-----------|
| Classical | 0.816 | 1.296 | 4.426 | 0.659 |
| PQC | 0.693 | 1.146 | 3.275 | 0.244 |

A difference of **0.15 ms** in the mean, with PQC nominally *ahead* — which is
the clearest way of saying the two are indistinguishable, since no mechanism
would make post-quantum key exchange reduce steady-state ping. Earlier runs
reported means near 2.3 ms with maxima of 117–119 ms; those maxima were the
first ping against a cold tunnel and they dominated the mean. This run does not
reproduce them, and the spread is correspondingly tighter (mdev 0.24–0.66 ms
against 11.5–11.8 ms).

This is an **idle** tunnel. §12.8 measures the same ping under saturation, where
the picture is very different.

### 12.4 Throughput (iperf3, 10 s, namespace harness)

| Mode | Protocol | Throughput (Mbps) |
|------|----------|-------------------|
| Classical | TCP | 635.15 |
| Classical | UDP | 999.90 |
| PQC | TCP | 635.86 |
| PQC | UDP | 999.90 |

**The two modes agree to within 0.1%.** Earlier revisions reported 1268 against
533 Mbps and had to argue that the 2.4x gap was scheduling variance rather than
a cryptographic effect; that argument was correct but it rested on asking the
reader to discount the headline number. This run makes the argument unnecessary.

The single-stream TCP figure is a property of the relay, not of the stream:
§12.8 shows two parallel streams reaching about 1 Gbps.

### 12.5 File transfer across sizes (namespace harness)

The server generates a deterministic byte stream on demand; the client hashes it
in flight and discards it. Nothing is written to disk at either end, and both
ends compute SHA-256 over the same stream and compare. Seven sizes, three
repetitions each per mode — 42 transfers per mode — reported as median with the
min–max range.

**This replaces the previous measurement, and it is a correction rather than a
refresh.** The earlier version downloaded a real 100 MB file to disk using
`http.server`. At the rates involved a disk write can be slower than the tunnel,
so the published 0.68 s and 1.31 s figures were partly a filesystem benchmark,
and the roughly 2x gap between modes was never safely attributable to anything
in the VPN. Streaming also removes the free-space constraint, which is what
makes a 1 GB measurement possible on a host with 1.5 GB free.

| Size | Classical (Mbps) | PQC (Mbps) | PQC / classical |
|------|------------------|------------|-----------------|
| 1 KB | 9.7 (7.4–18.8) | 13.7 (8.3–14.6) | 1.41x |
| 100 KB | 352.6 (263.3–356.9) | 295.3 (180.4–309.6) | 0.84x |
| 1 MB | 425.0 (407.9–430.5) | 418.1 (414.3–480.8) | 0.98x |
| 10 MB | 553.2 (494.0–553.4) | 548.9 (483.4–558.8) | 0.99x |
| 100 MB | 635.1 (551.6–972.7) | 589.5 (530.8–900.2) | 0.93x |
| 500 MB | 1085.3 (1074.6–1107.7) | 1045.5 (1036.4–1070.5) | 0.96x |
| 1 GB | 1081.3 (1046.9–1097.8) | 1041.5 (1035.6–1043.0) | 0.96x |

All 42 transfers per mode verified SHA-256 correct, including both 1 GB runs.
Throughput climbs with size and flattens near 1.05–1.09 Gbps from 500 MB
onwards, which is the relay ceiling of §12.8 reached once the transfer is long
enough to amortise setup.

**A prediction recorded before the measurement, which the measurement refuted.**
The sweep was designed around a stated expectation: that small transfers would
be setup-dominated and would therefore make PQC look worse, with the two modes
converging at large sizes. The convergence held — 0.96x to 0.99x from 1 MB
upwards. The small-size penalty did not appear, and the reason is structural
rather than statistical. The per-transfer setup in this harness is a plain TCP
connect *inside an already-established tunnel* (`socket.create_connection`,
median 0.70 ms classical against 0.83 ms PQC across all 42 rows). The TLS
handshake is not inside this measurement loop at all: it is paid once, when the
tunnel comes up, and §12.1 is where it appears. No PQC-specific small-transfer
penalty was therefore possible here, and the 1 KB row is measuring scheduler
jitter — its min–max ranges overlap almost entirely, and an earlier run of the
same benchmark placed classical ahead at that size by a comparable margin. **The
1 KB row should not be read as a result in either direction.**

### 12.6 Real-WAN handshake (laptop to Mumbai VPS)

Full handshake, 25 trials per mode, TCP round-trip measured at 99.2 ms:

| Mode | Median (ms) | Min (ms) | Max (ms) |
|------|-------------|----------|----------|
| Classical | 203.0 | 90.8 | 304.4 |
| PQC | 327.0 | 202.4 | 668.2 |

PQC is **1.61x** slower over the WAN, i.e. **+124 ms** — a smaller *ratio* than
loopback's 1.79x but a far larger absolute cost. §13 explains why.

Full versus resumed, 20 trials per mode, round-trip 51.6 ms during this later
run:

| Mode | Full median (ms) | Resumed median (ms) | Saved | Speedup |
|------|------------------|---------------------|-------|---------|
| Classical | 198.6 | 152.9 | 45.7 ms | 1.30x |
| PQC | 272.0 | 200.0 | 72.0 ms | 1.36x |

### 12.7 Internet egress through the tunnel

Verified both in the namespace harness and against the VPS: a client's public
IP as seen by an external service becomes the server's address,
`https://www.google.com` returns HTTP 200 in 0.43 s through the tunnel, and
`dig @10.8.0.1 github.com` resolves in 52 ms via the server's `dnsmasq`.

### 12.8 Scaling and behaviour under load

Three questions the rest of the suite does not answer, measured with
`benchmark/run_scaling_bench.sh` at 10 s per data point.

**Is the throughput ceiling per-stream or per-relay?** `iperf3 -P 1,2,4,8` from
one client:

| Parallel streams | Classical (Mbps) | PQC (Mbps) |
|------------------|------------------|------------|
| 1 | 758.73 | 700.96 |
| 2 | 965.22 | 999.80 |
| 4 | 995.30 | 996.99 |
| 8 | 1009.87 | 967.96 |

It is the relay. Two streams reach roughly 1 Gbps; four and eight add nothing.
Had the ceiling been per-stream the total would have continued to climb. §15
previously asserted this limitation; it is now measured.

**What happens to latency when the tunnel is saturated?** Ping from the same
namespace as the load, so it queues behind the same traffic:

| Mode | Idle (ms) | Under load (ms) | Degradation |
|------|-----------|-----------------|-------------|
| Classical | 1.210 | 17.203 | 14x |
| PQC | 1.086 | 30.574 | 28x |

Latency degrades by more than an order of magnitude. This is the honest
counterpart to §12.3: an idle tunnel at about 1 ms says little about a tunnel in
use. The mechanism is queueing inside the single-threaded relay — a ping waits
behind whatever bulk traffic is already buffered. The difference between the two
modes in this table is **not** evidence of a cryptographic cost: it is one 10 s
sample per mode with no repetitions, while every other data-plane measurement
here puts the modes within 1%. The finding is that both degrade sharply; the
per-mode difference is unmeasured.

**Does the server scale across clients?** Total throughput with one, two and
three clients transferring simultaneously, each against its own `iperf3` server
on its own port so that the load is genuinely concurrent:

| Concurrent clients | Classical (Mbps total) | PQC (Mbps total) |
|--------------------|------------------------|------------------|
| 1 | 714.72 | 737.07 |
| 2 | 670.03 | 669.67 |
| 3 | 458.73 | 455.49 |

**Total throughput falls as clients are added.** Three clients together move
about 36% less data than one client alone: the design does not merely fail to
scale, it degrades. The cause is the architecture this benchmark was written to
test. `server/vpn_server.py` demultiplexes every client's return traffic through
a single shared TUN reader inside one `select` loop, so each additional client
contends for a resource that was already the bottleneck in the parallel-stream
table above. Both modes track each other to within 3%, which isolates the result
from the cryptography: it is a relay-architecture finding, and the remedy is the
compiled multi-queue relay listed in §16.

---

## 13. Key finding — the post-quantum penalty is a round trip, not CPU

The expectation going into the WAN measurement was that network latency would
*dilute* the post-quantum overhead: if PQC costs 13 ms of extra computation on
loopback, that should vanish against a 99 ms round trip. **The measurement shows
the opposite.**

- Classical: **203 ms**, almost exactly two round trips (TCP connect, then the
  TLS 1.3 handshake) — textbook behaviour.
- PQC: **327 ms**, roughly 3.3 round trips — **+124 ms**, an order of magnitude
  more than the +13 ms seen on loopback.

The extra time is not compute: the whole computational difference measured on
loopback is 13 ms (§12.1). To find out what the handshake is actually doing, every
message was captured with `openssl s_client -msg` in both modes — full and
resumed — by `benchmark/measure_handshake_bytes.py`
(`benchmark/results/handshake_bytes.csv`). Message sizes are properties of the
protocol rather than the path, so loopback is the right place to measure them.

### 13.1 What the handshake actually carries

| Message | Classical | PQC |
|---|---|---|
| ClientHello | 288 | **1585** (the X25519MLKEM768 key share alone is 1222) |
| ServerHello | 122 | 1210 |
| Certificate (server → client) | 882 | **11168** (server certificate + CA, 5577 B each) |
| CertificateVerify (server) | 79 | **3317** (an ML-DSA-65 signature is 3309 B) |
| Client certificate | `Certificate` 980 | **`CompressedCertificate` 10898** |
| CertificateVerify (client) | 78 | **3317** |
| NewSessionTicket (x2) | 713 each | **5833 each** |
| **Resumed** ClientHello | **1039** | **7456** |

The client certificate is worth a second look: on the PQC path it arrives as a
`CompressedCertificate` (RFC 8879) rather than a `Certificate`, because the
custom OpenSSL 3.4 negotiates certificate compression where the system 3.0.13
used for classical mode does not. Compression is already in use and still
leaves 10.9 KB, so it is not an optimisation left on the table.

Totalled per direction, excluding the tickets that follow the handshake:

| | Classical | PQC |
|---|---|---|
| Full: client → server | 1399 B | **15 852 B** |
| Full: server → client | 1261 B | **16 001 B** |
| Resumed: client → server | 1091 B | **7 508 B** |
| Resumed: server → client | 186 B | 1 274 B |

### 13.2 Three things this settles

**There is no HelloRetryRequest.** The obvious cheap explanation for exactly one
extra round trip would be the server rejecting the client's key share and asking
for another. It does not happen: the ClientHello already carries an
X25519MLKEM768 key share, because `common/openssl_wrapper.py` passes
`-groups X25519MLKEM768` as the only group on both ends. Checked in the full and
the resumed handshake, both modes.

**The PQC handshake flight does exceed the initial congestion window.** Linux
starts a connection with 10 segments, about 14 600 bytes; the deployment VPS
uses that default (its route sets no `initcwnd`). The PQC server's flight is
**16 001 bytes** and the client's, under mutual TLS, is **15 852** — both above
the window, where classical sends well under a tenth of it. The flight therefore
cannot go out in a single burst. This part is measurement, not inference.

**Resumption does not remove the bulk**, which is why it recovered so much less
than expected. A resumed PQC ClientHello is **7456 bytes against 1039
classical**, because the PSK identity *is* the 5833-byte ticket, and the server
answers with another one. Resumption skips the certificates but still moves
roughly 8.8 KB where classical moves 1.3 KB.

### 13.3 What is established and what is not

What is measured: PQC costs about one extra round trip on a real path
(3.3 versus 2.0), and its handshake flight exceeds the initial congestion
window in both directions.

What is **not** established is that the second fact causes the first. The
measured round-trip counts are consistent with the larger handshake flight
costing an extra round trip; this is inferred, not confirmed by packet capture.
Other candidates remain open, and more than one may contribute:

- the flight exceeding `initcwnd`, needing a second burst;
- the oversized resumed ClientHello (7.4 KB) and the 5.8 KB tickets, which keep
  even a resumed PQC handshake far larger than a classical full one;
- ordinary WAN variance — the two runs were at different round-trip times
  (99.2 ms and 51.6 ms) and the PQC maximum reached 668 ms.

The resumption result points the same way — at 51.6 ms RTT resumption saved
72 ms for PQC against 46 ms for classical — but it is a strong hint rather than
proof. If certificate size alone bought a whole round trip, a resumed handshake
should have closed the gap to roughly the CPU difference (~13 ms). It did not:
PQC stays 47 ms (~0.9 RTT) slower than classical even resumed, and only about
26 ms of the full-handshake gap disappeared. The byte counts above say why —
resumption removes the certificates but not the ticket.

Settling this needs a packet capture of a WAN handshake showing where the
one-round-trip gap falls inside the flight. That is future work (§16).

Two practical conclusions hold regardless of which candidate dominates:

1. **Loopback benchmarks understate post-quantum handshake cost**, because they
   measure CPU while deployments pay for bytes and round trips. Published
   figures that come only from localhost should be read with that in mind.
2. **Message size, not algorithm speed, is the lever.** Every PQC number above
   is a size: 11 KB of certificates, a 3.3 KB signature, a 5.8 KB ticket. That
   is also why resumption helps less here than the loopback figures suggested.

---

## 14. Engineering findings from live testing

Each of these was found by running the system for real rather than by reading
code, and each is fixed in the repository.

| Symptom | Cause | Fix |
|---|---|---|
| Deployment succeeded, then the VPS became unreachable by every route including the provider's browser console | The deploy script ran `ufw --force enable` without first allowing SSH; Ubuntu's default incoming policy is deny | Allow the live sshd port first, verify SSH still answers after enabling, and refuse to leave ufw enabled without an SSH rule |
| Browsing through the tunnel worked, but every DNS query timed out — while `ping` to the server's tunnel address succeeded, making the tunnel look healthy | `ufw route allow` governs traffic *forwarded through* the box; `dnsmasq` runs *on* it, so queries hit the INPUT chain and were dropped. ufw answers ICMP by default, masking the problem | Add `ufw allow in on tun0` |
| Tunnel pings worked but internet traffic bypassed the tunnel entirely | The client installed `default … metric 50`; the existing default route had metric 0 and won | Install `0.0.0.0/1` + `128.0.0.0/1`, which beat any default route regardless of metric |
| Seven portability bugs (missing `LD_LIBRARY_PATH`, unpinned upstream branches, library path unreachable without `ldconfig`, no default `openssl.cnf`, `sudo` assumed present, PKI scripts entering an ignored directory, a v1 CA certificate rejected by released OpenSSL) | The development laptop had accumulated state that masked each one | Found by building from bare `ubuntu:24.04` in Docker; all fixed in the install and PKI scripts |
| Resumption benchmark reported "no successful trials" | `--resume` ignored `--out-dir`, writing the session ticket into a directory it never created, so priming failed and every resumed trial silently fell back to a full handshake | Honour `--out-dir`, create it first |
| An idle client kept reporting "connected" after the server tore its session down | The TUN-to-TLS thread only noticed a dead peer on its next write, which on an idle tunnel never came | A per-session stop event set when the TLS side closes, cascading to the other threads |

A recurring lesson runs through these: **a system can look healthy on the
signal you happen to be watching.** ICMP answered while DNS was blocked; pings
crossed the tunnel while real traffic bypassed it; a client reported connected
while the server had already forgotten it. Each was caught by checking a
different signal than the obvious one.

---

## 15. Limitations

Stated plainly, because knowing where a system stops is part of describing it.

- **Throughput is bounded by the Python relay**, not by cryptography, and this
  is now measured rather than argued (§12.8): two parallel streams saturate the
  relay at about 1 Gbps and four or eight add nothing. A single stream gets
  roughly 635 Mbps and a long bulk transfer about 1.08 Gbps. OpenSSL does the
  cryptography in a separate process and is not the bottleneck.
- **Latency collapses under load.** The tunnel holds about 1.2 ms idle and
  17–31 ms while saturated, a degradation of 14–28x (§12.8). Traffic queues
  inside the single-threaded relay, so an interactive session sharing the tunnel
  with a bulk transfer will feel it.
- **The server does not scale across clients — it degrades.** Total throughput
  falls from about 715 Mbps with one client to 459 Mbps with three (§12.8),
  because every client's return traffic is demultiplexed through one shared TUN
  reader. This is the most serious limitation in the list: adding clients makes
  the system worse, not merely no better.
- **Session resumption is per-server-process.** Every client session gets a
  fresh `s_server`, which generates fresh ticket-encryption keys, so a ticket
  cached from a previous server lifetime never resumes. This cannot be fixed
  with `openssl s_server`: it exposes no way to load a ticket key. The fix is a
  small custom TLS server using `SSL_CTX_set_tlsext_ticket_key_evp_cb`.
- **No mid-stream rekey.** `s_server` does not support renegotiation, so a new
  key exchange happens on reconnect rather than within a live session.
- **Three concurrent clients** by default. The pool size is configurable, but
  the firewall range and documentation assume three.
- **Revocation is file-based** — `crl.pem` is read when each session starts.
  There is no OCSP or CRL distribution point, so an updated CRL must be
  distributed with the deployment.
- **The tunnel is IPv4-only.** IPv6 is blackholed while connected rather than
  carried.
- **The welcome port is unauthenticated** by design. Cost is bounded by a 10 s
  reap and a per-source rate limit, but a distributed flood could still occupy
  the slots.
- **Tunnel latency, throughput, transfer and scaling were measured in the
  namespace harness**, not over the WAN. Only handshake and resumption have
  real-internet numbers, so every figure above describes a tunnel with no
  propagation delay and no packet loss — favourable conditions.

---

## 16. Future work

- **Compiled data plane.** Replacing the Python relay with a compiled
  implementation, or using `splice()` to avoid userspace copies, would lift the
  throughput ceiling.
- **Custom TLS server.** Linking against the same OpenSSL to install a ticket-key
  callback would give resumption that survives restarts *and* enable TLS 1.3
  KeyUpdate for mid-stream rekeying — one change solving two limitations.
- **Packet capture of a WAN handshake**, to settle §13: whether the
  one-round-trip gap really falls where the flight exceeds the congestion
  window, or somewhere else.
- **Smaller credentials.** §13 shows the cost is message size, and certificate
  compression is already in use (the client certificate is sent as
  `CompressedCertificate` and still runs to 10.9 KB). The remaining levers are a
  smaller parameter set (ML-DSA-44), not sending the CA with the leaf, and
  shrinking the 5.8 KB session ticket that keeps even resumed handshakes large.
- **Online revocation** via OCSP stapling instead of a shipped CRL.
- **WAN latency and throughput** through the tunnel, to complete the real-network
  picture alongside the handshake numbers.

---

## 17. How to reproduce

### From a clean Ubuntu 24.04 machine

```bash
sudo bash scripts/install_custom_openssl.sh      # OpenSSL + liboqs + oqs-provider
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
bash pki/gen_classical_ca.sh && bash pki/gen_pqc_ca.sh
sudo bash scripts/demo.sh --mode pqc --clients 2 --egress
```

### Without root, offline

```bash
bash scripts/demo.sh --dry-run --mode pqc
```

Runs all 193 checks plus a real handshake benchmark in both modes.

### In Docker, from a bare image

```bash
bash scripts/clean_build_check.sh
```

### Verifying the published numbers

Raw per-trial data is committed under `benchmark/results/`, so every table in
§12 can be recomputed:

```bash
python3 -c "import csv,statistics as s;r=[float(x['ms']) for x in csv.DictReader(open('benchmark/results/handshake_classical.csv'))];print(round(s.median(r),2),len(r))"
```

The transfer sweep (§12.5) and the scaling tables (§12.8) recompute the same
way, from `file_transfer_<mode>.csv` and `scaling_<mode>.csv`. Every row of the
transfer CSV carries its own `sha256_ok` flag, so the integrity claim is checked
rather than taken on trust:

```bash
awk -F, 'NR>1{n++;if($8=="yes")ok++}END{print ok"/"n" verified"}' benchmark/results/file_transfer_pqc.csv
```

To regenerate all of §12 rather than recompute it — harness up, both modes,
teardown, plots — one command does it:

```bash
sudo bash scripts/run_all_benchmarks.sh
```

The message sizes in §13 are reproducible the same way — the script reprints the
tables and rewrites `benchmark/results/handshake_bytes.csv`:

```bash
.venv/bin/python3 benchmark/measure_handshake_bytes.py
```

It needs both PKIs (`pki/gen_classical_ca.sh`, `pki/gen_pqc_ca.sh`) and the PQC
OpenSSL stack, but no root and no network.

---

## 18. Repository map

| Path | Purpose |
|---|---|
| `client/` | Client daemon, routing and DNS configuration, per-mode configs |
| `server/` | Server daemon, session manager, server networking, configs |
| `common/` | Framing, TUN utilities, OpenSSL wrapper, IPC and status, ticket cache, four test suites |
| `cli/vpnctl.py` | Control CLI |
| `dashboard/` | FastAPI + WebSocket dashboard, its page, its test suite |
| `pki/` | CA generation, per-client issue and revoke, OpenSSL configs |
| `scripts/` | Stack build, namespace harness, demo, VPS deploy, clean-build check, diagnostics, rollback |
| `benchmark/` | Loopback and WAN benchmarks, the handshake byte-size measurement behind §13, plotting, raw CSVs, plots |
| `deploy/` | systemd unit |
| `docs/` | This report, architecture, benchmarks, setup, deployment, roadmap |
| `Dockerfile` | Clean-build proof from `ubuntu:24.04` |
| `.github/workflows/` | CI running the clean build on every push |

---

## 19. Related documents

| Document | Contents |
|---|---|
| [`README.md`](../README.md) | Project overview and quick start |
| [`docs/architecture.md`](architecture.md) | Design detail with diagrams |
| [`docs/BENCHMARKS.md`](BENCHMARKS.md) | Full benchmark report and methodology |
| [`docs/setup.md`](setup.md) | Step-by-step build from a bare system |
| [`docs/DEPLOYMENT.md`](DEPLOYMENT.md) | VPS deployment and client management |
| [`docs/ROADMAP.md`](ROADMAP.md) | Development phases and decisions |
| `TODO.md` | Dated record of every verified run |
