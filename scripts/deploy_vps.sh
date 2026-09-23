#!/usr/bin/env bash
set -euo pipefail
# deploy_vps.sh — push the validated project to a VPS and start the server.
#
#   bash scripts/deploy_vps.sh user@203.0.113.5            # deploy
#   bash scripts/deploy_vps.sh --dry-run user@203.0.113.5  # print every command, touch nothing
#
# Prereqs on the laptop: server/config-vps.yaml exists (copy the .example),
# pki/certs/pqc/ generated (the server needs ca.crt + server.crt/key; client
# certs are synced too so `vpnctl`/tests work on the box). ssh key auth to a
# sudo-capable user on an Ubuntu 24.04 x86_64 VPS.
#
# Idempotent: re-running syncs code + config and restarts the service; the
# 15-minute OpenSSL build only happens if /opt/openssl-pqc is missing.

PDIR="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE_DIR="${PQVPN_REMOTE_DIR:-/opt/pqvpn}"
DRY=0
TARGET=""
for a in "$@"; do
    case "$a" in
        --dry-run) DRY=1 ;;
        -*) echo "unknown option: $a" >&2; exit 2 ;;
        *) TARGET="$a" ;;
    esac
done
[[ -n "$TARGET" ]] || { echo "usage: $0 [--dry-run] user@host" >&2; exit 2; }

run() { if [[ "$DRY" -eq 1 ]]; then echo "+ $*"; else "$@"; fi; }
remote() {
    if [[ "$DRY" -eq 1 ]]; then printf '+ ssh %s sudo bash -c %q\n' "$TARGET" "$1"
    else ssh "$TARGET" "sudo bash -c $(printf '%q' "$1")"; fi
}

echo "== preflight =="
[[ -f "$PDIR/server/config-vps.yaml" ]] || {
    echo "missing server/config-vps.yaml (cp server/config-vps.example.yaml and set outbound_iface)" >&2
    [[ "$DRY" -eq 1 ]] || exit 1; }
[[ -f "$PDIR/pki/certs/pqc/server.crt" ]] || {
    echo "missing pki/certs/pqc (run pki/gen_pqc_ca.sh; needs /opt/openssl-pqc)" >&2
    [[ "$DRY" -eq 1 ]] || exit 1; }

echo "== 1/5 sync project -> $TARGET:$REMOTE_DIR =="
remote "mkdir -p $REMOTE_DIR && chown \$SUDO_USER $REMOTE_DIR"
run rsync -az --delete \
    --exclude .venv --exclude .git --exclude graphify-out --exclude __pycache__ \
    --exclude 'benchmark/results/*.csv' --exclude '.~lock.*' \
    "$PDIR/" "$TARGET:$REMOTE_DIR/"

echo "== 2/5 PQC OpenSSL stack (skipped if present; ~35 min at BUILD_JOBS=1) =="
# BUILD_JOBS=1 unless the caller overrides: most cheap VPS instances are
# burstable, and a full-throttle compile burns their CPU credits to zero and
# throttles the box (SSH included) for an hour. Export BUILD_JOBS=0 to use all
# cores on a dedicated-CPU box.
JOBS="${BUILD_JOBS:-1}"
[ "$JOBS" = "0" ] && JOBS="$(ssh "$TARGET" nproc 2>/dev/null || echo 1)"
remote "test -x /opt/openssl-pqc/bin/openssl || BUILD_JOBS=${JOBS} bash $REMOTE_DIR/scripts/install_custom_openssl.sh"
remote "export PATH=/opt/openssl-pqc/bin:\$PATH LD_LIBRARY_PATH=/opt/openssl-pqc/lib64:/opt/openssl-pqc/lib; \
openssl list -kem-algorithms -provider oqsprovider -provider default | grep -qi mlkem768 && \
openssl list -signature-algorithms -provider oqsprovider -provider default | grep -qi mldsa65 && \
echo 'provider ok: ML-KEM-768 + ML-DSA-65'"

echo "== 3/5 python env + runtime deps =="
remote "apt-get install -y -qq python3-venv iproute2 iptables dnsmasq >/dev/null && \
cd $REMOTE_DIR && (test -x .venv/bin/python3 || python3 -m venv .venv) && \
.venv/bin/pip install -q -r requirements.txt"

echo "== 4/5 firewall: welcome + data ports =="
PORT="$(grep -E '^port:' "$PDIR/server/config-vps.yaml" 2>/dev/null | awk '{print $2}' || true)"
BASE="$(grep -E '^data_port_base:' "$PDIR/server/config-vps.yaml" 2>/dev/null | awk '{print $2}' || true)"
MAXC="$(grep -E '^max_clients:' "$PDIR/server/config-vps.yaml" 2>/dev/null | awk '{print $2}' || true)"
EGRESS="$(grep -E '^outbound_iface:' "$PDIR/server/config-vps.yaml" 2>/dev/null | awk '{print $2}' || true)"
TUN="$(grep -E '^tun_name:' "$PDIR/server/config-vps.yaml" 2>/dev/null | awk '{print $2}' || true)"
PORT="${PORT:-14430}"; BASE="${BASE:-14431}"; MAXC="${MAXC:-3}"; EGRESS="${EGRESS:-eth0}"; TUN="${TUN:-tun0}"
LAST=$((BASE + MAXC - 1))
# SSH FIRST, ALWAYS. `ufw --force enable` applies the default deny-incoming
# policy immediately; without an SSH rule already in place it locks the
# operator out of the box the moment it runs, and on a provider with no serial
# console (Lightsail) the only recovery is a snapshot rebuild. Learned the
# hard way on 2026-09-22.
#
# ufw's FORWARD policy is DROP. The daemon's own ACCEPT rules are appended after
# ufw's chains and do work, but a `ufw reload` flushes them; `ufw route allow`
# is the durable, explicit form and stays scoped to tunnel<->egress.
if [[ "$DRY" -eq 1 ]]; then
    SSH_PORT=22
else
    SSH_PORT="$(ssh -o ConnectTimeout=10 "$TARGET" "sudo sshd -T 2>/dev/null | awk '/^port /{print \$2; exit}'" 2>/dev/null || true)"
    SSH_PORT="${SSH_PORT:-22}"
fi
echo "   keeping SSH reachable on port ${SSH_PORT} before enabling ufw"
remote "command -v ufw >/dev/null && { \
ufw allow ${SSH_PORT}/tcp >/dev/null; \
ufw allow ${PORT}/tcp >/dev/null; ufw allow ${BASE}:${LAST}/tcp >/dev/null; \
ufw route allow in on ${TUN} out on ${EGRESS} >/dev/null; ufw route allow in on ${EGRESS} out on ${TUN} >/dev/null; \
ufw --force enable >/dev/null; \
ufw status | grep -qE '(^| )${SSH_PORT}/tcp' || { echo 'REFUSING to leave ufw enabled without an SSH rule' >&2; ufw --force disable; exit 1; }; \
ufw status | grep -E '${SSH_PORT}|${PORT}|${BASE}|${TUN}'; } || echo 'ufw not installed; rely on the cloud security group'"
# Prove SSH still works before moving on; if it does not, the next step would
# hang and the operator would be locked out with no signal about why.
if [[ "$DRY" -eq 0 ]]; then
    if ! ssh -o ConnectTimeout=10 -o BatchMode=yes "$TARGET" true 2>/dev/null; then
        echo "!! SSH no longer reachable after enabling ufw — aborting before further changes" >&2
        exit 1
    fi
    echo "   SSH still reachable after enabling ufw"
fi
echo "   (cloud security group must also allow TCP ${PORT} and ${BASE}-${LAST} from your client IPs)"

echo "== 5/5 systemd service =="
remote "cp $REMOTE_DIR/deploy/pqvpn-server.service /etc/systemd/system/ && \
systemctl daemon-reload && systemctl enable --now pqvpn-server && sleep 3 && \
systemctl --no-pager --lines=5 status pqvpn-server && \
$REMOTE_DIR/.venv/bin/python3 $REMOTE_DIR/cli/vpnctl.py status server"

echo
echo "Deployed. Next, on a client machine with pki/certs/pqc/{ca,client}.*:"
echo "  cp client/config-vps.example.yaml client/config-vps.yaml   # set server_host"
echo "  sudo .venv/bin/python3 client/vpn_client.py --config client/config-vps.yaml"
echo "  curl ifconfig.me    # should print the VPS IP"
