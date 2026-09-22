#!/usr/bin/env bash
set -euo pipefail
# gen_pqc_ca.sh — ML-DSA-65 PKI: CA, server cert, CA database + empty CRL, and
# the default client cert 'client' (issued via pki/issue_client.sh so it is
# revocable). Needs the custom OpenSSL + oqs-provider (pki/_env.sh finds it).
source "$(dirname "$0")/_env.sh" pqc
CA_CN="${SUBJ_PREFIX}/CN=PQC-CA"
SERVER_CN="${SUBJ_PREFIX}/CN=vpn-server"

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
rm -f "${CERT_DIR}/server.csr" "${CERT_DIR}/ca.srl"

# --- CA database + empty CRL + default client ---
rm -rf "${CERT_DIR}/db" "${CERT_DIR}/clients"
pki_init_db
pki_gencrl
bash "${PKI_DIR}/issue_client.sh" pqc client >/dev/null
# Legacy paths (configs without client_name) point at the default client.
cp "${CERT_DIR}/clients/client.crt" "${CERT_DIR}/client.crt"
cp "${CERT_DIR}/clients/client.key" "${CERT_DIR}/client.key"

# --- Verify ---
echo "=== PQC PKI verification ==="
openssl verify -CAfile "${CERT_DIR}/ca.crt" "${CERT_DIR}/server.crt"
openssl verify -CAfile "${CERT_DIR}/ca.crt" -CRLfile "${CERT_DIR}/crl.pem" -crl_check "${CERT_DIR}/client.crt"
echo "PQC certs ready: ${CERT_DIR} (CRL: crl.pem, clients/: client)"
