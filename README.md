# Fast kernel WireGuard on a single-core RTL8196E

Kernel patches and measurements for in-kernel WireGuard on the Realtek RTL8196E
(Lexra RLX4181, ~400 MHz, one core, 32 MiB RAM), as found in the SilverCrest
SGWZ 1 A1 Zigbee gateway. The kernel is Linux 6.18.54 with the
[jnilo1/rtl8196e-gateway](https://github.com/jnilo1/rtl8196e-gateway) port.

Board as the WireGuard endpoint, iperf3 on the board, wired peer, same stand
for both kernels (TCP: 11 × 30 s per direction, UDP: 5 × 15 s, medians):

| | Without | With [`0008`](patches/0008-wireguard-napi-crypt-up.patch) |
| --- | --- | --- |
| TCP to the board | 9.44 Mbit/s | **18.84 Mbit/s** (×2.0) |
| TCP from the board | 8.18 Mbit/s | **13.79 Mbit/s** (×1.69) |
| UDP from the board, 1380 / 64 B | 777 / 858 pps | **921 / 1253 pps** |
| Context switches per packet (TCP) | 1.12 / 0.91 | **0.08 / 0.08** |
| Ping through the tunnel under TCP load | 108 ms | **31 ms** |

Both kernels include `0002` and `0003`. Every kernel measured on that stand,
with ranges, CPU load and the earlier versions, is in
[results/ab-same-stand.csv](results/ab-same-stand.csv). Plain TCP to the
board, without WireGuard, runs at 73–88 Mbit/s. Older figures, including
routed traffic and the first version's chart
([endpoint-throughput.svg](results/endpoint-throughput.svg)), are in
[results/measurements.csv](results/measurements.csv).

## What made the difference

WireGuard is not limited by the cipher on this CPU. It is limited by the
**cost of each packet**. Sending UDP through the tunnel, the board managed
917, 904, 888 and 856 packets/s for 64, 256, 700 and 1380-byte payloads
([per-packet.csv](results/per-packet.csv)): 21 times more data, 7% fewer
packets. That is ~440,000 cycles per packet.

The kernel's PC sampler (`profile=4`) showed where they went. During a
WireGuard transfer about 22% of all samples sat on the IRQ re-enable after
`__schedule()` and `finish_task_switch()`, i.e. on context switches, and
`/proc/stat` counted ~0.85 context switches per tunnelled packet. WireGuard
hands every packet to its crypt workqueue so that encryption can run on
several CPUs, then to a per-peer worker to restore order. On one CPU that
buys nothing and costs a context switch per hop.

[`0008`](patches/0008-wireguard-napi-crypt-up.patch) removes those hops
when the system has one possible CPU (a static key, so SMP kernels on one
CPU benefit too):

- **Send:** packets still go on the peer's queue, which keeps their order and
  references, in entries of at most 8. A new per-peer TX NAPI encrypts and
  sends them, 16 packets per turn. A sending task runs one turn itself, so a
  local sender pays for its own encryption and cannot outrun softirq.
- **Receive:** the packet is queued as undecrypted and the peer's existing
  NAPI poll decrypts it. Decrypting earlier, inside the Ethernet driver's
  NAPI poll, raised `time_squeeze` 14-fold and made TCP collapse.
- **Safety:** encryption runs only by whoever owns the TX NAPI, so a routing
  loop through the device queues instead of recursing, and packets left on
  the queues when a peer is removed are freed.

### How it got there

1. [`0005`](patches/0005-wireguard-inline-crypto-up.patch) encrypted inline in
   `ndo_start_xmit` with bottom halves off. It was as fast, but a routing loop
   recursed into encryption at about 1.2 KB of stack per level on an 8 KiB
   stack, and the bottom-half-off work had no bound.
   [`0007`](patches/0007-wireguard-inline-tx-budget.patch) capped it at 8
   packets per call.
2. The first NAPI version was safe but slower: 16.0 instead of 18.9 Mbit/s
   receive, and UDP from the board fell to 389 pps with the CPU half idle,
   because the sender filled its socket buffer and waited for softirq.
3. Letting the sending task run one turn of the TX NAPI fixed UDP (925 pps).
   Giving the receive NAPI its default weight again fixed TCP receive: with a
   weight of 16, GRO on the tunnel flushed four times as often.

## Routing a LAN into the tunnel

The ×2 above is for the board as a tunnel endpoint. Routing a LAN into the
tunnel adds netfilter, conntrack, NAT and a second Ethernet pass to every
packet. A profile of routed traffic with `0008` puts crypto at ~21% of the
CPU, netfilter at ~13% and conntrack hashing (siphash) at another ~4%.

The tunnel's own outer UDP is never NATed, yet it went through conntrack
too. Skipping it with two raw-table rules (`CT --notrack` for UDP to and
from the tunnel endpoint) needs `CONFIG_NETFILTER_XT_TARGET_CT`, which
[wireguard-kernel.fragment](wireguard-kernel.fragment) now enables, and an
iptables build with the CT extension. Whole-LAN routing through the tunnel,
120 s iperf3 runs ([results/routed.csv](results/routed.csv)):

| | Forward | Reverse |
| --- | --- | --- |
| `0008`, conntrack on the outer UDP | 8.68 / 8.63 Mbit/s | 9.98 / 9.93 Mbit/s |
| `0008` + NOTRACK for the outer UDP | **9.23 / 9.25 / 9.09** | **10.71 / 10.66 / 10.62** |

The third NOTRACK run had the rules installed by the gateway daemon itself
rather than by hand. The port's original kernel managed 7.68 forward and
6.78 Mbit/s reverse, measured over Wi-Fi without `0002`/`0003` in single
5 s runs, so on a router the gain is roughly +20% forward and +55% reverse,
not ×2.

## Upstream

[upstream/](upstream/) holds two patches for the WireGuard and netdev lists:

1. **A bug fix for WireGuard as it is, sent on 2026-10-03** as
   [`[PATCH net] wireguard: peer: free packets left on the per-peer queues on removal`](https://lore.kernel.org/netdev/20261003201625.4572-1-apasichnik9@gmail.com/)
   ([patchwork](https://patchwork.kernel.org/project/netdevbpf/patch/20261003201625.4572-1-apasichnik9@gmail.com/)). A peer removed while its receive queue holds more
   than one NAPI budget of packets is never freed, with its keypair.
   Present since WireGuard was merged in 2019. Reproduced with
   [upstream/repro-peer-leak.sh](upstream/repro-peer-leak.sh): 5 leaks in
   2140 peer removals on `net-next` without the fix, 0 in 2140 with it
   ([results/peer-leak.csv](results/peer-leak.csv)).
2. **`0008`** in upstream form, prepared as an RFC for `net-next` and not
   sent yet. On an x86 guest with one vCPU TCP between two namespaces gains
   7–8%; with four vCPUs nothing changes
   ([results/x86-vm.csv](results/x86-vm.csv)).

Both pass the kernel's WireGuard selftests on `net` and `net-next` (x86
KVM) and on 6.18.54 (arm64, emulated), with 4 CPUs and with 1; `W=1` and
sparse report nothing new on x86_64 (SMP and UP) and i386.

## Patches

Apply on top of the port at revision `30eb5e5`. Each one after `0001` is an
opt-in switch of [build-wireguard-kernel.sh](build-wireguard-kernel.sh).

| Patch | Switch | Effect |
| --- | --- | --- |
| [0001](patches/0001-preserve-stock-flash-layout.patch) preserve stock flash layout | always | Keeps the five stock MTD partitions so only the kernel partition changes (board specific) |
| [0002](patches/0002-lib-crypto-O2.patch) `-O2` for the generic ChaCha path | `LABGW_CRYPTO_O2=1` | Receive +10% (the kernel is otherwise `-Os`) |
| [0003](patches/0003-lib-crypto-mtune-r3000.patch) `-mtune=r3000` for those files | `LABGW_CRYPTO_TUNE=1` | The Lexra gcc patch defines `PROCESSOR_LX4380` without an rtx cost table; tuning for R3000 shrinks `chacha20poly1305.o` from 5842 to 4762 bytes |
| [0004](patches/0004-wireguard-crypt-wq-single-worker.patch) drop `WQ_CPU_INTENSIVE` | `LABGW_WG_ONE_WORKER=1` | **Negative**: 20–24% slower; the pipeline serialised and the CPU idled |
| [0005](patches/0005-wireguard-inline-crypto-up.patch) inline WireGuard crypto on UP | `LABGW_WG_INLINE=1` | Superseded by 0008. With 0002 and 0003: receive ×2.2, send ×1.6 (same Wi-Fi host) |
| [0006](patches/0006-lexra-bswap-and-fused-chacha.patch) inline byte swaps + one-pass ChaCha XOR | `LABGW_LEXRA_FAST=1` | **Negative**: ~6.5% slower in a wired A/B, despite removing 144 libgcc `__bswapsi2` calls |
| [0007](patches/0007-wireguard-inline-tx-budget.patch) at most 8 packets per inline call | `LABGW_WG_INLINE_BUDGET=1` | On top of 0005; superseded by 0008 |
| [0008](patches/0008-wireguard-napi-crypt-up.patch) crypto in per-peer NAPI polls with one CPU | `LABGW_WG_NAPI=1` | With 0002 and 0003: receive ×2.0, send ×1.69 (wired, same stand). Excludes 0005 and 0007 |

The recommended set is `0001 0002 0003 0008`.

The ChaCha change in `0006` was checked against the original code on the
board before it was measured: [tools/chacha_xor_diff.c](tools/chacha_xor_diff.c),
5000 random lengths and alignments, 0 differences.

## Build

On Linux, with the port's Lexra toolchain (`mips-lexra-linux-musl-*`) on `PATH`:

```sh
git clone https://github.com/jnilo1/rtl8196e-gateway.git port
git -C port checkout --detach 30eb5e5b9ee334d2c6d76812acd4a6cdf2923423
LABGW_CRYPTO_O2=1 LABGW_CRYPTO_TUNE=1 LABGW_WG_NAPI=1 \
  ./build-wireguard-kernel.sh "$PWD/port" clean
```

The image is written to
`port/3-Main-SoC-Realtek-RTL8196E/32-Kernel/kernel-img/lidl/kernel-6.18.img`.

On any host with Docker, including macOS on arm64, build the toolchain image
once (about an hour) and then the kernel in a Docker volume:

```sh
docker build -t lexra-builder -f builder/Dockerfile port/1-Build-Environment
LABGW_CRYPTO_O2=1 LABGW_CRYPTO_TUNE=1 LABGW_WG_NAPI=1 \
  builder/build-in-docker.sh port out kernel-wireguard
```

The script checks the port revision, that WireGuard, TUN, policy routing,
legacy iptables and NAT survived Kconfig, the partition size and the Realtek
image header. It never writes to a device.

Installing a kernel is device specific and can leave a board that only
boots with UART access; follow the port's documentation. The busybox `ip`
on the stock root filesystem has no `rule` or table support, so policy
routing needs a full iproute2 `ip` in userspace.

## Measurement method

- iperf3 on the board (a static build) and on a host; only figures from the
  same host and link are compared. Wired runs vary by ~0.1–1 Mbit/s, Wi-Fi
  runs by several Mbit/s. The same-stand results come from
  [tools/wg_ab_bench.py](tools/wg_ab_bench.py) (one board session, a 1 Hz
  sampler of `/proc/stat` and `/proc/net/dev`, alternating directions) and
  [tools/wg_ab_report.py](tools/wg_ab_report.py).
- Earlier runs from a different wired Linux host showed 40–60 spurious TCP
  retransmissions per run with `0005`
  ([tools/tcp_reorder.py](tools/tcp_reorder.py) found the duplicates, no
  holes). On the stand above neither `0005`+`0007` nor `0008` showed any, so
  that effect is not attributed to either.
- WireGuard peers on the host ran in Docker containers with the standard
  kernel implementation, so every run is also an interoperability check.
- Profiles came from builds with an empty I-MEM window and `profile=4`; the
  decoder is [tools/profile_decode.py](tools/profile_decode.py).
- Routed figures come from a policy-routing gateway daemon forwarding a LAN
  client into an outbound tunnel, and an inbound peer reaching the LAN
  through a fixed-tuple UDP relay. The NOTRACK comparison used 120 s runs;
  older routed figures are single 5 s runs.
- The x86 figures come from the kernel's WireGuard selftest VM under KVM,
  with the selftest's iperf3 runs lengthened to 10 s, medians of three.

## Limits

- One board, one kernel version. Other single-core devices have not been tried.
- IPv6 and long-running stability were not measured.
- UDP floods far above capacity (200 Mbit/s offered) varied too much within
  one kernel to compare, and iperf3's delivered count is not trustworthy at
  80–99% loss.
- A UDP flood between two namespaces of one single-CPU guest loses about
  half the packets with `0008`, as plain UDP over veth does there; without
  it the crypt workqueue throttled the sender. Senders on another machine,
  as on a router, are not affected.

## License

GPL-2.0, like the Linux kernel these patches modify. See [LICENSE](LICENSE).
The port is by jnilo1; WireGuard is by Jason A. Donenfeld.
