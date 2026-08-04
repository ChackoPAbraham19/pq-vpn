#!/usr/bin/env bash
set -euo pipefail
# install_oqs_provider.sh — Build and install oqs-provider (OpenSSL 3.x plugin for PQC)
#
# Requires: liboqs already installed at /opt/liboqs
# Produces: oqsprovider.so in OpenSSL modules directory

SRC_DIR="/tmp/oqs_provider_src"
INSTALL_PREFIX="/opt/liboqs"
OQS_PROVIDER_BUILD="${SRC_DIR}/_build"

if [ -f "/usr/lib/x86_64-linux-gnu/ossl-modules/oqsprovider.so" ] || [ -f "/usr/local/lib/ossl-modules/oqsprovider.so" ]; then
    echo "[SKIP] oqsprovider.so already installed"
    exit 0
fi

echo "=== Building oqs-provider ==="

if [ ! -d "${SRC_DIR}" ]; then
    git clone --branch main --depth 1 https://github.com/open-quantum-safe/oqs-provider.git "${SRC_DIR}"
fi

# Ensure liboqs is findable
export CMAKE_PREFIX_PATH="${INSTALL_PREFIX}"

cmake -S "${SRC_DIR}" -B "${OQS_PROVIDER_BUILD}" \
    -DCMAKE_BUILD_TYPE=Release \
    -Dliboqs_DIR="${INSTALL_PREFIX}/lib/cmake/liboqs" \
    -DOPENSSL_ROOT_DIR=/usr

cmake --build "${OQS_PROVIDER_BUILD}" --parallel "$(nproc)"
sudo cmake --install "${OQS_PROVIDER_BUILD}"

echo "=== oqs-provider installed ==="
echo "Run: openssl list -providers | grep oqsprovider"
echo "Run: openssl list -kem-algorithms -provider oqsprovider -provider default"
echo "Run: openssl list -signature-algorithms -provider oqsprovider -provider default"