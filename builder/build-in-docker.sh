#!/bin/sh
# Build a board kernel in the lexra-builder image, on any host with Docker.
# The kernel tree lives in a Docker volume: macOS file systems are
# case-insensitive and the kernel tree is not.
#
#   docker build -t lexra-builder -f builder/Dockerfile <port>/1-Build-Environment
#   LABGW_CRYPTO_O2=1 LABGW_CRYPTO_TUNE=1 LABGW_WG_NAPI=1 \
#     builder/build-in-docker.sh <port-checkout> <output-dir> <name>
#
# Writes <output-dir>/<name>.img and <name>.uts (the kernel's UTS_VERSION,
# to compare with /proc/version after booting it).
set -eu
port=$(CDPATH= cd -- "${1:?port checkout}" && pwd)
out=${2:?output dir}; name=${3:?output name}
mkdir -p "$out"; out=$(CDPATH= cd -- "$out" && pwd)
here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
docker volume create labgw-work >/dev/null
docker run --rm --user root -v labgw-work:/work alpine chown 1000:1000 /work
env_args=""
for v in LABGW_CRYPTO_O2 LABGW_CRYPTO_TUNE LABGW_WG_ONE_WORKER LABGW_WG_INLINE \
         LABGW_WG_INLINE_BUDGET LABGW_WG_NAPI LABGW_LEXRA_FAST IMEM_PROFILE; do
    eval "val=\${$v:-0}"; env_args="$env_args -e $v=$val"
done
docker run --rm $env_args -e NAME="$name" \
    -v labgw-work:/work -v "$port:/src/port:ro" -v "$here:/src/wg:ro" -v "$out:/out" \
    lexra-builder bash -euc '
rev=30eb5e5b9ee334d2c6d76812acd4a6cdf2923423
[ -d /work/port/.git ] || git clone -q /src/port /work/port
git -C /work/port checkout -q -f "$rev"
rsync -a --delete --exclude __pycache__ /src/wg/ /work/wg/
sh /work/wg/build-wireguard-kernel.sh /work/port clean
k=/work/port/3-Main-SoC-Realtek-RTL8196E/32-Kernel
cp "$k/kernel-img/lidl/kernel-6.18.img" "/out/$NAME.img"
grep -h UTS_VERSION "$k/linux-6.18-rtl8196e/include/generated/utsversion.h" | cut -d\" -f2 > "/out/$NAME.uts"
sha256sum "/out/$NAME.img"; cat "/out/$NAME.uts"'
