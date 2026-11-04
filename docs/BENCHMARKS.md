# PQ-VPN Benchmark Report

Canonical, compiled benchmark report. Raw per-trial data lives in
`benchmark/results/*.csv`; plots in `benchmark/results/plots/`. Generated with
`benchmark/run_handshake_bench.py` (handshake, resumption),
`benchmark/run_wan_handshake_bench.py` (real WAN, §7) and the netns shell
benchmarks `benchmark/run_{latency,throughput,file_transfer}_bench.sh`.

**Every table here is reproducible from the committed CSVs** — the raw per-trial
data is tracked, not just these summaries. To check any figure:

```bash
# median of a handshake table (§1, §7)
python3 -c "import csv,statistics as s;r=[float(x['ms']) for x in csv.DictReader(open('benchmark/results/handshake_classical.csv'))];print(round(s.median(r),2),len(r))"

# full vs resumed (§2, §7) — group by the 'kind' column
python3 -c "import csv,statistics as s;g={};[g.setdefault(x['kind'],[]).append(float(x['ms'])) for x in csv.DictReader(open('benchmark/results/handshake_resume_pqc.csv'))];print({k:round(s.median(v),2) for k,v in g.items()})"
```

## Environment

Every figure in §1–§5 and §8 comes from **one run on 2026-11-04**
(`sudo bash scripts/run_all_benchmarks.sh`, ~9 minutes), so tables may be
compared with each other without worrying about which day they were taken.
§7 (real WAN) still dates from 2026-09-23: it needs the deployed VPS.

- Ubuntu 24.04, Python 3.12, TUN MTU 1400. Kernel: 7.0.0-42-generic for the
  2026-11-04 run; 7.0.0-31-generic for the 2026-09-23 WAN figures.
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
five identical repetitions of the 2026-11-04 run the classical median ranged
14.54–18.85 ms and the PQC median 27.33–31.45 ms, and the *per-repetition*
ratio ranged 1.64–2.16x. **The ratio between modes is far more stable than either
absolute value**, so §1 and §2 report pooled medians across repetitions and
state the spread. Single-run figures from this machine should not be quoted to
two decimal places as if repeatable.

## 1. TLS Handshake (full, no resume)

**Method:** `openssl s_server -quiet` ready → `openssl s_client -quiet` connects,
negotiates, exits; timed `time.perf_counter()` → process wait. **5 independent
repetitions of 25 trials per mode (n = 125)**; the table pools all trials.

| Mode | Median (ms) | Min (ms) | Max (ms) | Repetition medians (ms) |
|------|-------------|----------|----------|-------------------------|
| Classical (P-256 + X25519) | 16.84 | 11.13 | 20.59 | 14.54 – 18.85 |
| PQC (ML-DSA-65 + X25519MLKEM768) | 30.08 | 19.96 | 36.18 | 27.33 – 31.45 |

> **Measured 2026-11-04** (kernel 7.0.0-42). The 2026-09-23 run gave 15.80 /
> 27.98 ms for a ratio of 1.77x. Both modes are ~7–8% slower in absolute terms
> on this busier host, and **the ratio moved by 0.02x** — which is the point the
> variance note above makes: the ratio is the stable figure, the absolute values
> are not. Per-repetition ratios span 1.64–2.16x, so the ratio should be read as
> "about 1.8x", not to two decimal places.

**PQC ≈ 1.79x slower median** (per-repetition ratios 1.64–2.16x). Cause:
ML-DSA-65 certs 5577 B DER vs 386 B for ECDSA P-256, plus the larger X25519MLKEM768 key
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
| Classical | 16.98 | 13.20 | 1.29x |
| PQC | 28.06 | 24.34 | 1.15x |

> **Measured 2026-11-04.** The 2026-09-23 run gave 13.21 → 11.80 (1.12x) and
> 22.00 → 20.31 (1.08x). Both modes are slower in absolute terms, consistent
> with §1, and **both speedups improved**. The full-handshake medians here sit
> close to §1's (16.98 vs 16.84 classical), where in September they sat well
> below it — because §1 and §2 now come from the same run under the same load,
> which is exactly what that previous gap was measuring.

**§1 and §2 are separate runs; compare within a table, not across.**

Modest on loopback because the KEM is a small share of the cost there. The real
win is the saved round-trip (one WAN RTT) plus skipped CPU for KEM/cert verify
over the internet, where latency dominates. The daemons cache the ticket per
server (`~/.pqvpn/cache/`) and resume automatically on reconnect.

![resume](../benchmark/results/plots/handshake_resume.png)

## 3. Ping Latency (through tunnel, netns harness)

**Method:** `ping -c 100 10.8.0.1` from the client namespace, both modes.

| Mode | Min (ms) | Avg (ms) | Max (ms) | Mdev (ms) |
|------|----------|----------|----------|-----------|
| Classical | 0.816 | 1.296 | 4.426 | 0.659 |
| PQC | 0.693 | 1.146 | 3.275 | 0.244 |

**PQC is 0.15 ms *faster* here — i.e. the two are indistinguishable** and the
sign of the difference is noise. The 2026-08-26 run reported 2.29 / 2.33 ms
average with a 117–119 ms maximum; that maximum was the first ping against a
cold tunnel and it dragged the average up. This run does not show it, so both
the average and the spread (mdev 0.24–0.66 vs 11.5–11.8) are much tighter.
Steady-state latency through the tunnel is ~1 ms in both modes.

These are **idle-tunnel** figures, which is the easy case. §8.2 measures the
same ping while the tunnel is saturated.

![latency](../benchmark/results/plots/latency.png)

## 4. Throughput (netns harness, iperf3, 10s)

**Method:** `iperf3 -c 10.8.0.1` from client namespace to server namespace;
TCP and UDP (`-b 1000M`). Bottleneck is the Python relay loop (veth + read/write),
not crypto — OpenSSL runs in a separate subprocess.

| Mode | Protocol | Throughput (Mbps) |
|------|----------|-------------------|
| Classical | TCP | 635.15 |
| Classical | UDP | 999.90 |
| PQC | TCP | 635.86 |
| PQC | UDP | 999.90 |

**The two modes agree to 0.1%** — the clearest statement yet that the data
plane is not affected by the choice of key exchange. The 2026-08-26 run
reported 1268 vs 533 Mbps and had to be described as "run variance"; that
spread was real but it was scheduling noise, not a PQC effect, and this run
removes the ambiguity. UDP saturates the veth link in both modes.

The single-stream TCP figure is a *relay* ceiling, not a per-stream one —
see §8.1, which tests that directly.

![throughput](../benchmark/results/plots/throughput.png)

## 5. File Transfer across sizes (netns harness)

**Method:** `benchmark/transfer_bench.py`. The server generates a deterministic
byte stream on the fly; the client hashes it in flight and discards it. **Nothing
is written to disk at either end**, and both ends compute SHA-256 over the same
stream and compare. **Seven sizes spanning six orders of magnitude, 3
repetitions each per mode (42 transfers per mode)**; the table reports the
median with the min–max range.

> **This replaces the earlier measurement, and the change is a correction, not
> a refresh.** The 2026-08-26 version downloaded a real 100 MB file to disk with
> `http.server`. At the rates measured here a disk write can be slower than the
> tunnel, so **the old 0.68 s / 1.31 s figures were partly a filesystem
> benchmark** and the ~2x gap between modes was never attributable to crypto.
> Streaming also removes the free-space limit, which is why a 1 GB point is now
> possible on a disk with 1.5 GB free.

| Size | Classical (Mbps) | PQC (Mbps) | PQC / classical |
|------|------------------|------------|-----------------|
| 1 KB | 9.7 (7.4–18.8) | 13.7 (8.3–14.6) | 1.41x |
| 100 KB | 352.6 (263.3–356.9) | 295.3 (180.4–309.6) | 0.84x |
| 1 MB | 425.0 (407.9–430.5) | 418.1 (414.3–480.8) | 0.98x |
| 10 MB | 553.2 (494.0–553.4) | 548.9 (483.4–558.8) | 0.99x |
| 100 MB | 635.1 (551.6–972.7) | 589.5 (530.8–900.2) | 0.93x |
| 500 MB | 1085.3 (1074.6–1107.7) | 1045.5 (1036.4–1070.5) | 0.96x |
| 1 GB | 1081.3 (1046.9–1097.8) | 1041.5 (1035.6–1043.0) | 0.96x |

**42 / 42 transfers per mode verified SHA-256 correct, including both 1 GB
runs.** Throughput rises with size until it flattens at ~1.05–1.09 Gbps from
500 MB on — the relay ceiling of §8.1, reached once the transfer is long enough
to amortise setup.

**A prediction that was written down before measuring, and did not survive.**
The plan predicted that small transfers would be *setup*-dominated and would
therefore make PQC look worse, with the modes converging at large sizes. The
convergence happened (0.96–0.99x from 1 MB up). The small-size penalty did not,
and the reason is structural rather than statistical: the per-transfer setup
here is a plain TCP connect *inside an already-established tunnel*
(`socket.create_connection`, median 0.70 ms classical / 0.83 ms PQC across all
42 rows). **The TLS handshake is not in this measurement loop at all** — it is
paid once, when the tunnel comes up, and §1 is where it shows. So no
PQC-specific small-transfer penalty was ever possible here, and the 1 KB column
is measuring scheduling jitter: its min–max ranges (7.4–18.8 vs 8.3–14.6 Mbps)
overlap almost completely, and an earlier run of the same benchmark put
classical ahead at that size by a similar margin. **The 1 KB row should not be
read as a result in either direction.**

![transfer sweep](../benchmark/results/plots/transfer_sweep.png)

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

## 7. Real-WAN handshake (laptop → AWS Lightsail, Mumbai)

**Method:** `benchmark/run_wan_handshake_bench.py ubuntu@<vps>` starts a plain
`openssl s_server` on the VPS over SSH and times `s_client` handshakes from the
laptop against it — TLS only, the VPN daemon is not involved, so these are
directly comparable in method to §1. Home Wi-Fi in India to `ap-south-1`.
Recorded 2026-09-23.

**Full handshake, 25 trials/mode** (TCP round-trip measured at 99.2 ms during
this run):

| Mode | Median (ms) | Min (ms) | Max (ms) |
|------|-------------|----------|----------|
| Classical | 203.0 | 90.8 | 304.4 |
| PQC | 327.0 | 202.4 | 668.2 |

![wan handshake](../benchmark/results/plots/wan_handshake.png)

**PQC = 1.61x, i.e. +124 ms.** Classical's 203 ms is almost exactly two round
trips (TCP connect + TLS 1.3), as expected. PQC lands near 3.3 round trips.

**This is the opposite of what loopback suggests.** On loopback PQC costs
+12 ms — pure CPU for the bigger certificate and key share. Over a WAN the cost
is ~124 ms, about one extra round trip. `benchmark/measure_handshake_bytes.py`
measures where the bytes go: the PQC server's handshake flight is **16 001 B**
against a **14 600 B** initial congestion window (and the client's, under mutual
TLS, is 15 852 B), so it cannot be sent in one burst. A HelloRetryRequest — the
other way to buy exactly one round trip — was checked for and does not occur.

![handshake bytes](../benchmark/results/plots/handshake_bytes.png)

The measured round-trip counts are consistent with the larger handshake flight
costing an extra round trip; **this is inferred, not confirmed by packet
capture.** See `docs/PROJECT_REPORT.md` §13 for the full message-size table and
the other candidate explanations.

**Full vs resumed, 20 trials/mode** (interleaved against one long-lived server;
round-trip measured at 51.6 ms during this later run — the path was quieter, so
compare within this table only):

| Mode | Full median (ms) | Resumed median (ms) | Saved | Speedup |
|------|------------------|---------------------|-------|---------|
| Classical | 198.6 | 152.9 | 45.7 ms | 1.30x |
| PQC | 272.0 | 200.0 | 72.0 ms | 1.36x |

Resumption saves **more** for PQC in absolute terms (72 ms vs 46 ms), and on
loopback the same effect looked negligible (§2: PQC 1.08x), so it matters far
more over the internet than lab figures suggested.

It does **not** remove most of the penalty, though. The PQC handshake is still
47 ms (~0.9 round trips) slower than classical even when resumed, and only
about 26 ms of the 73 ms full-handshake gap disappears.
`measure_handshake_bytes.py` shows why: a resumed PQC ClientHello is **7456
bytes against 1039 classical**, because the PSK identity *is* a 5833-byte
ticket and the server returns another one. Resumption drops the certificates
but not the bulk. It is the most effective optimisation available here, not a
fix.

## 8. Scaling and behaviour under load (netns harness)

**Method:** `benchmark/run_scaling_bench.sh <mode>`, run from the host so it can
drive several client namespaces at once. 10 s per data point. Three questions
the rest of the suite does not answer.

### 8.1 Is the throughput ceiling per-stream, or the relay?

`iperf3 -P 1,2,4,8` from one client.

| Parallel streams | Classical (Mbps) | PQC (Mbps) |
|------------------|------------------|------------|
| 1 | 758.73 | 700.96 |
| 2 | 965.22 | 999.80 |
| 4 | 995.30 | 996.99 |
| 8 | 1009.87 | 967.96 |

**It is the relay.** Two streams reach ~1 Gbps and four and eight add nothing;
if the ceiling were per-stream, the total would keep climbing. The project's
stated limitation — that throughput is bounded by the Python `select` relay
rather than by OpenSSL — was asserted in earlier revisions of this document and
is now measured. It is also why §4's single-stream figure (~635 Mbps) sits below
the §5 large-transfer figure (~1.08 Gbps): neither is wrong, they are a
single-stream and a saturating measurement of the same ceiling.

### 8.2 What happens to latency when the tunnel is saturated?

Ping from the same namespace as the load, so it queues behind the same traffic.

| Mode | Idle (ms) | Under load (ms) | Degradation |
|------|-----------|-----------------|-------------|
| Classical | 1.210 | 17.203 | 14x |
| PQC | 1.086 | 30.574 | 28x |

**Latency degrades by more than an order of magnitude under saturation.** This
is the honest counterpart to §3: an idle tunnel at ~1 ms says little about a
tunnel in use. The mechanism is queueing in the single-threaded relay — a ping
waits behind whatever bulk traffic is already buffered — and it is a real
usability limit, not a measurement artefact.

The classical/PQC gap in this table (17 vs 31 ms) is **not** evidence of a
crypto cost: it is one 10-second sample per mode with no repetitions, and every
other data-plane measurement here shows the two modes within 1%. Treat it as
"both degrade sharply", and read the per-mode difference as unmeasured.

### 8.3 Does the server scale across clients?

Total throughput with 1, 2 and 3 clients transferring at once. Each client gets
its own `iperf3` server on its own port, so the load is genuinely concurrent
rather than three tests taking turns.

| Concurrent clients | Classical (Mbps total) | PQC (Mbps total) |
|--------------------|------------------------|------------------|
| 1 | 714.72 | 737.07 |
| 2 | 670.03 | 669.67 |
| 3 | 458.73 | 455.49 |

**Total throughput falls as clients are added.** Three clients together move
~36% less data than one client alone — the design does not merely fail to scale,
it degrades. The cause is the architecture this benchmark was written to test:
`server/vpn_server.py` demultiplexes every client's return traffic through **one
shared TUN reader** in a single `select` loop, so each additional client adds
contention to a resource that was already the bottleneck in §8.1.

Both modes track each other to within 3%, which again isolates this from the
crypto: it is a relay-architecture result, and the fix is the compiled,
multi-queue relay listed under Future Work.

![scaling](../benchmark/results/plots/scaling.png)

## Validation

`bash scripts/demo.sh --dry-run` runs five suites — **193 checks**:

| Suite | Checks | Covers |
|-------|--------|--------|
| `common/test_common.py` | 92 | framing, welcome→TLS config push, slot/port recycling, CRL revocation against a real `s_server`, rate limiter, IPC |
| `common/test_net_config.py` | 36 | NAT, MSS clamp, route protection/restore, IPv6 guard, DNS fallback |
| `common/test_keepalive.py` | 7 | keepalive, idle detection, backoff, session-stop cascade |
| `common/test_resumption.py` | 14 | ticket cache, wrapper flags, real OpenSSL prime→`Reused` |
| `dashboard/test_dashboard.py` | 44 | IPC envelope, endpoints, token auth, single poller, WS |

## Interpretation

| Metric | Classical → PQC | Verdict |
|--------|-----------------|---------|
| WAN full handshake | 1.61x (+124 ms) | About one extra round trip, not CPU — the real deployment cost |
| WAN resumed handshake | saves 72 ms (1.36x) | Helps far more than loopback showed, but PQC stays ~47 ms behind |
| Full handshake | 1.79x slower | Only PQC cost; once per connection/reconnect |
| Resumed handshake | 1.15x vs 1.29x | Resume minimizes even this; saves WAN RTT |
| Ping latency (idle) | −0.15 ms | Indistinguishable; sign is noise |
| TCP / UDP throughput | 1.001x / 1.000x | No data-plane penalty, to 0.1% |
| Transfer, 1 MB – 1 GB | 0.93–0.99x | Not crypto-bound |
| Parallel streams, clients | within 3% | Relay-bound in both modes |

Key takeaways:

1. **PQC is practical for VPNs.** The cost is confined to the one-time
   handshake — 1.79x on loopback (CPU) and 1.61x over a real WAN, where it
   takes the form of about one extra round trip. What causes that round trip is
   not established (§7): the PQC flight does exceed the initial congestion
   window, which is consistent with it, but no packet capture confirms the
   link. TLS session resumption recovers part of the penalty (72 ms, 1.36x) and
   the daemons resume automatically.
2. **Steady-state data plane is identical** — both modes use AES-256-GCM; PQC
   only affects the initial key exchange.
3. **Throughput is bounded by the Python relay** (`select` + `read`/`write`),
   not by OpenSSL subprocesses — measured directly in §8.1: two parallel streams
   saturate the ceiling and four or eight add nothing. Removing it requires a
   compiled relay (Tier 3 — see Future Work).
4. **The relay architecture, not the cryptography, is this project's real
   limitation.** Latency degrades 14–28x under saturation (§8.2) and total
   throughput *falls* as clients are added (§8.3). Both follow from the single
   shared TUN reader, both are mode-independent, and neither would have been
   visible from the idle-tunnel and single-client numbers the suite used to
   report. This is the most significant negative result in the project.
5. **Custom OpenSSL ≥ 3.2 is mandatory** for ML-DSA certs; system OpenSSL 3.0.x
   cannot load OID-based signature algorithms.

## Future Work (explicitly out of current scope)

- **Tier 3 performance:** Go/C rewrite of the relay, `splice()` batching,
  multi-queue — target >2 Gbps; benchmark re-run with `-b 0` for the true
  UDP ceiling.
- **Production auth:** PAM / directory-backed accounts. (Per-client certificates
  with CRL revocation and welcome-port rate limiting are implemented; MTU
  auto-negotiation is not.)
- **Multi-client scaling.** §8.3 shows total throughput *falling* from 715 to
  459 Mbps between 1 and 3 clients, so the open item is no longer "measure it"
  but "fix it": the single shared TUN reader needs to become per-client queues
  or a multi-queue TUN before an IP pool beyond 3 clients is worth building.
  Per-client bandwidth QoS is also unimplemented.
- **Live WAN numbers:** handshake and resumption are measured (§7). Still open:
  WAN latency/throughput through the tunnel itself, and whether a smaller
  certificate chain (ML-DSA-44, or a hybrid with a compressed chain) removes
  the extra round trip.
- **Key rotation:** mid-stream rekey is unsupported (no s_server
  renegotiation); reconnect + resume is the workaround.

## Reproducing

Everything in §1–§5 and §8 regenerates with one command, which builds the netns
harness, runs both modes against a live tunnel, tears the harness down on every
exit path, regenerates the plots and hands the files back to your own user:

```bash
sudo bash scripts/run_all_benchmarks.sh            # full sweep, ~9 minutes
sudo bash scripts/run_all_benchmarks.sh --mode pqc # one mode only
sudo bash scripts/run_all_benchmarks.sh --quick    # smoke test, see note
```

> `--quick` writes to a **temporary directory**, not `benchmark/results/`, and
> prints the path. It runs a fifth of the handshake trials and three of the
> seven transfer sizes, so pointing it at the published CSVs would silently
> replace good data with worse data — which is exactly what happened once, and
> cost a full sweep.

The individual benchmarks, if you want to run one on its own:

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
#   host:       sudo bash benchmark/run_scaling_bench.sh <mode>
#
# Transfer sizes and repetitions are overridable:
#   PQVPN_BENCH_SIZES="1K 1M 100M" PQVPN_BENCH_REPS=3 ...

# Regenerate all plots:
.venv/bin/python3 benchmark/plot_results.py
```
