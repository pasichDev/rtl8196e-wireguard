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
  loop through the device queues instead of recursing; the receive queue is
  capped at 256 packets; packets left on the queues when a peer is removed
  are freed (a leak that also exists without this patch).

The change is prepared for the WireGuard and netdev lists in
[upstream/](upstream/).

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
  through a fixed-tuple UDP relay. They are single runs.

## Limits

- One board, one kernel version. Other single-core devices have not been tried.
- IPv6 and long-running stability were not measured.
- UDP floods far above capacity (200 Mbit/s offered) varied too much within
  one kernel to compare, and iperf3's delivered count is not trustworthy at
  80–99% loss.
- `0008` passes the kernel's WireGuard selftests on 6.18.54 (qemu arm64,
  emulated) and on `net-next` (x86 KVM), each with 4 CPUs and with 1.
- **Known issue:** on a fast single-CPU x86 guest `0008` drops packets on
  receive (~9000 TCP retransmissions per 10 s; vanilla has none), most
  likely at its 256-packet receive queue cap. The board never reaches the
  cap. See [upstream/README.md](upstream/README.md).
- Traffic routed through the board has not been measured with `0008` yet.

## License

GPL-2.0, like the Linux kernel these patches modify. See [LICENSE](LICENSE).
The port is by jnilo1; WireGuard is by Jason A. Donenfeld.
