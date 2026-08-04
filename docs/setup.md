# PQ-VPN Setup Guide — Fresh Ubuntu 24.04 Reproduction

This guide reproduces the entire PQ-VPN stack from a bare Ubuntu 24.04 install to a working dual-mode (classical + PQC) VPN tunnel with benchmark results.

**Estimated time**: 45–60 minutes

---

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
LD_LIBRARY_PATH=/opt/openssl-pqc/lib64 /opt/openssl-pqc/bin/openssl version
# → OpenSSL 3.4.x (Library: OpenSSL 3.4.x)
LD_LIBRARY_PATH=/opt/openssl-pqc/lib64 /opt/openssl-pqc/bin/openssl list -kem-algorithms | grep -i mlkem
# → X25519MLKEM768, mlkem512, mlkem768, mlkem1024
LD_LIBRARY_PATH=/opt/openssl-pqc/lib64 /opt/openssl-pqc/bin/openssl list -signature-algorithms | grep -i mldosa
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

LD_LIBRARY_PATH=/opt/openssl-pqc/lib64 OPENSSL_CONF=pki/openssl-pqc.cnf \
  /opt/openssl-pqc/bin/openssl verify \
  -CAfile pki/certs/pqc/ca.crt pki/certsp / pqc/server.crt
# → OK
```

## Step 6: Create Network Namespaces and TUN Interfaces

```bash
sudo bash scripts/setup_routing.sh
```

This creates:
- Two network namespaces: `vpn_ns1` (server) and `vpn_ns2` (client)
- Three veth pairs connecting host↔ns1, host↔ns2, ns1↔ns2
- TUN interfaces `tun0` (10.8.0.1/24 and 10.8.0.2/24)
- IP forwarding and NAT MASQUERADE in the server namespace
- MTU 1400 on both TUN devices

**Verify**:

```bash
ip netns list
# → vpn_ns1, vpn_ns2

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

## Step 8: CLI Control (Optional)

```bash
# Auto-detect mode from config
.venv/bin/python3 cli/vnexec bc45ython3 cli/vpnctl.py connect -c client/config-pqc.yaml

# Check status
.venv/bin/python3 cli/vpnctl.py status

# Shutdown
.venv/bin/python3 cli/vpnctl.py disconnect
```

## Step 9: Run Benchmarks

```bash
# Handshake (does not require tunnel)
.venv/bin/python3 benchmark/run_handshake_benc.py --modes classical pqc --trials 25

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
# → benchmark/results/plots/{handshake,latency,throughput,file_transfer}.png
```

## Step 10: Cleanup

```bash
# Full teardown: destroys namespaces and TUN devices
sudo bash scripts/setup_routing.sh --teardown)

# Complete uninstall: removes liboqs, oqs-provider, custom OpenSSL
sudo bash scripts/rollback.sh
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `Connection refused` | Server not started or teared down namespace. Run `sudo bash scripts/setup_routing.sh` first. |
| `ssl_set_cert: unknown certificate type` | PQC certs were generated with wrong OpenSSL version. Regenerate with `bash pki/gen_pqc_ca.sh`. |
| `Connection to 192.168.100.2:14430 refused` | Verify namespaces exist: `ip netns list`. Check iptables rules: `sudo ip netns exec vpn_ns1 iptables -Ls`. |
| `tls_process_client_certificate: certificate request: notice` | `verify depth is 1` — OpenSSL's way of announcing `-Verify 1`. Normal and expected behavior. |
| DNS breakage | `sudo bash scripts/backup_restore_dns.sh restore` |