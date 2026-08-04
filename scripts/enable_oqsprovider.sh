#!/usr/bin/env bash
set -euo pipefail
# enable_oqsprovider.sh — Generates project-scoped OpenSSL config for PQC.
#
# Creates pki/openssl-pqc.cnf which loads both default and oqsprovider.
# This config is used via OPENSSL_CONF env var — no system files are touched.
#
# Usage: bash scripts/enable_oqsprovider.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"
CONFIG_FILE="${PROJECT_DIR}/pki/openssl-pqc.cnf"
MODULE_PATH="/usr/lib/x86_64-linux-gnu/ossl-modules/oqsprovider.so"

if [ ! -f "${MODULE_PATH}" ]; then
    MODULE_PATH="/usr/local/lib/ossl-modules/oqsprovider.so"
fi

if [ ! -f "${MODULE_PATH}" ]; then
    echo "ERROR: oqsprovider.so not found. Run install_oqs_provider.sh first."
    exit 1
fi

cat > "${CONFIG_FILE}" << EOF
openssl_conf = openssl_init

[openssl_init]
providers = provider_sect

[provider_sect]
default = default_sect
oqsprovider = oqsprovider_sect

[default_sect]
activate = 1

[oqsprovider_sect]
activate = 1
module = ${MODULE_PATH}
EOF

echo "=== PQC config generated ==="
echo "Config: ${CONFIG_FILE}"
echo ""
echo "Usage: export OPENSSL_CONF=${CONFIG_FILE}"
echo "Verify: OPENSSL_CONF=${CONFIG_FILE} openssl list -providers | grep oqsprovider"
