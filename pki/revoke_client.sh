#!/usr/bin/env bash
set -euo pipefail
# revoke_client.sh <classical|pqc> <name> [--kick] — ban one client.
#
# Marks the cert revoked in the CA database and regenerates crl.pem. Every NEW
# TLS session the server accepts loads crl.pem, so the ban is effective for the
# client's next connection with no daemon restart. --kick additionally
# disconnects the client's live session right now via the server's IPC socket.
source "$(dirname "$0")/_env.sh" "${1:-}"
NAME="${2:?usage: $0 <classical|pqc> <name> [--kick]}"
KICK=0; [ "${3:-}" = "--kick" ] && KICK=1
CRT="${CERT_DIR}/clients/${NAME}.crt"
[ -f "$CRT" ] || { echo "no such client cert: $CRT" >&2; exit 1; }

pki_init_db
if openssl ca -config "${CA_CNF}" -revoke "$CRT" 2>&1 | grep -q "Already revoked"; then
    echo "already revoked: ${NAME}"
fi
pki_gencrl
echo "CRL regenerated: ${CERT_DIR}/crl.pem"
openssl crl -in "${CERT_DIR}/crl.pem" -noout -text | grep -E "Serial Number|Next Update" | sed 's/^ */  /'

if [ "$KICK" -eq 1 ]; then
    PROJECT_DIR="$(dirname "$PKI_DIR")"
    PY="${PROJECT_DIR}/.venv/bin/python3"; [ -x "$PY" ] || PY=python3
    "$PY" "${PROJECT_DIR}/cli/vpnctl.py" disconnect-client "${NAME}" || true
fi
