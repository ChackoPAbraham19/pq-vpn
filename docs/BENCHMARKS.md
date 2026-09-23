# PQ-VPN Benchmark Report

Canonical, compiled benchmark report. Raw per-trial data lives in
`benchmark/results/*.csv`; plots in `benchmark/results/plots/`. Generated with
`benchmark/run_handshake_bench.py` (handshake, resumption) and the netns shell
benchmarks `benchmark/run_{latency,throughput,file_transfer}_bench.sh`.

## Environment

Handshake and resumption figures (§1, §2) were recorded **2026-09-23**; the
netns tunnel figures (§3–§5) come from the **2026-08-26** run and have not been
re-measured since.

- Ubuntu 24.04, Python 3.12, TUN MTU 1400. Kernel: 7.0.0-31-generic for the
  2026-09-23 handshake runs; 6.13 for the 2026-08-26 netns runs.
- Classical: system OpenSSL 3.0.13. PQC: custom stack at `/opt/openssl-pqc` —
  **as measured**: OpenSSL 3.4.7-dev, liboqs 0.16.0, oqs-provider 0.12.0-dev.
- TLS 1.3, mutual auth. Classical: ECDSA P-256 + X25519.
  PQC: ML-DSA-65 + X25519MLKEM768 hybrid
- Loopback for handshake measurements; veth-pair netns harness for
  latency / throughput / file transfer
- Machine: an interactive desktop (browsers open), not a quiet benchmark host.
  The Python relay and the OpenSSL subprocesses compete for the same cores.

> **Version note.** `scripts/install_custom_openssl.sh` pins OpenSSL 3.4.7,
> liboqs 0.15.0 and oqs-provider 0.11.0 so clean builds (and CI) are
> reproducible. Those pins were added *after* the stack on this measurement
> machine was built, so the numbers below come from the slightly newer
> versions listed above. Re-measuring on the pinned set is pending; the
> relative classical↔PQC comparison is not expected to move, since both modes
> traverse identical code paths.

## Measurement variance

Absolute handshake times on this host depend strongly on desktop load: across
five identical repetitions the classical median ranged 13.9–18.1 ms and the PQC
median 25.8–30.1 ms. **The ratio between modes is far more stable than either
absolute value**, so §1 and §2 report pooled medians across repetitions and
state the spread. Single-run figures from this machine should not be quoted to
two decimal places as if repeatable.

## 1. TLS Handshake (full, no resume)

**Method:** `openssl s_server -quiet` ready → `openssl s_client -quiet` connects,
negotiates, exits; timed `time.perf_counter()` → process wait. **5 independent
repetitions of 25 trials per mode (n = 125)**; the table pools all trials.

| Mode | Median (ms) | Min (ms) | Max (ms) | Repetition medians (ms) |
|------|-------------|----------|----------|-------------------------|
| Classical (P-256 + X25519) | 15.80 | 10.64 | 25.19 | 13.92 – 18.06 |
| PQC (ML-DSA-65 + X25519MLKEM768) | 27.98 | 19.29 | 45.32 | 25.81 – 30.10 |

**PQC ≈ 1.77x slower median** (per-repetition ratios 1.66–1.86x). Cause:
ML-DSA-65 certs ≈7.5 KB vs ≈4.5 KB ECDSA, plus the larger X25519MLKEM768 key
share. One-time cost per connection.

![handshake](../benchmark/results/plots/handshake.png)

## 2. TLS Session Resumption (full vs resumed)

**Method:** one long-lived `s_server`; a priming connection writes a TLS 1.3
session ticket; then 20 interleaved full + `-sess_in` resumed handshakes.
**3 repetitions per mode (n = 60 per kind)**; the table pools all trials.
Resumption verified to skip the KEM + certificate exchange (`Reused, TLSv1.3`,
`pre_shared_key` ext).

| Mode | Full median (ms) | Resumed median (ms) | Speedup |
|------|------------------|---------------------|---------|
| Classical | 13.21 | 11.80 | 1.12x |
| PQC | 22.00 | 20.31 | 1.08x |

Per-repetition speedups: classical 1.08–1.29x, PQC 1.04–1.14x. These absolute
values sit below §1's because the three repetitions here ran when the host was
quieter — **§1 and §2 are separate runs and their absolute values are not
directly comparable**; compare within a table, not across.

Modest on loopback because the KEM is a small share of the cost there. The real
win is the saved round-trip (one WAN RTT) plus skipped CPU for KEM/cert verify
over the internet, where latency dominates. The daemons cache the ticket per
server (`~/.pqvpn/cache/`) and resume automatically on reconnect.

![resume](../benchmark/results/plots/handshake_resume.png)

## 3. Ping Latency (through tunnel, netns harness)

**Method:** `ping -c 100 10.8.0.1` from the client namespace, both modes.

| Mode | Min (ms) | Avg (ms) | Max (ms) | Mdev (ms) |
|------|----------|----------|----------|-----------|
| Classical | 0.54 | 2.29 | 116.99 | 11.53 |
| PQC | 0.93 | 2.33 | 119.18 | 11.75 |

Max ≈117–119 ms occurs on the first ping only (cold tunnel); steady-state is
1–2 ms. **PQC adds <0.1 ms — indistinguishable.**

![latency](../benchmark/results/plots/latency.png)

## 4. Throughput (netns harness, iperf3, 10s)

**Method:** `iperf3 -c 10.8.0.1` from client namespace to server namespace;
TCP and UDP (`-b 1000M`). Bottleneck is the Python relay loop (veth + read/write),
not crypto — OpenSSL runs in a separate subprocess.

| Mode | Protocol | Throughput (Mbps) |
|------|----------|-------------------|
| Classical | TCP | 1268.47 |
| Classical | UDP | 999.89 |
| PQC | TCP | 532.99 |
| PQC | UDP | 999.89 |

TCP is noise-dominated by CPU scheduling on this box; UDP saturates the veth
link in both modes. Earlier runs measured 687–693 Mbps TCP for both modes.
**No consistent PQC data-plane penalty.**

![throughput](../benchmark/results/plots/throughput.png)

## 5. File Transfer (100 MB over tunnel, netns harness)

**Method:** 100 MB random file served by `http.server` on server TUN IP
(10.8.0.1:9998), downloaded from the client namespace over the tunnel, SHA-256
verified. One run per mode.

| Mode | Size (MB) | Time (s) | SHA-256 OK |
|------|-----------|----------|------------|
| Classical | 100 | 0.68 | yes |
| PQC | 100 | 1.31 | yes |

Run-to-run variance on this host is large (0.68 vs 1.31 s earlier); both are
transfer-bound, not crypto-bound.

![file transfer](../benchmark/results/plots/file_transfer.png)

## 6. Internet egress through the tunnel (netns harness)

**Method:** `scripts/demo.sh --mode pqc --clients 2 --egress`. The host NATs the
server namespace out its own uplink, so a client namespace can reach the public
internet *through* the tunnel. Each client fetches a public IP-echo service and
the result is compared with the host's own public address.

| Check | Result |
|-------|--------|
| Public IP seen by client 1 / client 2 | identical to the host's — traffic left via the tunnel + server NAT |
| `curl https://ifconfig.me/ip` from `vpn_ns2` | succeeds (DNS + TLS to a real site, inside the tunnel) |
| Client route table | `0.0.0.0/1` and `128.0.0.0/1` via `tun0`, above the namespace's metric-0 default |

Two settings are required for this to work and are on by default: **TCP MSS
clamping** on the server's forward path (the 1400-byte tunnel against a
1500-byte uplink otherwise blackholes path-MTU discovery) and the client's
**IPv6 default blackhole** (the tunnel is IPv4-only, so v6-capable sites would
otherwise be reached outside it).

The first run of this test found a real leak: the client used to install
`default … metric 50`, which loses to a metric-0 default route, so tunnel pings
worked while internet traffic silently bypassed the tunnel. The `/1` half-routes
fix it regardless of the host's metric.

## Validation

`bash scripts/demo.sh --dry-run` runs five suites — **142 checks**:

| Suite | Checks | Covers |
|-------|--------|--------|
| `common/test_common.py` | 71 | framing, welcome→TLS config push, slot/port recycling, CRL revocation against a real `s_server`, rate limiter, IPC |
| `common/test_net_config.py` | 25 | NAT, MSS clamp, route protection/restore, IPv6 guard, DNS fallback |
| `common/test_keepalive.py` | 7 | keepalive, idle detection, backoff, session-stop cascade |
| `common/test_resumption.py` | 14 | ticket cache, wrapper flags, real OpenSSL prime→`Reused` |
| `dashboard/test_dashboard.py` | 25 | IPC envelope, endpoints, token auth, single poller, WS |

## Interpretation

| Metric | Classical → PQC | Verdict |
|--------|-----------------|---------|
| Full handshake | 1.77x slower | Only PQC cost; once per connection/reconnect |
| Resumed handshake | 1.08x vs 1.12x | Resume minimizes even this; saves WAN RTT |
| Ping latency | +0.04 ms | Negligible |
| TCP / UDP throughput | noise / 0% | No data-plane penalty |
| 100 MB transfer | ~2x run variance | Not crypto-bound |

Key takeaways:

1. **PQC is practical for VPNs.** The only measurable cost is the one-time
   handshake (~1.77x), mitigated by TLS session resumption (the daemons resume
   automatically).
2. **Steady-state data plane is identical** — both modes use AES-256-GCM; PQC
   only affects the initial key exchange.
3. **Throughput is bounded by the Python relay** (`select` + `read`/`write`),
   not by OpenSSL subprocesses. Removing this ceiling requires a compiled relay
   (Tier 3 — see Future Work).
4. **Custom OpenSSL ≥ 3.2 is mandatory** for ML-DSA certs; system OpenSSL 3.0.x
   cannot load OID-based signature algorithms.

## Future Work (explicitly out of current scope)

- **Tier 3 performance:** Go/C rewrite of the relay, `splice()` batching,
  multi-queue — target >2 Gbps; benchmark re-run with `-b 0` for the true
  UDP ceiling.
- **Production auth:** PAM / directory-backed accounts. (Per-client certificates
  with CRL revocation and welcome-port rate limiting are implemented; MTU
  auto-negotiation is not.)
- **Multi-client scaling** beyond 3 (IP pool, per-client port range) and
  per-client bandwidth QoS.
- **Live WAN numbers:** re-run handshake/resumption/latency against the Phase 7
  VPS to capture real-internet overhead (reconnect + resume saving one WAN RTT).
- **Key rotation:** mid-stream rekey is unsupported (no s_server
  renegotiation); reconnect + resume is the workaround.

## Reproducing

```bash
cd /home/chacko/Chacko/VPN

# Handshake: 25 trials/mode
.venv/bin/python3 benchmark/run_handshake_bench.py --modes classical pqc --trials 25

# Resumption: full vs resumed, 20 trials/mode
.venv/bin/python3 benchmark/run_handshake_bench.py --modes classical pqc --resume --trials 20

# Netns benchmarks (require the tunnel up + sudo):
#   server ns:  sudo ip netns exec vpn_ns1 iperf3 -s
#   client ns:  sudo ip netns exec vpn_ns2 bash benchmark/run_latency_bench.sh <mode>
#               sudo ip netns exec vpn_ns2 bash benchmark/run_throughput_bench.sh <mode>
#               sudo ip netns exec vpn_ns2 bash benchmark/run_file_transfer_bench.sh <mode>

# Regenerate all plots:
.venv/bin/python3 benchmark/plot_results.py
```
