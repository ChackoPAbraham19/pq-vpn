#!/usr/bin/env bash
set -euo pipefail
# install_liboqs.sh — Build and install liboqs (quantum-safe crypto library)
#
# Target: /opt/liboqs
# Prerequisites: build-essential, cmake, ninja-build, libssl-dev (already checked)

SRC_DIR="/tmp/liboqs_src"
INSTALL_PREFIX="/opt/liboqs"
BUILD_DIR="${SRC_DIR}/build"

if [ -d "${INSTALL_PREFIX}/lib" ] && [ -f "${INSTALL_PREFIX}/lib/liboqs.so" ]; then
    echo "[SKIP] liboqs already installed at ${INSTALL_PREFIX}"
    exit 0
fi

echo "=== Building liboqs ==="

if [ ! -d "${SRC_DIR}" ]; then
    git clone --branch main --depth 1 https://github.com/open-quantum-safe/liboqs.git "${SRC_DIR}"
fi

mkdir -p "${BUILD_DIR}"
cd "${BUILD_DIR}"

cmake -GNinja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="${INSTALL_PREFIX}" \
    -DBUILD_SHARED_LIBS=ON \
    -DOQS_USE_OPENSSL=ON \
    ..

ninja
sudo ninja install

echo "=== liboqs installed ==="
echo "Library:  ${INSTALL_PREFIX}/lib/liboqs.so"
echo "Headers:  ${INSTALL_PREFIX}/include/oqs/"
echo "PkgConfig: ${INSTALL_PREFIX}/lib/pkgconfig/liboqs.pc"