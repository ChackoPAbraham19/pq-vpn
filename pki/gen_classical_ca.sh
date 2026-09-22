#!/usr/bin/env bash
set -euo pipefail
# gen_classical_ca.sh — ECDSA P-256 PKI: CA, server cert, CA database + empty
# CRL, and the default client cert 'client' (issued via pki/issue_client.sh so it
# is revocable). Uses the default OpenSSL provider only (no PQC).
source "$(dirname "$0")/_env.sh" classical
CA_CN="${SUBJ_PREFIX}/CN=Classical-CA"
SERVER_CN="${SUBJ_PREFIX}/CN=vpn-server"

mkdir -p "${CERT_DIR}"

# --- CA (v3, CA:TRUE from the default openssl.cnf req section) ---
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

# --- CA database + empty CRL + default client ---
rm -rf "${CERT_DIR}/db" "${CERT_DIR}/clients"
pki_init_db
pki_gencrl
bash "${PKI_DIR}/issue_client.sh" classical client >/dev/null
# Legacy paths (configs without client_name) point at the default client.
cp "${CERT_DIR}/clients/client.crt" "${CERT_DIR}/client.crt"
cp "${CERT_DIR}/clients/client.key" "${CERT_DIR}/client.key"

# --- Verify ---
echo "=== Classical PKI verification ==="
openssl verify -CAfile "${CERT_DIR}/ca.crt" "${CERT_DIR}/server.crt"
openssl verify -CAfile "${CERT_DIR}/ca.crt" -CRLfile "${CERT_DIR}/crl.pem" -crl_check "${CERT_DIR}/client.crt"
echo "Classical certs ready: ${CERT_DIR} (CRL: crl.pem, clients/: client)"
