# Shared PKI environment. Source from pki/*.sh:  source "$(dirname "$0")/_env.sh" <mode>
#
# Sets PKI_MODE, CERT_DIR, and — for pqc — puts the custom OpenSSL (>=3.2 +
# oqs-provider under /opt/openssl-pqc) on PATH with the project provider config,
# so every script works as `bash pki/<script>.sh` with no caller setup.
PKI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKI_MODE="${1:?usage: <classical|pqc>}"
case "$PKI_MODE" in
    classical|pqc) ;;
    *) echo "mode must be classical or pqc (got: $PKI_MODE)" >&2; exit 2 ;;
esac
export CERT_DIR="${PKI_DIR}/certs/${PKI_MODE}"   # pki/certs is git-ignored: never cd into it
CA_CNF="${PKI_DIR}/ca.cnf"
DAYS=3650
SUBJ_PREFIX="/C=US/ST=CA/O=PQ-VPN"

if [ "$PKI_MODE" = "pqc" ]; then
    export OPENSSL_CONF="${PKI_DIR}/openssl-pqc.cnf"
    PQC_PREFIX="${PQC_PREFIX:-/opt/openssl-pqc}"
    if [ -x "${PQC_PREFIX}/bin/openssl" ]; then
        export PATH="${PQC_PREFIX}/bin:${PATH}"
        export LD_LIBRARY_PATH="${PQC_PREFIX}/lib64:${PQC_PREFIX}/lib:${LD_LIBRARY_PATH:-}"
    fi
    KEY_ALG_ARGS=(-newkey mldsa65)
else
    KEY_ALG_ARGS=(-newkey ec -pkeyopt ec_paramgen_curve:prime256v1)
fi

# CA database for `openssl ca` (issue / revoke / gencrl). Idempotent.
pki_init_db() {
    mkdir -p "${CERT_DIR}/db/newcerts" "${CERT_DIR}/clients"
    [ -f "${CERT_DIR}/db/index.txt" ] || : > "${CERT_DIR}/db/index.txt"
    [ -f "${CERT_DIR}/db/serial" ]    || echo 1000 > "${CERT_DIR}/db/serial"
    [ -f "${CERT_DIR}/db/crlnumber" ] || echo 01 > "${CERT_DIR}/db/crlnumber"
}

# Regenerate the CRL atomically from the database.
pki_gencrl() {
    openssl ca -config "${CA_CNF}" -gencrl -out "${CERT_DIR}/crl.pem.tmp" 2>/dev/null
    mv "${CERT_DIR}/crl.pem.tmp" "${CERT_DIR}/crl.pem"
}
