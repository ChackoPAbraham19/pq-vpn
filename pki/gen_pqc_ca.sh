#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export OPENSSL_CONF="${SCRIPT_DIR}/openssl-pqc.cnf"

# ML-DSA keygen needs the custom OpenSSL (>=3.2 + oqs-provider). Put it on
# PATH here so `bash pki/gen_pqc_ca.sh` works exactly as documented, without
# the caller having to know about /opt/openssl-pqc.
PQC_PREFIX="${PQC_PREFIX:-/opt/openssl-pqc}"
if [ -x "${PQC_PREFIX}/bin/openssl" ]; then
    export PATH="${PQC_PREFIX}/bin:${PATH}"
    export LD_LIBRARY_PATH="${PQC_PREFIX}/lib64:${PQC_PREFIX}/lib:${LD_LIBRARY_PATH:-}"
fi

CERT_DIR="${SCRIPT_DIR}/certs/pqc"   # pki/certs is git-ignored, so never cd into it
DAYS=3650
SUBJ_PREFIX="/C=US/ST=CA/O=PQ-VPN"
CA_CN="${SUBJ_PREFIX}/CN=PQC-CA"
SERVER_CN="${SUBJ_PREFIX}/CN=vpn-server"
CLIENT_CN="${SUBJ_PREFIX}/CN=vpn-client"

mkdir -p "${CERT_DIR}"

# --- CA ---
# A real v3 CA with explicit extensions. The previous two-step
# `req` + `x509 -req -signkey` produced a bare v1 certificate that a released
# OpenSSL 3.4.7 rejects as "invalid CA certificate" (error 79); only the dev
# build on the original laptop tolerated it. openssl-pqc.cnf has no [req]
# section, so the extensions must be given here rather than inherited.
openssl req -new -x509 -newkey mldsa65 -keyout "${CERT_DIR}/ca.key" \
  -out "${CERT_DIR}/ca.crt" -days "${DAYS}" -nodes -subj "${CA_CN}" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -addext "subjectKeyIdentifier=hash"

# --- Server ---
openssl req -new -newkey mldsa65 -keyout "${CERT_DIR}/server.key" \
  -out "${CERT_DIR}/server.csr" -nodes -subj "${SERVER_CN}"
openssl x509 -req -in "${CERT_DIR}/server.csr" \
  -CA "${CERT_DIR}/ca.crt" -CAkey "${CERT_DIR}/ca.key" -CAcreateserial \
  -out "${CERT_DIR}/server.crt" -days "${DAYS}"
rm -f "${CERT_DIR}/server.csr"

# --- Client ---
openssl req -new -newkey mldsa65 -keyout "${CERT_DIR}/client.key" \
  -out "${CERT_DIR}/client.csr" -nodes -subj "${CLIENT_CN}"
openssl x509 -req -in "${CERT_DIR}/client.csr" \
  -CA "${CERT_DIR}/ca.crt" -CAkey "${CERT_DIR}/ca.key" -CAcreateserial \
  -out "${CERT_DIR}/client.crt" -days "${DAYS}"
rm -f "${CERT_DIR}/client.csr"

# --- Clean up serial file ---
rm -f "${CERT_DIR}/ca.srl"

# --- Verify ---
echo "=== PQC PKI verification ==="
openssl verify -CAfile "${CERT_DIR}/ca.crt" "${CERT_DIR}/server.crt"
openssl verify -CAfile "${CERT_DIR}/ca.crt" "${CERT_DIR}/client.crt"
echo "PQC certs ready: ${CERT_DIR}"
