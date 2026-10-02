#!/bin/sh
# Run on the Linux build host against a dedicated upstream checkout.
# Builds an experimental artifact; never writes to a device.
set -eu
upstream=${1:?usage: build-wireguard-kernel.sh /absolute/upstream-checkout}
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
upstream=$(CDPATH= cd -- "$upstream" && pwd)
revision=30eb5e5b9ee334d2c6d76812acd4a6cdf2923423
test "$(git -C "$upstream" rev-parse HEAD)" = "$revision" || {
    echo 'Unexpected upstream revision' >&2; exit 1;
}
kernel="$upstream/3-Main-SoC-Realtek-RTL8196E/32-Kernel"
# Apply the separately reviewable stock-layout patch, or accept it already applied.
layout_patch="$here/patches/0001-preserve-stock-flash-layout.patch"
if git -C "$upstream" apply --check "$layout_patch"; then
    git -C "$upstream" apply "$layout_patch"
elif git -C "$upstream" apply --reverse --check "$layout_patch" 2>/dev/null; then
    echo 'Stock flash layout patch already applied'
else
    echo 'Unexpected board partition source; refusing to guess layout' >&2
    exit 1
fi
python3 - "$kernel/config-6.18-realtek.txt" "$here/wireguard-kernel.fragment" "$upstream/wireguard.config" <<'PY'
import pathlib, re, sys
base, fragment, dest = map(pathlib.Path, sys.argv[1:])
settings = {}
for line in fragment.read_text().splitlines():
    if line.startswith('CONFIG_'):
        key, value = line.split('=', 1)
        settings[key] = value
lines = []
for line in base.read_text().splitlines():
    match = re.match(r'(?:# )?(CONFIG_[A-Z0-9_]+)(?:=| is not set)', line)
    if not match or match[1] not in settings:
        lines.append(line)
lines.extend(f'{key}={value}' for key, value in settings.items())
dest.write_text('\n'.join(lines) + '\n')
PY
# Opt-in until measured on the board: build the generic ChaCha path with -O2.
# Upstream applies patches only when it extracts a fresh tree (use `clean`).
crypto_patch="$kernel/patches-6.18/zz-labgw-lib-crypto-O2.patch"
if [ "${LABGW_CRYPTO_O2:-0}" = 1 ]; then
    cp "$here/patches/0002-lib-crypto-O2.patch" "$crypto_patch"
else
    rm -f "$crypto_patch"
fi
# Further opt-in experiments; each applies on top of the previous one.
opt_in() { # variable patch-file kernel-patch-name
    if [ "$1" = 1 ]; then
        cp "$here/patches/$2" "$kernel/patches-6.18/$3"
    else
        rm -f "$kernel/patches-6.18/$3"
    fi
}
opt_in "${LABGW_CRYPTO_TUNE:-0}" 0003-lib-crypto-mtune-r3000.patch zz-labgw-lib-crypto-tune.patch
opt_in "${LABGW_WG_ONE_WORKER:-0}" 0004-wireguard-crypt-wq-single-worker.patch zz-labgw-wireguard-wq.patch
opt_in "${LABGW_WG_INLINE:-0}" 0005-wireguard-inline-crypto-up.patch zz-labgw-wireguard-inline.patch
opt_in "${LABGW_LEXRA_FAST:-0}" 0006-lexra-bswap-and-fused-chacha.patch zz-labgw-lexra-fast.patch
export KCONFIG_FILE="$upstream/wireguard.config"
export BOARD=lidl KERNEL=6.18
# The shipped symbol-placement policy was measured with NETFILTER disabled.
# It does not match this configuration (ip_list_rcv_finish is inlined).
# Use upstream's experimental baseline layout in a clean tree.
export IMEM_POLICY_DISABLE=1
# Upstream only reads KCONFIG_FILE when .config does not already exist.
# Replace that generated file so repeated builds cannot silently use old flags.
if [ -d "$kernel/linux-6.18-rtl8196e" ]; then
    cp "$KCONFIG_FILE" "$kernel/linux-6.18-rtl8196e/.config"
    "$kernel/build_kernel.sh" olddefconfig
fi
case "${2:-}" in
    clean) "$kernel/build_kernel.sh" clean ;;
    '') "$kernel/build_kernel.sh" ;;
    *) echo 'Only the optional argument clean is supported' >&2; exit 1 ;;
esac
python3 - "$kernel/linux-6.18-rtl8196e/.config" <<'PY'
import pathlib, sys
config = set(pathlib.Path(sys.argv[1]).read_text().splitlines())
required = ('WIREGUARD', 'TUN', 'IP_MULTIPLE_TABLES', 'NETFILTER',
            'NF_CONNTRACK', 'NF_NAT', 'IP_NF_IPTABLES', 'IP_NF_FILTER',
            'IP_NF_NAT', 'NETFILTER_XT_TARGET_MASQUERADE')
missing = [key for key in required if f'CONFIG_{key}=y' not in config]
if missing:
    raise SystemExit('Required features dropped by Kconfig: ' + ', '.join(missing))
print('Required built-in kernel features verified in resolved .config')
PY
image="$kernel/kernel-img/lidl/kernel-6.18.img"
python3 - "$image" <<'PY'
import hashlib, pathlib, struct, sys
p = pathlib.Path(sys.argv[1])
data = p.read_bytes()
if len(data) < 18 or len(data) > 0x1e0000:
    raise SystemExit(f'Kernel size {len(data)} does not fit stock partition')
magic, start, burn, length = struct.unpack('>4sIII', data[:16])
if magic != b'cs6c' or burn != 0x20000 or length < 2 or length > len(data) - 16:
    raise SystemExit('Unexpected Realtek image header')
payload = data[16:16 + length]
if length % 2 or sum(struct.unpack(f'>{length//2}H', payload)) & 0xffff:
    raise SystemExit('Realtek payload checksum mismatch')
if any(data[16 + length:]):
    raise SystemExit('Unexpected nonzero image alignment padding')
print(f'image={p}\nbytes={len(data)}\nentry=0x{start:08x}')
print(f'sha256={hashlib.sha256(data).hexdigest()}')
print('Build/header checks only; boot validation remains device-specific.')
PY
