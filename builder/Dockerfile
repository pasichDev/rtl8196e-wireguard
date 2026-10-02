# Lexra-only subset of the port's 1-Build-Environment/Dockerfile (@30eb5e5),
# without the x86-only Silabs/ARM parts, so it also builds on arm64 hosts.
# Build context: the port's 1-Build-Environment directory (see build-in-docker.sh).
FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y build-essential bison flex gawk bc \
      help2man texinfo libtool-bin libncurses5-dev unzip wget curl bzip2 lbzip2 \
      xz-utils zlib1g-dev git libssl-dev libelf-dev device-tree-compiler pkg-config \
      cpio python3 rsync sudo file kmod \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
RUN useradd -m -s /bin/bash builder && echo 'builder ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers
USER builder
WORKDIR /home/builder
RUN cd /tmp && wget -q http://crosstool-ng.org/download/crosstool-ng/crosstool-ng-1.28.0.tar.bz2 && \
    tar xjf crosstool-ng-1.28.0.tar.bz2 && cd crosstool-ng-1.28.0 && \
    ./configure --prefix=$HOME/crosstool-ng >/dev/null && make -j$(nproc) >/dev/null && \
    make install >/dev/null && rm -rf /tmp/crosstool-ng*
ENV PATH="/home/builder/crosstool-ng/bin:${PATH}"
COPY --chown=builder:builder 10-lexra-toolchain/crosstool-ng.config /home/builder/toolchain.config
COPY --chown=builder:builder 10-lexra-toolchain/patches /home/builder/patches
RUN mkdir -p ~/.crosstool-ng/patches && cp -r /home/builder/patches/* ~/.crosstool-ng/patches/
RUN mkdir -p /tmp/tc && cd /tmp/tc && cp /home/builder/toolchain.config .config && \
    sed -i 's|CT_PREFIX_DIR=.*|CT_PREFIX_DIR="/home/builder/x-tools/mips-lexra-linux-musl"|' .config && \
    (ct-ng build || ct-ng build || ct-ng build) && \
    rm -rf /tmp/tc ~/.crosstool-ng/tarballs ~/.crosstool-ng/src
ENV PATH="/home/builder/x-tools/mips-lexra-linux-musl/bin:${PATH}"
RUN mips-lexra-linux-musl-gcc --version
COPY --chown=builder:builder 11-realtek-tools /home/builder/realtek-tools
RUN cd /home/builder/realtek-tools && mkdir -p bin && \
    cd cvimg && make && cp cvimg ../bin/ && cd .. && \
    cd lzma-4.65/CPP/7zip/Compress/LZMA_Alone && make -f makefile.gcc && cp lzma /home/builder/realtek-tools/bin/
ENV PATH="/home/builder/realtek-tools/bin:${PATH}"
WORKDIR /workspace
