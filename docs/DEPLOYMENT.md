# PQ-VPN Deployment Guide

Deploy PQ-VPN on a fresh Ubuntu 24.04 host. Two contexts:

1. **Local netns test harness** (`scripts/setup_routing.sh`) — for development
   and demos on a single machine.
2. **Real VPS** (Phase 7) — the capstone deployment.

Local indentation: everything here runs offline; the VPS steps purchase-and-bill.

---

## 0. Prerequisites

Ubuntu 24.04 (x86_64 recommended), Python 3.12, root access.

```bash
sudo apt-get update
sudo apt-get install -y build-essential cmake ninja-build libssl-dev \
    zlib1g-dev python3-venv python3-yaml iperf3 dnsmasq
```

## 1. Build the PQC OpenSSL stack (one time, ~15 min)

`scripts/install_custom_openssl.sh` builds and installs OpenSSL 3.4.7-dev +
liboqs + oqs-provider into `/opt/openssl-pqc`. The system OpenSSL is untouched.

```bash
cd /home/<user>/VPN
sudo bash scripts/install_custom_openssl.sh
```

**Verify the provider works** before going further:

```bash
export OPENSSL_CONF=$PWD/pki/openssl-pqc-runtime.cnf
/opt/openssl-pqc/bin/openssl list -kem-algorithms    | grep -i mlkem
/opt/openssl-pqc/bin/openssl list -signature-algorithms | grep -i mldsa
```

Expect `ML-KEM-768` / `X25519MLKEM768` and `ML-DSA-65` to appear.

## 2. Python environment

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

Dependencies: `pyyaml`, `click`, plus dashboard deps `fastapi`, `uvicorn[standard]`,
`websockets`; benchmark/plot uses `matplotlib`, `numpy`.

## 3. Generate the PKI (both modes, 10-year certs)

`pki/certs/` is git-ignored. Generate per host:

```bash
bash pki/gen_classical_ca.sh     # ECDSA P-256 chain (CA → server, CA → client)
bash pki/gen_pqc_ca.sh           # ML-DSA-65 chain (requires step 1)
```

Certs land in `pki/certs/classical/` and `pki/certs/pqc/`. Copy the tree to any
new host (server + client both need server.crt/key and client.crt/key).

## 4. Local netns harness (dev / demo)

`scripts/setup_routing.sh` creates `vpn_ns1` (server) plus `vpn_ns2..ns4`
(3 clients), each with its own veth pair, forwarding rules, and (for the server)
NAT/dnsmasq plumbing.

```bash
sudo bash scripts/setup_routing.sh          # setup
sudo bash scripts/setup_routing.sh --teardown  # cleanup
```

Verify reachability before starting the tunnel:
`sudo ip netns exec vpn_ns2 ping -c2 192.168.100.2`.

## 5. Start the VPN

```bash
# Classical
sudo ip netns exec vpn_ns1 .venv/bin/python3 server/vpn_server.py --config server/config.yaml &
sudo ip netns exec vpn_ns2 .venv/bin/python3 client/vpn_client.py --config client/config.yaml &

# PQC (separate terminals / separate namespaces)
sudo ip netns exec vpn_ns1 .venv/bin/python3 server/vpn_server.py --config server/config-pqc.yaml &
sudo ip netns exec vpn_ns2 .venv/bin/python3 client/vpn_client.py --config client/config-pqc.yaml &
```

Verify:

```bash
sudo ip netns exec vpn_ns2 ping -c5 10.8.0.1          # tunnel up
.venv/bin/python3 cli/vpnctl.py status                # session state, resumed flag
.venv/bin/python3 dashboard/dashboard_server.py &     # optional dashboard UI (http://127.0.0.1:8080)
# It prints a one-time token; paste it into the page's "token" box to enable
# kick/rekey. Set PQVPN_DASH_TOKEN or --token for a stable one. Keep the
# dashboard on 127.0.0.1 unless you have set a token — status is readable by
# anyone who can reach the port.
```

## 6. VPS deployment (Phase 7)

### 6.1 Create the VM

- x86_64 Ubuntu 24.04, ≥2 vCPU / 1 GB RAM (build needs a few GB disk).
- Own the public IP (this is the TLS endpoint: `server_host` in client configs).
- Security group: **allow inbound TCP 14430** (welcome) and the data port range
  **`data_port_base` … `data_port_base + max_clients - 1`** (14431–14433 with the
  defaults) from your remote client IPs only. No other inbound ports. Ports are
  bound to client slots, so reconnects always land back inside this range.

### 6.2 Build (same as step 1–2) + PKI

Copy the repo, run steps 1–3 on the VPS (or copy `/opt/openssl-pqc` build to
avoid a 15-min recompile — copying the whole tree works since everything is
statically-ish linked under one prefix).

### 6.3 Server config

`server/config-vps.yaml` (not committed) — the `outbound_iface` is the VPS's
primary NIC (e.g. `eth0`), NOT a veth:

```yaml
mode: pqc                    # or classical
host: 0.0.0.0
port: 14430
tun_name: tun0
tun_addr: 10.8.0.1/24
tun_net: 10.8.0.0/24
gateway: 10.8.0.1
dns: 10.8.0.1
tun_mtu: 1400
cert_dir: pki/certs/pqc
groups: X25519MLKEM768
nat: true
outbound_iface: eth0        # VPS primary NIC
persist_sysctl: true        # survive reboots
dnsmasq: true
dns_upstream: 1.1.1.1
keepalive_interval: 15
handshake_timeout: 10       # reap welcome slot claims that never finish mTLS
max_clients: 3              # => data ports 14431..14433 (data_port_base + slot)
```

### 6.4 ufw + security group

```bash
# welcome port + one data port per client slot (data_port_base .. +max_clients-1)
sudo ufw allow from <YOUR_CLIENT_IP> to any port 14430,14431,14432,14433 proto tcp
sudo ufw enable
sudo ufw status verbose
```

(The VPS cloud firewall should match the same allow-list; the OS ufw is defense
in depth.)

### 6.5 Rollback / rebuild

`scripts/rollback.sh` reverses the OpenSSL build (removes `/opt/openssl-pqc`).
`install_custom_openssl.sh` is idempotent-safe to re-run for a fresh rebuild.

---

## Troubleshooting checklist

| Symptom | Check |
|---------|-------|
| Handshake fails / cert verify error | `pki/certs/<mode>/` present on both ends; matching CA |
| PQC `unknown group` | `OPENSSL_CONF=pki/openssl-pqc-runtime.cnf` and `/opt/openssl-pqc/bin/openssl` in use (PATH/LD_LIBRARY_PATH) |
| Can't reach server IP | PFX: welcome port + data port range open in firewall **and** ufw |
| Client shows `reconnecting` forever | `backoff_min`/`backoff_max` window; server actually listening? `ss -ltn` |
| TUN not configured | run `--dry-run` on `server/net_config.py` / `client/net_config.py` to print commands without touching the system |
| DNS not resolving | `dnsmasq` running? `sudo ip netns exec vpn_ns1 pgrep dnsmasq` |