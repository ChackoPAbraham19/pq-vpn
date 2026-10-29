#!/usr/bin/env bash
set -euo pipefail
# refresh_crl.sh <classical|pqc> — reissue the CRL from the existing CA.
#
# A CRL carries its own validity window (default_crl_days in pki/ca.cnf) and
# stops being accepted once it passes, even when nothing has been revoked. The
# server hands `-CRL crl.pem -crl_check -verify_return_error` to every session,
# so an expired CRL rejects every client — reported only as "error 12 ... CRL
# has expired" from inside a failed TLS handshake.
#
# This touches the CA not at all: same key, same certificates, same serial
# numbers, same revocation list. Only the CRL's dates move. Use it whenever the
# server's startup preflight warns that the CRL is near or past expiry.
#
#   bash pki/refresh_crl.sh pqc
#   bash pki/refresh_crl.sh classical
#
# Do NOT reach for gen_<mode>_ca.sh to fix an expired CRL: that mints a brand
# new CA, which silently invalidates every certificate already distributed —
# including the copy on a deployed server, which then rejects every client with
# "self-signed certificate in certificate chain".
#
# After refreshing the CRL for a deployed server, copy it over and restart:
#   rsync -a pki/certs/pqc/ ubuntu@<host>:/opt/pqvpn/pki/certs/pqc/
#   ssh ubuntu@<host> 'sudo systemctl restart pqvpn-server'
source "$(dirname "$0")/_env.sh" "${1:-}"

[ -f "${CERT_DIR}/ca.key" ] || {
    echo "no CA in ${CERT_DIR}; run pki/gen_${PKI_MODE}_ca.sh first" >&2; exit 1; }

pki_init_db
pki_gencrl

NEXT="$(openssl crl -in "${CERT_DIR}/crl.pem" -noout -nextupdate | cut -d= -f2)"
REVOKED="$(grep -c '^R' "${CERT_DIR}/db/index.txt" || true)"
echo "refreshed ${PKI_MODE} CRL -> ${CERT_DIR}/crl.pem"
echo "  valid until : ${NEXT}"
echo "  revoked     : ${REVOKED} certificate(s)"

# Prove it: the server enforces exactly this check on every handshake.
if openssl verify -CAfile "${CERT_DIR}/ca.crt" -CRLfile "${CERT_DIR}/crl.pem" \
        -crl_check "${CERT_DIR}/server.crt" >/dev/null 2>&1; then
    echo "  verified    : server.crt passes -crl_check"
else
    echo "  WARNING: server.crt still fails -crl_check against this CRL" >&2
    exit 1
fi
