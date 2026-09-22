#!/usr/bin/env bash
set -euo pipefail
# issue_client.sh <classical|pqc> <name> — mint a per-client certificate.
#
# The cert is issued by `openssl ca` so it is recorded in the CA database and
# can later be revoked with pki/revoke_client.sh. Output:
#   pki/certs/<mode>/clients/<name>.crt / .key
# Copy those two files + ca.crt to the client and set `client_name: <name>`.
source "$(dirname "$0")/_env.sh" "${1:-}"
NAME="${2:?usage: $0 <classical|pqc> <name>}"
[[ "$NAME" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "name must be [A-Za-z0-9._-]+" >&2; exit 2; }
[ -f "${CERT_DIR}/ca.key" ] || { echo "no CA in ${CERT_DIR}; run pki/gen_${PKI_MODE}_ca.sh first" >&2; exit 1; }
OUT="${CERT_DIR}/clients/${NAME}"
[ -f "${OUT}.crt" ] && { echo "refusing to overwrite ${OUT}.crt (revoke it first)" >&2; exit 1; }

pki_init_db
openssl req -new "${KEY_ALG_ARGS[@]}" -nodes -keyout "${OUT}.key" -out "${OUT}.csr" \
    -subj "${SUBJ_PREFIX}/CN=${NAME}" -config "${CA_CNF}" 2>/dev/null
openssl ca -batch -config "${CA_CNF}" -extensions leaf -in "${OUT}.csr" -out "${OUT}.crt" 2>/dev/null
rm -f "${OUT}.csr"
chmod 600 "${OUT}.key"
openssl verify -CAfile "${CERT_DIR}/ca.crt" -CRLfile "${CERT_DIR}/crl.pem" -crl_check "${OUT}.crt" >/dev/null

SERIAL="$(openssl x509 -in "${OUT}.crt" -noout -serial)"
echo "issued ${PKI_MODE} client '${NAME}' (${SERIAL}) -> ${OUT}.{crt,key}"
echo "client config:"
echo "  cert_dir: pki/certs/${PKI_MODE}"
echo "  client_name: ${NAME}"
