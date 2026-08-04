#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export OPENSSL_CONF="${SCRIPT_DIR}/openssl-pqc.cnf"

CERT_DIR="$(cd "$(dirname "$0")/certs" && pwd)/pqc"
DAYS=3650
SUBJ_PREFIX="/C=US/ST=CA/O=PQ-VPN"
CA_CN="${SUBJ_PREFIX}/CN=PQC-CA"
SERVER_CN="${SUBJ_PREFIX}/CN=vpn-server"
CLIENT_CN="${SUBJ_PREFIX}/CN=vpn-client"

mkdir -p "${CERT_DIR}"

# --- CA ---
openssl req -new -newkey mldsa65 -keyout "${CERT_DIR}/ca.key" \
  -out "${CERT_DIR}/ca.csr" -nodes -subj "${CA_CN}"
openssl x509 -req -in "${CERT_DIR}/ca.csr" \
  -signkey "${CERT_DIR}/ca.key" -out "${CERT_DIR}/ca.crt" -days "${DAYS}"
rm -f "${CERT_DIR}/ca.csr"

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
