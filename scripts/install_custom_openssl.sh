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

# Pinned upstream versions. `main` of liboqs and oqs-provider drift apart
# (the Docker clean-build hit oqs-provider main referencing algorithm names a
# same-day liboqs main did not export), and releases must be paired: liboqs
# 0.16.0 renamed the SPHINCS+ symbols oqs-provider 0.11.0 still uses, so the
# newest matching released pair is liboqs 0.15.0 + oqs-provider 0.11.0.
# ML-KEM-768 / ML-DSA-65 / X25519MLKEM768 are identical across these.
# Override to experiment:
#   LIBOQS_TAG=0.15.0 OQSPROVIDER_TAG=0.11.0 OPENSSL_TAG=openssl-3.4.7 bash scripts/install_custom_openssl.sh
OPENSSL_TAG="${OPENSSL_TAG:-openssl-3.4.7}"
LIBOQS_TAG="${LIBOQS_TAG:-0.15.0}"
OQSPROVIDER_TAG="${OQSPROVIDER_TAG:-0.11.0}"
OPENSSL_SRC="/tmp/openssl_src"
LIBOQS_SRC="/tmp/liboqs_src"
OQS_PROVIDER_SRC="/tmp/oqs_provider_src"
BUILD_DIR="/tmp/pqc_build"

NPROC="$(nproc)"

# Root inside a container has no sudo; a normal user needs it.
SUDO="$(command -v sudo || true)"
[ "$(id -u)" -eq 0 ] && SUDO=""

echo "=== Installing build dependencies ==="
$SUDO apt-get update -qq
$SUDO apt-get install -y -qq build-essential cmake ninja-build git libssl-dev zlib1g-dev 2>&1 | tail -2

if [ -f "${PREFIX}/bin/openssl" ]; then
    echo "[SKIP] OpenSSL already installed at ${PREFIX}"
    echo "  To rebuild: rm -rf ${PREFIX} && bash $0"
    exit 0
fi

mkdir -p "${BUILD_DIR}"

# ============================================================
# Step 1: Build OpenSSL 3.4.5 to custom prefix
# ============================================================
echo "=== Step 1/3: Building OpenSSL ${OPENSSL_TAG} → ${PREFIX} ==="
if [ ! -d "${OPENSSL_SRC}" ]; then
    git clone --branch "${OPENSSL_TAG}" --depth 1 https://github.com/openssl/openssl.git "${OPENSSL_SRC}"
fi

cd "${OPENSSL_SRC}"
./Configure --prefix="${PREFIX}" --openssldir="${PREFIX}/ssl" \
    shared zlib linux-x86_64
make -j"${NPROC}"
make install_sw
# Install dev headers too (needed for liboqs/oqs-provider builds)
make install_dev
# And the default openssl.cnf: without it any `openssl req` run through this
# binary with no OPENSSL_CONF dies with "Can't open .../ssl/openssl.cnf"
# (the classical PKI script, on a box where this openssl is first on PATH).
make install_ssldirs

# The new binary links against ${PREFIX}/lib64, not the system libssl 3.0;
# without this the very next line fails with "OPENSSL_3.4.0 not found" on a
# clean machine (found by the Docker clean-build check).
export LD_LIBRARY_PATH="${PREFIX}/lib64:${PREFIX}/lib:${LD_LIBRARY_PATH:-}"

echo "=== OpenSSL 3.4.x installed ==="
${PREFIX}/bin/openssl version

# ============================================================
# Step 2: Rebuild liboqs against custom OpenSSL
# ============================================================
echo "=== Step 2/3: Building liboqs ${LIBOQS_TAG} → ${PREFIX} ==="
if [ ! -d "${LIBOQS_SRC}" ]; then
    git clone --branch "${LIBOQS_TAG}" --depth 1 https://github.com/open-quantum-safe/liboqs.git "${LIBOQS_SRC}"
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
echo "=== Step 3/3: Building oqs-provider ${OQSPROVIDER_TAG} ==="
if [ ! -d "${OQS_PROVIDER_SRC}" ]; then
    git clone --branch "${OQSPROVIDER_TAG}" --depth 1 https://github.com/open-quantum-safe/oqs-provider.git "${OQS_PROVIDER_SRC}"
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

# Make the prefix's libraries resolvable system-wide. liboqs lands in
# ${PREFIX}/lib while OpenSSL uses lib64; oqsprovider.so NEEDS liboqs.so.9, so
# without this a fresh machine fails with "liboqs.so.9: cannot open shared
# object file" (the dev laptop only worked thanks to a stray /usr/local/lib
# copy). Found by the Docker clean-build check.
if [ -w /etc/ld.so.conf.d ] || [ -n "$SUDO" ]; then
    printf '%s/lib64\n%s/lib\n' "${PREFIX}" "${PREFIX}" | $SUDO tee /etc/ld.so.conf.d/openssl-pqc.conf >/dev/null
    $SUDO ldconfig
    echo "=== registered ${PREFIX}/{lib64,lib} with ldconfig ==="
fi

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
