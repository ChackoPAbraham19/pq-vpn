#!/usr/bin/env bash
set -euo pipefail
# install_custom_openssl.sh — Build OpenSSL >=3.4 with oqs-provider for pure PQC TLS
#
# The system OpenSSL 3.0.13 cannot use provider-based signature algorithms
# in TLS 1.3 (ssl_set_cert hardcodes EVP_PKEY type IDs). OpenSSL >=3.2
# dispatches to providers for certificate key loading, enabling pure
# PQC certificates (ML-DSA-65) in TLS handshakes.
#
# Installs to /opt/openssl-pqc — system OpenSSL is untouched.

PREFIX="/opt/openssl-pqc"
OPENSSL_SRC="/tmp/openssl_src"
LIBOQS_SRC="/tmp/liboqs_src"
OQS_PROVIDER_SRC="/tmp/oqs_provider_src"
BUILD_DIR="/tmp/pqc_build"

NPROC="$(nproc)"

echo "=== Installing build dependencies ==="
sudo apt-get update -qq
sudo apt-get install -y -qq build-essential cmake ninja-build libssl-dev zlib1g-dev 2>&1 | tail -2

if [ -f "${PREFIX}/bin/openssl" ]; then
    echo "[SKIP] OpenSSL already installed at ${PREFIX}"
    echo "  To rebuild: rm -rf ${PREFIX} && bash $0"
    exit 0
fi

mkdir -p "${BUILD_DIR}"

# ============================================================
# Step 1: Build OpenSSL 3.4.5 to custom prefix
# ============================================================
echo "=== Step 1/3: Building OpenSSL 3.4.5 → ${PREFIX} ==="
if [ ! -d "${OPENSSL_SRC}" ]; then
    git clone --branch openssl-3.4 --depth 1 https://github.com/openssl/openssl.git "${OPENSSL_SRC}"
fi

cd "${OPENSSL_SRC}"
./Configure --prefix="${PREFIX}" --openssldir="${PREFIX}/ssl" \
    shared zlib linux-x86_64
make -j"${NPROC}"
make install_sw
# Install dev headers too (needed for liboqs/oqs-provider builds)
make install_dev

echo "=== OpenSSL 3.4.5 installed ==="
${PREFIX}/bin/openssl version

# ============================================================
# Step 2: Rebuild liboqs against custom OpenSSL
# ============================================================
echo "=== Step 2/3: Rebuilding liboqs → ${PREFIX} ==="
if [ ! -d "${LIBOQS_SRC}" ]; then
    git clone --branch main --depth 1 https://github.com/open-quantum-safe/liboqs.git "${LIBOQS_SRC}"
fi

cd "${LIBOQS_SRC}"
mkdir -p build && cd build
cmake -GNinja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="${PREFIX}" \
    -DBUILD_SHARED_LIBS=ON \
    -DOQS_USE_OPENSSL=ON \
    -DOPENSSL_ROOT_DIR="${PREFIX}" ..
ninja
ninja install

echo "=== liboqs rebuilt ==="
ls "${PREFIX}/lib/liboqs.so"

# ============================================================
# Step 3: Rebuild oqs-provider against custom OpenSSL
# ============================================================
echo "=== Step 3/3: Rebuilding oqs-provider ==="
if [ ! -d "${OQS_PROVIDER_SRC}" ]; then
    git clone --branch main --depth 1 https://github.com/open-quantum-safe/oqs-provider.git "${OQS_PROVIDER_SRC}"
fi

cd "${OQS_PROVIDER_SRC}"
cmake -S . -B _build \
    -DCMAKE_BUILD_TYPE=Release \
    -Dliboqs_DIR="${PREFIX}/lib/cmake/liboqs" \
    -DOPENSSL_ROOT_DIR="${PREFIX}"
cmake --build _build --parallel "${NPROC}"
cmake --install _build

echo "=== oqs-provider rebuilt ==="
# Verify oqsprovider.so is in the custom OpenSSL modules dir
OQS_MODULE_DIR="${PREFIX}/lib64/ossl-modules"
if [ -d "${PREFIX}/lib/ossl-modules" ]; then
    OQS_MODULE_DIR="${PREFIX}/lib/ossl-modules"
fi
ls "${OQS_MODULE_DIR}/oqsprovider.so"

echo ""
echo "=== Build complete ==="
echo "OpenSSL:       ${PREFIX}/bin/openssl"
echo "liboqs:        ${PREFIX}/lib/liboqs.so"
echo "oqsprovider:   ${OQS_MODULE_DIR}/oqsprovider.so"
echo ""
echo "Verify:"
echo "  ${PREFIX}/bin/openssl list -providers"
echo ""
echo "To test PQC TLS with ML-DSA-65 certs:"
echo "  PATH=${PREFIX}/bin:\$PATH LD_LIBRARY_PATH=${PREFIX}/lib64 ${PREFIX}/bin/openssl s_server ..."
