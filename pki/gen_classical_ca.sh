#!/usr/bin/env bash
set -euo pipefail

# Uses default OpenSSL provider only (no PQC)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CERT_DIR="$(cd "$(dirname "$0")/certs" && pwd)/classical"
DAYS=3650
SUBJ_PREFIX="/C=US/ST=CA/O=PQ-VPN"
CA_CN="${SUBJ_PREFIX}/CN=Classical-CA"
SERVER_CN="${SUBJ_PREFIX}/CN=vpn-server"
CLIENT_CN="${SUBJ_PREFIX}/CN=vpn-client"

mkdir -p "${CERT_DIR}"

# --- CA ---
openssl ecparam -genkey -name prime256v1 -out "${CERT_DIR}/ca.key"
openssl req -new -x509 -key "${CERT_DIR}/ca.key" -out "${CERT_DIR}/ca.crt" \
  -days "${DAYS}" -subj "${CA_CN}" -nodes

# --- Server ---
openssl ecparam -genkey -name prime256v1 -out "${CERT_DIR}/server.key"
openssl req -new -key "${CERT_DIR}/server.key" -out "${CERT_DIR}/server.csr" \
  -subj "${SERVER_CN}" -nodes
openssl x509 -req -in "${CERT_DIR}/server.csr" \
  -CA "${CERT_DIR}/ca.crt" -CAkey "${CERT_DIR}/ca.key" -CAcreateserial \
  -out "${CERT_DIR}/server.crt" -days "${DAYS}"
rm -f "${CERT_DIR}/server.csr"

# --- Client ---
openssl ecparam -genkey -name prime256v1 -out "${CERT_DIR}/client.key"
openssl req -new -key "${CERT_DIR}/client.key" -out "${CERT_DIR}/client.csr" \
  -subj "${CLIENT_CN}" -nodes
openssl x509 -req -in "${CERT_DIR}/client.csr" \
  -CA "${CERT_DIR}/ca.crt" -CAkey "${CERT_DIR}/ca.key" -CAcreateserial \
  -out "${CERT_DIR}/client.crt" -days "${DAYS}"
rm -f "${CERT_DIR}/client.csr"

# --- Verify ---
echo "=== Classical PKI verification ==="
openssl verify -CAfile "${CERT_DIR}/ca.crt" "${CERT_DIR}/server.crt"
openssl verify -CAfile "${CERT_DIR}/ca.crt" "${CERT_DIR}/client.crt"
echo "Classical certs ready: ${CERT_DIR}"
