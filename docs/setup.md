# PQ-VPN Setup Guide — Fresh Ubuntu 24.04 Reproduction

This guide reproduces the entire PQ-VPN stack from a bare Ubuntu 24.04 install to a working dual-mode (classical + PQC) VPN tunnel with benchmark results.

**Estimated time**: 45–60 minutes

---

## Step 0 (alternative): Reproduce the whole build in Docker

If you just want proof that everything below works from a bare Ubuntu 24.04:

```bash
bash scripts/clean_build_check.sh          # build image (~10 min first time) + run all suites inside it
bash scripts/clean_build_check.sh --live   # additionally run the netns demo in a privileged container
```

Measured 2026-09-16 on a laptop, cold cache: apt 231 s, OpenSSL 3.4.7 + liboqs 0.15.0
+ oqs-provider 0.11.0 build 190 s, runtime deps + venv + PKI ~130 s. Inside the
image: 207/207 unit checks, a real handshake bench (classical ~17 ms, PQC ~79 ms
median under Docker — slower than the host figures in `BENCHMARKS.md`, which is
expected), and with `--live` a 2-client PQC tunnel over netns. The clean build surfaced seven portability bugs on this machine
that the laptop had been masking (see the `feat(build)` commit message).

The `Dockerfile` runs the same `scripts/install_custom_openssl.sh` a VPS would,
then gates on `openssl list` showing `mlkem768` and `mldsa65` via oqsprovider,
generates both PKIs, and `CMD` runs `scripts/demo.sh --dry-run --mode pqc`. The
script falls back to `docker build --network=host` when bridge-mode containers
have no egress (typical with ufw's default FORWARD policy).

The same check runs in GitHub Actions on every push (`.github/workflows/clean-build.yml`);
the README badge reflects the latest result. It is a *clean checkout*, which is
not the same thing as your working tree: files that are gitignored — notably
`client/config-vps.yaml` and `server/config-vps.yaml`, which hold a real public
IP — simply are not there. A test that named the `vps` profile therefore passed
for everyone who had deployed and failed in CI on every push. If CI disagrees
with your machine, reproduce it the way CI sees it:

```bash
git archive HEAD | tar -x -C /tmp/clean && ls /tmp/clean/client/*.yaml
```

## Step 1: System Dependencies

```bash
sudo apt-get update
sudo apt-get install -y build-essential cmake ninja-build libssl-dev zlib1g-dev iperf3 git curl
```

## Step 2: Clone Repository

```bash
git clone <repo-url> PQ-VPN
cd PQ-VPN
```

## Step 3: Python Environment

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Step 4: Build Custom OpenSSL 3.4.x + liboqs + oqs-provider

The system OpenSSL 3.0.13 cannot use provider-based signature algorithms (ML-DSA) in TLS 1.3 cert loading. We build OpenSSL >=3.4 to `/opt/openssl-pqc`:

```bash
sudo bash scripts/install_custom_openssl.sh
```

This performs 3 steps:
1. Builds OpenSSL 3.4.x → `/opt/openssl-pqc`
2. Builds liboqs against custom OpenSSL → `/opt/openssl-pqc`
3. Builds oqs-provider against custom OpenSSL → `/opt/openssl-pqc/lib64/ossl-modules/`

**Verify**:

```bash
LD_LIBRARY_PATH=/opt/openssl-pqc/lib64:/opt/openssl-pqc/lib /opt/openssl-pqc/bin/openssl version
# → OpenSSL 3.4.x (Library: OpenSSL 3.4.x)
LD_LIBRARY_PATH=/opt/openssl-pqc/lib64:/opt/openssl-pqc/lib /opt/openssl-pqc/bin/openssl list -kem-algorithms | grep -i mlkem
# → X25519MLKEM768, mlkem512, mlkem768, mlkem1024
LD_LIBRARY_PATH=/opt/openssl-pqc/lib64:/opt/openssl-pqc/lib /opt/openssl-pqc/bin/openssl list -signature-algorithms | grep -i mldsa
# → mldsa44, mldsa65, mldsa87
```

## Step 5: Generate PKI Certificates

Two parallel certificate chains:

```bash
# Classical: ECDSA P-256 (system OpenSSL)
bash pki/gen_classical_ca.sh    # → pki/certs/classical/

# PQC: ML-DSA-65 (custom OpenSSL)
bash pki/gen_pqc_ca.sh          # → pki/certs/pqc/
```

**Verify**:

```bash
openssl verify -CAfile pki/certs/classical/ca.crt pki/certs/classical/server.crt
# → OK

LD_LIBRARY_PATH=/opt/openssl-pqc/lib64:/opt/openssl-pqc/lib OPENSSL_CONF=pki/openssl-pqc.cnf \
  /opt/openssl-pqc/bin/openssl verify \
  -CAfile pki/certs/pqc/ca.crt pki/certs/pqc/server.crt
# → OK
```

## Step 6: Create Network Namespaces and TUN Interfaces

```bash
sudo bash scripts/setup_routing.sh
```

This creates:
- Four network namespaces: `vpn_ns1` (server) and `vpn_ns2`–`vpn_ns4` (up to 3 clients)
- One veth pair per namespace connecting it to the host, with forwarding rules between all pairs
- IP forwarding on host + namespaces, `rp_filter=0`
- DNS backup via `scripts/backup_restore_dns.sh`

TUN interfaces (`tun0`) are created by the daemons at runtime, not by this script.

**Verify**:

```bash
ip netns list
# → vpn_ns1, vpn_ns2, vpn_ns3, vpn_ns4

sudo ip netns exec vpn_ns2 ping -c 3 192.168.100.2
# → veth via host forwarding works
```

## Step 7: Run the Tunnel

### Classical Mode

```bash
# Terminal 1: Server
sudo ip netns exec vpn_ns1 .venv/bin/python3 server/vpn_server.py --config server/config.yaml

# Terminal 2: Client
sudo ip netns exec vpn_ns2 .venv/bin/python3 client/vpn_client.py --config client/config.yaml

# In any terminal:
sudo ip netns exec vpn_ns2 ping -c 5 10.8.0.1
```

### PQC Mode

```bash
# Terminal 1: Server
sudo ip netns exec vpn_ns1 .venv/bin/python3 server/vpn_server.py --config server/config-pqc.yaml

# Terminal 2: Client
sudo ip netns exec vpn_ns2 .venv/bin/python3 client/vpn_client.py --config client/config-pqc.yaml

# In any terminal:
sudo ip netns exec vpn_ns2 ping -c 5 10.8.0.1
```

**Expected output**: 5 packets transmitted, 5 received, 0% packet loss, rtt ~1-2ms steady state.

> One-shot alternative: `sudo bash scripts/demo.sh --mode classical --clients 2`
> does harness → daemons → ping → status → iperf3 → teardown automatically
> (`bash scripts/demo.sh --dry-run` for a rootless sanity pass).

> **Note (Phase 1):** With `nat: true` / `dnsmasq: true` in the server config, the
> server configures IP forwarding, NAT MASQUERADE, FORWARD rules, and a dnsmasq
> listener on tun0; the client installs `0.0.0.0/1` + `128.0.0.0/1` via tun0 (they
> beat any default route regardless of metric) plus DNS after
> the welcome handshake and restores both on shutdown. (The daemons still need
> root to open the TUN device, so `--dry-run` on the daemon binary is only useful
> under sudo; the rootless plumbing preview lives in the unit tests.)
>
> **Multi-client:** start extra clients with `client/config-ns3.yaml` /
> `config-ns4.yaml` (or the `config-pqc-ns*.yaml` variants) inside `vpn_ns3` /
> `vpn_ns4`. Each sets an `instance:` name (`ns3`, `ns4`) giving it its own IPC
> socket `/tmp/pqvpn-client-<instance>.sock`, so multiple clients don't collide.

## Step 8: Session Resumption + Status

The client caches TLS 1.3 session tickets per server under `~/.pqvpn/cache/`
(toggle with `resume_cache` in the client config). On reconnect the handshake is
resumed instead of full. Verify from status:

```bash
sudo ip netns exec vpn_ns2 .venv/bin/python3 client/vpn_client.py --config client/config.yaml &
sleep 5
.venv/bin/python3 cli/vpnctl.py status
# Client:
#   State:      connected
#   Negotiated: cipher=... · protocol=TLSv1.3 · sig_type=ECDSA · group_offered=X25519MLKEM768
```

`Negotiated` shows what the live handshake actually agreed to (`sig_type`
`mldsa65` in PQC mode proves post-quantum authentication; in PQC mode only
`X25519MLKEM768` is offered/enforced, so a completed handshake proves the PQ
key exchange). These fields reset on every reconnect attempt — no stale values.

## Step 9: CLI Control (Optional)

```bash
# Auto-detect mode from config (spawns daemon outside netns — for same-host
# or cross-host use; inside netns start daemons directly as in Step 7)
.venv/bin/python3 cli/vpnctl.py connect -c client/config-pqc.yaml

# Check status (server + every running client instance)
.venv/bin/python3 cli/vpnctl.py status

# Shutdown server + all client instances
.venv/bin/python3 cli/vpnctl.py disconnect
```

## Step 10: Web Dashboard (Optional)

```bash
pqvpn dashboard                     # http://127.0.0.1:8080, read-only, no root
pqvpn dashboard --control           # ...plus working Connect/Disconnect (asks for sudo once)
pqvpn dashboard --port N --token T  # override either
```

`.venv/bin/python3 dashboard/dashboard_server.py` is the same server if you
prefer to invoke it directly; `pqvpn dashboard` adds free-port probing and the
privilege handling.

The page opens on the tunnel's state, the controls and the negotiated suite,
then aggregate throughput over the last 60 s, a per-tunnel table with sparklines
and a Disconnect action, the client daemons on this host, and an event log. It
polls `/tmp/pqvpn-*.sock` every 0.5 s through a single background poller, so
state flips reach every open tab in under a second and extra tabs cost nothing.

Actions carry a token in an `X-PQVPN-Token` header. Bound to loopback, the
server embeds its own token in the page and there is nothing to copy. Bound to
anything else, the field must be filled in by hand — that is when the token is
the only guard. `--control` works only as root, because bringing a tunnel up
needs TUN, routes and DNS; without it the buttons render disabled and say so.

## Step 11: Run Benchmarks

```bash
# Handshake (does not require tunnel)
.venv/bin/python3 benchmark/run_handshake_bench.py --modes classical pqc --trials 25

# Full-vs-resumed handshake (does not require tunnel)
.venv/bin/python3 benchmark/run_handshake_bench.py --resume --trials 20

# Latency (requires tunnel running)
sudo ip netns exec vpn_ns2 bash benchmark/run_latency_bench.sh classical
sudo ip netns exec vpn_ns2 bash benchmark/run_latency_bench.sh pqc

# Throughput (requires iperf3 and tunnel)
sudo ip netns exec vpn_ns1 iperf3 -s -D && sleep 2
sudo ip netns exec vpn_ns2 bash benchmark/run_throughput_bench.sh classical
sudo ip netns exec vpn_ns2 bash benchmark/run_throughput_bench.sh pqc

# File transfer (100MB, requires tunnel)
sudo ip netns exec vpn_ns2 bash benchmark/run_file_transfer_bench.sh classical
sudo ip netns exec vpn_ns2 bash benchmark/run_file_transfer_bench.sh pqc

# Generate plots
.venv/bin/python3 benchmark/plot_results.py
# → benchmark/results/plots/{handshake,handshake_resume,latency,throughput,
#                            file_transfer,wan_handshake,handshake_bytes}.png
```

Results are interpreted in [`docs/BENCHMARKS.md`](BENCHMARKS.md).

## Step 12: Cleanup

```bash
# Full teardown: destroys namespaces and veth devices
sudo bash scripts/setup_routing.sh --teardown

# Complete uninstall: removes liboqs, oqs-provider, custom OpenSSL
sudo bash scripts/rollback.sh
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `Connection refused` | Server not started or teared down namespace. Run `sudo bash scripts/setup_routing.sh` first. |
| `ssl_set_cert: unknown certificate type` | PQC certs were generated with wrong OpenSSL version. Regenerate with `bash pki/gen_pqc_ca.sh`. |
| `Connection to 192.168.100.2:14430 refused` | Verify namespaces exist: `ip netns list`. Check iptables rules: `sudo ip netns exec vpn_ns1 iptables -S`. |
| `tls_process_client_certificate: certificate request: notice` | `verify depth is 1` — OpenSSL's way of announcing `-Verify 1`. Normal and expected behavior. |
| Multiple clients overwrite each other's status | Give each client config a unique `instance:` name (see Step 7 note); check `/tmp/pqvpn-client-*.sock`. |
| DNS breakage | `sudo bash scripts/backup_restore_dns.sh restore` |