# PQ-VPN clean-build proof (Phase 0b).
#
# Stage 1 builds the post-quantum crypto stack (OpenSSL 3.4 + liboqs +
# oqs-provider -> /opt/openssl-pqc) from a bare Ubuntu 24.04 using the same
# script a VPS would run. Stage 2 layers the project on top, gates on the
# provider actually listing ML-KEM-768 / ML-DSA-65, generates both PKIs and
# runs the offline test suites. If this image builds, docs/setup.md is true.
#
#   docker build -t pqvpn .            # ~15 min the first time, cached after
#   docker run --rm pqvpn              # all five unit suites + handshake bench
#   bash scripts/clean_build_check.sh  # the same, plus optional --live netns demo

FROM ubuntu:24.04 AS pqc-build
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
        build-essential cmake ninja-build git ca-certificates \
        libssl-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
COPY scripts/install_custom_openssl.sh /tmp/install_custom_openssl.sh
RUN bash /tmp/install_custom_openssl.sh \
    && rm -rf /tmp/openssl_src /tmp/liboqs_src /tmp/oqs_provider_src /tmp/pqc_build

FROM pqc-build AS pqvpn
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
        python3 python3-venv python3-pip \
        iproute2 iptables iputils-ping iperf3 dnsmasq netcat-openbsd procps \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/pqvpn
ENV PATH=/opt/openssl-pqc/bin:$PATH \
    LD_LIBRARY_PATH=/opt/openssl-pqc/lib64:/opt/openssl-pqc/lib \
    PYTHONUNBUFFERED=1
COPY . .
# Phase 0b verify gate: the provider must expose the algorithms we depend on.
RUN openssl list -kem-algorithms -provider oqsprovider -provider default | grep -qi 'mlkem768' \
    && openssl list -signature-algorithms -provider oqsprovider -provider default | grep -qi 'mldsa65' \
    && echo "verify gate: ML-KEM-768 + ML-DSA-65 available via oqsprovider"
RUN python3 -m venv .venv && .venv/bin/pip install -q --no-cache-dir -r requirements.txt
RUN bash pki/gen_classical_ca.sh && bash pki/gen_pqc_ca.sh \
    && openssl verify -provider oqsprovider -provider default \
         -CAfile pki/certs/pqc/ca.crt pki/certs/pqc/server.crt pki/certs/pqc/client.crt
CMD ["bash", "scripts/demo.sh", "--dry-run", "--mode", "pqc"]
