# PQ-VPN — Post-Quantum Secure VPN Tunnel

[![clean build](https://github.com/ChackoPAbraham19/Major-Project/actions/workflows/clean-build.yml/badge.svg)](https://github.com/ChackoPAbraham19/Major-Project/actions/workflows/clean-build.yml)

A working Linux VPN (real TUN device, real routing, real DNS) whose TLS 1.3 control
plane can run either **classical** crypto (ECDSA P-256 + X25519) or **post-quantum**
crypto (NIST **ML-DSA-65** certificates + hybrid **X25519MLKEM768** key exchange) with a
one-line config switch — so the cost of going post-quantum can be measured
apples-to-apples on the same tunnel.

**Why:** traffic recorded today can be decrypted once a large quantum computer exists
("harvest now, decrypt later"). NIST finalised the replacement algorithms (FIPS 203/204)
in 2024; this project puts them in a complete VPN and measures what they cost.

**Result:** post-quantum security costs about 1.8x on the one-time handshake
(≈12 ms on loopback) and **nothing** on steady-state latency or throughput.
Measured against a real VPS, the cost turns out to be **one extra round trip**
— +124 ms at 99 ms RTT — because the larger certificate flight overflows the
initial congestion window; session resumption recovers most of it.

## Quick start

```bash
# 0. Rootless sanity — all unit suites + a real 5-trial handshake benchmark (both modes)
bash scripts/demo.sh --dry-run --mode pqc

# 1. One-time: build OpenSSL 3.4 + liboqs + oqs-provider into /opt/openssl-pqc (~15 min)
sudo bash scripts/install_custom_openssl.sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
bash pki/gen_classical_ca.sh && bash pki/gen_pqc_ca.sh

# 2. Live demo on one machine: netns harness → server + 3 clients → ping → iperf3 → teardown
sudo bash scripts/demo.sh --mode pqc --clients 3           # add --keep to leave it running

# 3. Same tunnel over the real internet, against a deployed VPS (docs/DEPLOYMENT.md)
sudo bash scripts/demo_vps.sh              # public IP before/after, DNS, a real website

# 4. Or prove it builds from a bare Ubuntu 24.04 (Docker)
bash scripts/clean_build_check.sh
```

Once a tunnel is up:

```bash
.venv/bin/python3 cli/vpnctl.py status                     # server + every client, ports, bytes, negotiated crypto
bash scripts/dashboard.sh                                 # http://127.0.0.1:8080, token "demo"
bash scripts/dashboard.sh --vps                           # same, but for a deployed server, over SSH
.venv/bin/python3 cli/vpnctl.py disconnect
```

`dashboard.sh` picks a free port if 8080 is taken; override with
`PQVPN_DASH_PORT` / `PQVPN_DASH_TOKEN`, or run `dashboard/dashboard_server.py`
directly for full control.

## What's implemented

Everything below is exercised by the unit suites (**143 tests**, `demo.sh --dry-run`) and
was verified live in the network-namespace harness with 2–3 concurrent PQC clients.

- **Dual-mode PKI** — ECDSA P-256 chain and ML-DSA-65 chain, both with mutual TLS.
  Live `s_client -brief` shows `Signature type: mldsa65` on the PQC path.
- **Tunnel** — raw IP packets from a TUN device, framed `[type:1][len:4][payload]`
  inside a TLS 1.3 stream via `openssl s_server`/`s_client` subprocesses (Python's
  `ssl` module cannot load ML-DSA certificates at all).
- **Multi-client** — a plaintext *welcome* port hands each client a dedicated data port;
  the tunnel IP/gateway/DNS config is pushed as the first frame **inside** mTLS, so
  nothing tamperable is ever applied. 3 client slots with **fixed ports 14431–14433**
  that survive any number of reconnects; a shared TUN reader demuxes by destination IP.
- **Resilience** — 15 s keepalives, idle teardown at 45 s, per-client `s_server` exits
  the moment its client hangs up, client reconnects with 1→30 s backoff. Verified live:
  a kicked client is back in ~1 s.
- **Hardening** — welcome slot claims that never finish mTLS are reaped at 10 s; a
  per-source token bucket (1/s, burst 3) on the welcome port; the config push is
  validated before touching routes or DNS.
- **Per-client certificates + revocation** — `pki/issue_client.sh` mints a cert per
  client from a real CA database; `pki/revoke_client.sh <mode> <name> --kick` puts it
  on the CRL every new session checks and ends the live tunnel. Works for both PKIs
  (the CRL is ML-DSA-65-signed in PQC mode).
- **Real networking** — NAT MASQUERADE + FORWARD rules, TCP MSS clamping, dnsmasq on
  the tunnel, default route via tun0 with a pinned `/32` transport route, IPv6 leak
  guard, DNS via `resolvectl` or `resolv.conf`, all restored on exit. `demo.sh --egress`
  proves a client reaches the real internet *through* the tunnel.
- **TLS session tickets** — cached per server and offered on reconnect (`Reused` verified
  for both modes in the benchmark; see limitations).
- **Tooling** — `vpnctl` CLI over Unix-socket IPC; FastAPI + WebSocket **dashboard** with
  per-tunnel rates and a token-guarded kick; `demo.sh` for a reproducible run.
- **CI** — every push rebuilds the entire PQC stack from `ubuntu:24.04`, gates on the
  provider exposing ML-KEM-768 / ML-DSA-65, and runs every suite inside the image
  (`.github/workflows/clean-build.yml`).

## Architecture

```
  Client (vpn_ns2..4)                                   Server (vpn_ns1)
  ┌──────────────────┐   welcome: {port} (plaintext)   ┌────────────────────────────┐
  │ app → tun0 10.8.0.N │ ───────────────────────────► │ welcome listener :14430    │
  │  vpn_client daemon  │   TLS 1.3 mTLS to :1443N     │ SessionManager: 3 slots    │
  │  openssl s_client   │ ◄──────────────────────────► │ s_server -accept 1443N ×N  │
  │  ticket cache       │   1st frame: {ip,gw,dns}     │ shared TUN demux → tun0    │
  └──────────────────┘   then framed IP packets       │ NAT + dnsmasq → internet   │
                                                       └────────────────────────────┘
        vpnctl / dashboard ◄── /tmp/pqvpn-{server,client}.sock (JSON status + commands)
```

**Classical:** ECDSA P-256 certs, X25519, system OpenSSL 3.0.
**PQC:** ML-DSA-65 certs, `-groups X25519MLKEM768`, custom OpenSSL 3.4.7 + oqs-provider
(`/opt/openssl-pqc`). Full detail in [`docs/architecture.md`](docs/architecture.md);
a single self-contained write-up of the whole project — design, implementation,
results and findings — is in [`docs/PROJECT_REPORT.md`](docs/PROJECT_REPORT.md).

## Benchmarks (25 trials per mode, loopback + netns)

| Metric | Classical | PQC | Overhead |
|--------|-----------|-----|----------|
| **TLS handshake** (median, n=125) | 15.8 ms | 28.0 ms | 1.77× |
| **Resumed handshake** (median, n=60) | 11.8 ms | 20.3 ms | 1.08–1.12× vs full |
| **Ping RTT through tunnel** | 2.29 ms | 2.33 ms | 1.7 % |
| **TCP throughput** (iperf3) | 1268 Mbps | 533 Mbps | run variance |
| **UDP throughput** | 999.9 Mbps | 999.9 Mbps | 0 % |
| **100 MB file transfer** | 0.68 s | 1.31 s | run variance |
| **Handshake over real WAN** (Mumbai VPS) | 203 ms | 327 ms | 1.61× (+124 ms) |
| **…resumed** | 152.9 ms | 200.0 ms | saves 72 ms |

Larger certificates and key shares make the one-time handshake slower; the data plane
is unaffected (throughput is bounded by the Python relay, not the crypto). Live
3-client run: ~1 ms ping, 0.5–1 Gbps per client concurrently. Absolute handshake times
on this desktop vary with load — the **ratio** between modes is the stable result, so
the figures above pool several repetitions and `docs/BENCHMARKS.md` states the spread.
Methodology, raw CSVs and plots: [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md).

## Project structure

```
VPN/
├── client/       vpn_client.py, net_config.py (route/DNS), config*.yaml
├── server/       vpn_server.py, session_manager.py (slots/ports/limiter), net_config.py (NAT/dnsmasq)
├── common/       framing, TUN utils, OpenSSL wrapper, IPC + status, ticket cache, test_*.py
├── cli/          vpnctl.py
├── dashboard/    dashboard_server.py (FastAPI/WS, token auth), index.html, test_dashboard.py
├── pki/          gen_classical_ca.sh, gen_pqc_ca.sh, OpenSSL configs (certs are git-ignored)
├── scripts/      install_custom_openssl.sh, setup_routing.sh (netns), demo.sh, clean_build_check.sh, deploy_vps.sh
├── deploy/       pqvpn-server.service (systemd)
├── benchmark/    bench scripts (loopback + real-WAN), raw results/*.csv (tracked), plots
├── docs/         architecture, BENCHMARKS, DEPLOYMENT, setup, ROADMAP
└── Dockerfile    clean-build proof from ubuntu:24.04
```

## Limitations (honest list)

- **Python prototype**: throughput tops out around 0.5–1.3 Gbps per client because of
  the Python relay and pipe copies, not the crypto.
- **Session resumption is per-server-process**: each client gets a fresh `s_server`,
  which regenerates ticket keys, so tickets resume within a benchmark but not across
  daemon restarts. Status reports `resumed` only on hard evidence.
- **No mid-stream rekey** (`s_server` cannot renegotiate); reconnect re-handshakes.
- **Welcome port** is unauthenticated by design; cost is bounded (10 s reap + rate
  limit) but a distributed flood could still fill 3 slots.
- **Revocation is a file** (`crl.pem`, read per new session) — no OCSP / CRL
  distribution points.
- **Tested on one machine** (netns) and in Docker; the real-internet VPS run is the
  remaining roadmap item — [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) and
  `scripts/deploy_vps.sh` are ready for it.

## Prerequisites

Ubuntu 24.04 (or 22.04), Python 3.12, root for TUN/netns, `build-essential cmake
ninja-build libssl-dev`, `iperf3` for throughput benchmarks, Docker optional.

## References

- [liboqs](https://github.com/open-quantum-safe/liboqs) ·
  [oqs-provider](https://github.com/open-quantum-safe/oqs-provider)
- [NIST FIPS 203 (ML-KEM)](https://csrc.nist.gov/pubs/fips/203/final) ·
  [NIST FIPS 204 (ML-DSA)](https://csrc.nist.gov/pubs/fips/204/final)
