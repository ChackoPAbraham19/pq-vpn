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
quiet benchmark machine. Seven independent 20-trial measurements of the *same*
PQC full handshake, taken within two hours of each other, gave medians of 26.69,
32.25, 29.71, 32.11, 29.66, 32.04 and 27.66 ms — a 1.21x spread with nothing
changed between them. **The ratio between modes is far more stable than either
absolute value**, so the tables below pool repetitions and state the spread.
Single-run figures from this host should not be quoted to two decimal places as
if repeatable, and a gap smaller than roughly 20% between two handshake medians
is not a result.

**Every data point below is repeated**, and the tables state the spread. This
was not true of earlier revisions, and the difference matters: two figures that
this report previously published — a rising parallel-stream curve and a UDP
throughput of 999.90 Mbps — did not survive repetition and measurement of the
right quantity. Both corrections are recorded in place rather than silently
overwritten (§12.4, §12.8).

**The sweep validates itself.** `benchmark/validate_results.py` runs as the
final step and exits non-zero if any file is incomplete, any transfer failed its
hash, any multi-client total came from fewer clients than it claims, the two
sections that time the same handshake disagree, or the two modes diverge at bulk
transfer sizes. The run published here passes with zero failures and zero
warnings.

### 12.1 Full TLS handshake, loopback (5 repetitions x 25 trials, n = 125)

| Mode | Median (ms) | Min (ms) | Max (ms) |
|------|-------------|----------|----------|
| Classical (P-256 + X25519) | 18.95 | 12.52 | 22.14 |
| PQC (ML-DSA-65 + X25519MLKEM768) | 29.24 | 18.38 | 35.90 |

PQC is **1.54x** slower, which should be read as "about one and a half times".
The previous run of this benchmark gave 16.84 / 30.08 ms (1.79x) and the
September run 15.80 / 27.98 ms (1.77x); the code did not change between them, so
the movement is the variance noted above. An independent 25-trial check two
hours later gave 31.94 ms for PQC, bracketing the 29.24 recorded here. The
repetition medians are not recoverable from the committed CSV, which pools the
five repetitions without a repetition column. Cause of the penalty: size. An
ML-DSA-65 certificate is 5577 bytes DER against 386 for ECDSA P-256, its
signature is 3309 bytes against about 72, and the X25519MLKEM768 key share is
1222 bytes against 32. Measured message by message in §13.1.

### 12.2 Session resumption, loopback (3 repetitions x 20 trials, n = 60 per kind)

| Mode | Full median (ms) | Resumed median (ms) | Speedup |
|------|------------------|---------------------|---------|
| Classical | 17.62 | 14.59 | 1.21x |
| PQC | 29.75 | 25.48 | 1.17x |

The full-handshake medians here agree with §12.1 to within 7% (classical 17.62
against 18.95, PQC 29.75 against 29.24), which is what makes either table
quotable: both time the same handshake through the same function, so agreement
between them is a check and disagreement is a fault.

**A figure was measured, checked and discarded here.** The sweep of 15:45
recorded a PQC full-handshake median of **16.96 ms** in this section while
§12.1, minutes earlier, recorded 29.24 ms — the same handshake, 1.72x apart,
with classical agreeing between the two sections. Treating that as a
measurement to reproduce rather than a result to explain, seven independent
re-measurements gave 26.69, 32.25, 29.71, 32.11, 29.66, 32.04 and 27.66 ms. None
approached 16.96, and §12.1's 29.24 sits among them. The sweep's figures for
this section were therefore discarded and the section re-measured at full
published scale. **The cause of the 16.96 ms reading was never established;
what is established is that it does not reproduce**, and the validator now
fails any run whose two sections disagree by more than 1.4x.

### 12.3 Tunnel ping latency (100 pings, namespace harness)

Three repetitions of 100 pings per mode; the table reports the median of the
three.

| Mode | Min (ms) | Avg (ms) | Max (ms) | Mdev (ms) |
|------|----------|----------|----------|-----------|
| Classical | 0.781 | 1.288 | 3.536 | 0.458 |
| PQC | 0.710 | 1.354 | 3.949 | 0.543 |

A difference of **0.07 ms** in the mean, with PQC nominally *behind*. The
previous run had PQC 0.15 ms *ahead*. A difference whose sign reverses between
runs is noise, and demonstrating that is why this measurement is now repeated;
asserting it, as earlier revisions did, was weaker. The two modes are
indistinguishable at about 1.3 ms, with a floor near 0.7–0.8 ms.

Earlier runs reported means near 2.3 ms with maxima of 117–119 ms; those maxima
were the first ping against a cold tunnel and they dominated the mean. A
discarded warm-up now runs before each mode's measurements for that reason.

This is an **idle** tunnel. §12.8 measures the same ping under saturation, where
the picture is very different.

### 12.4 Throughput (iperf3, 10 s, namespace harness)

Three repetitions of 10 s per point; median with the min–max range.

| Mode | Measurement | Delivered (Mbps) | Loss |
|------|-------------|------------------|------|
| Classical | TCP, 1 stream | 669.11 (636.84–677.85) | — |
| Classical | UDP, 1 Gbps offered | 957.06 (950.00–969.96) | 4.28% |
| Classical | UDP, line rate (`-b 0`) | 1396.49 (1348.34–1462.93) | 59.91% |
| PQC | TCP, 1 stream | 686.85 (649.74–731.32) | — |
| PQC | UDP, 1 Gbps offered | 972.41 (963.40–994.63) | 2.74% |
| PQC | UDP, line rate (`-b 0`) | 1317.92 (1309.43–1343.87) | 61.92% |

**A previously published figure was wrong, and the error was systematic rather
than statistical.** Earlier revisions of this report gave UDP throughput as
999.90 Mbps in both modes and concluded a 0% penalty. That number was `end.sum`
from the iperf3 JSON document — the rate the client *transmitted*. The rate that
*arrived* is `end.sum_received`, and at 1 Gbps offered it is 957–972 Mbps,
because 3–4% of the datagrams were dropped. Reporting a sending rate as a
throughput result made the tunnel look lossless when it was not. Every UDP row
now carries its loss percentage, because a UDP throughput figure without one is
not interpretable.

**Only the `-b 0` row is a ceiling.** Offering 1 Gbps answers "if we ask for
1 Gbps, does it arrive?"; offering line rate finds the limit, which is about
1.3–1.4 Gbps delivered with roughly 60% of the offered traffic discarded. That
loss is structural, not a defect: the tunnel's outlet is a single TCP/TLS
stream, so UDP flooded into the TUN device queues and is dropped rather than
delivered. The measurement is destructive — it leaves the relay draining a full
queue — and is therefore run last in each mode, after everything else has been
measured.

**The two modes agree to within 3%** on TCP and 2% on delivered UDP. Earlier
revisions reported 1268 against 533 Mbps and had to argue that the 2.4x gap was
scheduling variance rather than a cryptographic effect; that argument was
correct, but it rested on a single unrepeated sample per mode.

The single-stream TCP figure is a property of the relay, not of the stream — but
§12.8 shows that eight parallel streams do not exceed it either, and that the
gap between this figure and the bulk-transfer figure of §12.5 is **not**
explained by stream count, as earlier revisions claimed.

### 12.5 File transfer across sizes (namespace harness)

The server generates a deterministic byte stream on demand; the client hashes it
in flight and discards it. Nothing is written to disk at either end, and both
ends compute SHA-256 over the same stream and compare. Seven sizes, three
repetitions each per mode — 21 transfers per mode — reported as median with the
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
| 1 KB | 11.0 (8.3–13.2) | 8.5 (7.6–9.8) | 0.77x |
| 100 KB | 421.1 (219.3–428.2) | 261.0 (223.6–292.1) | 0.62x |
| 1 MB | 413.3 (404.0–488.4) | 377.7 (355.6–422.4) | 0.91x |
| 10 MB | 528.2 (502.2–565.4) | 523.2 (519.2–551.3) | 0.99x |
| 100 MB | 580.2 (526.0–727.4) | 558.3 (527.0–660.2) | 0.96x |
| 500 MB | 1095.7 (1070.9–1103.9) | 1032.7 (1026.3–1038.3) | 0.94x |
| 1 GB | 1074.8 (1071.9–1091.0) | 1028.6 (1024.4–1032.9) | 0.96x |

All 21 transfers per mode verified SHA-256 correct, including both 1 GB runs.
Throughput climbs with size and flattens near 1.03–1.10 Gbps from 500 MB
onwards, once the transfer is long enough to amortise setup. The two modes agree
to within 6% from 1 MB upwards and to within 4% at 500 MB and 1 GB.

**A prediction recorded before the measurement, which the measurement refuted.**
The sweep was designed around a stated expectation: that small transfers would
be setup-dominated and would therefore make PQC look worse, with the two modes
converging at large sizes. The convergence held — 0.96x to 0.99x from 1 MB
upwards. The small-size penalty did not appear, and the reason is structural
rather than statistical. The per-transfer setup in this harness is a plain TCP
connect *inside an already-established tunnel* (`socket.create_connection`,
median 0.81 ms classical against 0.71 ms PQC across all 21 rows). The TLS
handshake is not inside this measurement loop at all: it is paid once, when the
tunnel comes up, and §12.1 is where it appears. No PQC-specific small-transfer
penalty was therefore possible here.

**The 1 KB and 100 KB rows should not be read as results in either direction.**
Neither is long enough to measure a rate: a 100 KB transfer completes in 2–4 ms,
so one scheduling delay moves the figure by a factor of two. The evidence that
these columns are jitter rather than signal is that they neither hold still nor
agree with one another. The previous run placed PQC *ahead* at 1 KB (1.41x) and
behind at 100 KB (0.84x); this run reverses the first (0.77x) and widens the
second (0.62x); and classical's own three repetitions at 100 KB span 219 to
428 Mbps, a 1.95x spread against itself. The ranges of the two modes overlap at
both sizes. Whatever these columns measure, it cannot be cryptography: the modes
share the data plane and the cipher, and the handshake is not in the loop.

### 12.6 Real-WAN handshake (laptop to Mumbai VPS)

Full handshake, 25 trials per mode, TCP round-trip measured at 99.2 ms:

| Mode | Median (ms) | Min (ms) | Max (ms) |
|------|-------------|----------|----------|
| Classical | 203.0 | 90.8 | 304.4 |
| PQC | 327.0 | 202.4 | 668.2 |

PQC is **1.61x** slower over the WAN, i.e. **+124 ms** — a ratio comparable to
loopback's 1.54x but a far larger absolute cost (+124 ms against +10 ms). §13
explains why.

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
`benchmark/run_scaling_bench.sh` at 10 s per data point, **three repetitions per
point**.

**Is the throughput ceiling per-stream or per-relay?** `iperf3 -P 1,2,4,8` from
one client:

| Parallel streams | Classical (Mbps) | PQC (Mbps) |
|------------------|------------------|------------|
| 1 | 676.68 (656.68–706.68) | 673.59 (663.36–712.94) |
| 2 | 712.09 (707.98–720.06) | 681.15 (678.79–689.20) |
| 4 | 692.02 (671.16–704.29) | 679.12 (677.90–721.75) |
| 8 | 669.61 (665.77–672.91) | 671.76 (662.33–741.06) |

**It is the relay, and one stream already saturates it.** All 24 measurements
lie between 656 and 741 Mbps. Adding streams does not raise the total, which is
what a per-stream ceiling would do. §15 previously asserted this limitation; it
is now measured.

**This replaces a published figure, and the correction strengthens the result.**
The previous revision of this table reported 758.73, 965.22, 995.30 and 1009.87
Mbps across 1, 2, 4 and 8 streams and concluded that two streams saturate the
relay. Those were four single 10-second samples with no repetitions. Repeating
each point three times gives a flat series, and the flat series is the one that
is internally consistent: the earlier run reported 635 Mbps for one stream
(§12.4), 715 Mbps for one client (below) and 1010 Mbps for eight streams, three
values for substantially the same measurement, where this run reports 669, 655
and 670. The conclusion does not weaken — the ceiling is still the relay — but
it is reached one stream earlier than previously claimed.

**An open question this measurement creates.** A single iperf3 TCP stream
reaches about 670 Mbps (§12.4) while a 1 GB bulk transfer through the same
tunnel sustains about 1075 Mbps (§12.5), and eight parallel iperf3 streams do
not close the gap. Earlier revisions explained the difference as "a single-stream
and a saturating measurement of the same ceiling"; this table rules that
explanation out, because saturating with eight streams still yields 670. The two
tests do differ in direction — iperf3 sends client to server, `transfer_bench.py`
streams server to client — but that asymmetry has not been measured, so **the
1.6x gap is currently unexplained**. A reverse-direction `iperf3 -R` row would
settle it. The gap appears in the previous run as well (635 against 1081), so it
is long-standing rather than new.

**What happens to latency when the tunnel is saturated?** Ping from the same
namespace as the load, so it queues behind the same traffic:

| Mode | Idle (ms) | Under load (ms) | Degradation |
|------|-----------|-----------------|-------------|
| Classical | 1.31 (1.25–1.34) | 30.85 (17.29–31.48) | 24x |
| PQC | 1.31 (1.22–1.39) | 20.75 (18.75–28.07) | 16x |

Latency degrades by more than an order of magnitude. This is the honest
counterpart to §12.3: an idle tunnel at about 1.3 ms says little about a tunnel
in use. The mechanism is queueing inside the single-threaded relay — a ping
waits behind whatever bulk traffic is already buffered.

The difference between the two modes in this table is **not** evidence of a
cryptographic cost, and the repetitions now demonstrate that rather than
asserting it. The previous run put classical at 17 ms and PQC at 31 ms; this run
puts classical at 31 ms and PQC at 21 ms, and the two ranges overlap (17.3–31.5
against 18.8–28.1). **The ordering reverses between runs**, which is what noise
looks like. The finding is that both degrade by roughly 15 to 25 times; the
per-mode difference is unmeasured.

**Does the server scale across clients?** Total throughput with one, two and
three clients transferring simultaneously, each against its own `iperf3` server
on its own dedicated port (5301–5303) so that the load is genuinely concurrent.
Each port is verified to be listening before a round starts, and every row
records how many clients actually reported.

| Concurrent clients | Classical (Mbps total) | PQC (Mbps total) |
|--------------------|------------------------|------------------|
| 1 | 655.39 (652.67–681.68) | 659.30 (650.54–671.29) |
| 2 | 373.30 (370.50–381.24) | 389.39 (377.98–393.93) |
| 3 | 369.98 (368.29–373.88) | 370.94 (369.54–375.33) |

**Total throughput falls as clients are added.** Three clients together move
about **44% less data than one client alone**, and most of the loss occurs on
the step from one client to two: the design does not merely fail to scale, it
degrades. The cause is the architecture this benchmark was written to test.
`server/vpn_server.py` demultiplexes every client's return traffic through a
single shared TUN reader inside one `select` loop, so each additional client
contends for a resource that was already the bottleneck in the parallel-stream
table above. Both modes track each other to within 1% at three clients, which
isolates the result from the cryptography: it is a relay-architecture finding,
and the remedy is the compiled multi-queue relay listed in §16.

**The clients do share fairly.** The per-client composition recorded in the CSV
shows 188+186, 185+185 and 192+189 Mbps at two clients, and 132+124+119,
129+122+119 and 123+125+120 at three. No client is starved; the total is simply
smaller.

**Why the per-client composition is recorded at all.** An earlier run of this
benchmark reported two clients and three clients at 367.64 and 367.70 Mbps —
suspiciously identical — and nothing in the recorded data could distinguish a
real ceiling from a total that silently came from two clients because the third
never started. The multi-client section also shared port 5201 with the sections
above it, and an `iperf3` server handles one test at a time, so a client could
be refused as *server busy* while the others succeeded, producing exactly that
shape. Dedicated ports and a recorded client count remove both ambiguities. A
round with a missing client is now written as `INCOMPLETE_2of3` with no value,
and the validator fails the run rather than letting the number be plotted.

---

## 13. Key finding — the post-quantum penalty is a round trip, not CPU

The expectation going into the WAN measurement was that network latency would
*dilute* the post-quantum overhead: if PQC costs 10 ms of extra computation on
loopback, that should vanish against a 99 ms round trip. **The measurement shows
the opposite.**

- Classical: **203 ms**, almost exactly two round trips (TCP connect, then the
  TLS 1.3 handshake) — textbook behaviour.
- PQC: **327 ms**, roughly 3.3 round trips — **+124 ms**, an order of magnitude
  more than the +10 ms seen on loopback.

The extra time is not compute: the whole computational difference measured on
loopback is 10 ms (§12.1). To find out what the handshake is actually doing, every
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
should have closed the gap to roughly the CPU difference (~10 ms). It did not:
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
  is now measured rather than argued (§12.8): a single iperf3 stream already
  saturates the relay at about 670 Mbps, and two, four or eight add nothing.
  OpenSSL does the cryptography in a separate process and is not the bottleneck.
- **A long bulk transfer reaches about 1075 Mbps through the same tunnel**,
  roughly 1.6x the iperf3 figure, and parallel streams do not close the gap.
  This is unexplained (§12.8). It is a limitation of the measurement rather than
  of the system, but it means the single number "throughput" is not yet
  well-defined for this VPN, and a reverse-direction measurement is needed
  before one is quoted.
- **Latency collapses under load.** The tunnel holds about 1.3 ms idle and
  21–31 ms while saturated, a degradation of 16–24x (§12.8). Traffic queues
  inside the single-threaded relay, so an interactive session sharing the tunnel
  with a bulk transfer will feel it.
- **The server does not scale across clients — it degrades.** Total throughput
  falls from about 655 Mbps with one client to 370 Mbps with three — a 44% loss,
  most of it on the step from one client to two (§12.8) — because every client's
  return traffic is demultiplexed through one shared TUN reader. This is the most serious limitation in the list: adding clients makes
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
