#!/usr/bin/env bash
set -euo pipefail
# clean_build_check.sh — Phase 0b: prove the stack builds from a bare
# Ubuntu 24.04 and the offline suites pass inside it.
#
#   bash scripts/clean_build_check.sh          # build image + run dry-run suites
#   bash scripts/clean_build_check.sh --live   # also run the netns demo inside
#                                              # a privileged container (best effort)
#   bash scripts/clean_build_check.sh --no-cache
#
# First build is ~15 min (OpenSSL + liboqs + oqs-provider); later builds reuse
# the cached crypto stage and only re-copy the project.

PDIR="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${PQVPN_IMAGE:-pqvpn}"
LIVE=0
BUILD_ARGS=()
for a in "$@"; do
    case "$a" in
        --live) LIVE=1 ;;
        --no-cache) BUILD_ARGS+=(--no-cache) ;;
        *) echo "unknown option: $a" >&2; exit 2 ;;
    esac
done

command -v docker >/dev/null || { echo "docker not found" >&2; exit 1; }

# Some hosts (ufw with FORWARD=DROP is the usual one) give bridge-mode
# containers no egress at all, which breaks apt/git inside the build. Probe
# once and fall back to the host network for the BUILD only; the resulting
# image is identical.
if ! timeout 20 docker run --rm ubuntu:24.04 getent hosts archive.ubuntu.com >/dev/null 2>&1; then
    echo "!! bridge-mode containers cannot resolve/reach the internet on this host"
    echo "   (ufw FORWARD policy?) — building with --network=host instead"
    BUILD_ARGS+=(--network=host)
fi

t0=$(date +%s)
echo "== building ${IMAGE} from ubuntu:24.04 (this is the clean-build proof) =="
docker build "${BUILD_ARGS[@]}" -t "${IMAGE}" "${PDIR}"
echo "== build ok in $(( $(date +%s) - t0 ))s =="

echo "== provider check inside the image =="
docker run --rm "${IMAGE}" sh -c \
    'openssl list -kem-algorithms -provider oqsprovider -provider default | grep -i mlkem768; \
     openssl list -signature-algorithms -provider oqsprovider -provider default | grep -i mldsa65; \
     openssl version'

echo "== offline suites + handshake bench inside the image =="
docker run --rm "${IMAGE}"

if [[ "$LIVE" -eq 1 ]]; then
    echo "== live netns demo inside a privileged container (best effort) =="
    docker run --rm --privileged --cap-add NET_ADMIN --device /dev/net/tun \
        "${IMAGE}" bash scripts/demo.sh --mode pqc --clients 2
fi
echo "== clean build check passed in $(( $(date +%s) - t0 ))s =="
