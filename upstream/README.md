# Sending the WireGuard change upstream

> ✅ **Ready for an RFC**, once signed off. Applies to `net-next`
> (`c29fbea7e`) unchanged and passes the WireGuard selftests there under
> KVM with 1 and 4 vCPUs. Patch 1/2's leak is reproduced with
> [`repro-peer-leak.sh`](repro-peer-leak.sh): 5 leaks in 2140 peer
> removals without it, 0 in 2140 with it. Patch 2/2 on x86 with one vCPU:
> TCP +7–8%, no retransmissions; four vCPUs unchanged. A same-guest UDP
> flood on one CPU now loses about half the packets, like plain UDP over
> veth; the cover letter says so.

A two-patch RFC series, generated on Linux 6.18.54; it applies to `net-next`
unchanged:

| Patch | What | Board equivalent |
|---|---|---|
| [`0001`](0001-wireguard-peer-free-packets-left-on-the-per-peer-que.patch) | Fix: packets left on the per-peer queues when a peer is removed keep the peer alive. Present since WireGuard was merged; applies on its own. | part of [`0008`](../patches/0008-wireguard-napi-crypt-up.patch) |
| [`0002`](0002-wireguard-run-crypto-in-the-peers-NAPI-polls-on-sing.patch) | With one possible CPU, crypto runs in budgeted per-peer NAPI polls instead of the crypt workqueue. | [`0008`](../patches/0008-wireguard-napi-crypt-up.patch) |

The cover letter is [`0000-cover-letter.patch`](0000-cover-letter.patch).

## Why not the first version

The first version ([`../patches/0005`](../patches/0005-wireguard-inline-crypto-up.patch),
with the budget in [`0007`](../patches/0007-wireguard-inline-tx-budget.patch))
encrypted inline in `ndo_start_xmit` with bottom halves off. It was fast, but a
review found problems that would block it:

- A routing loop through the device recursed into encryption on every level.
  Each level costs about 1.2 KB of stack, and the 8 KiB MIPS stack has no guard
  page; WireGuard has no loop guard of its own.
- Bottom-half-off work was bounded only by packet count, and the transmit
  worker drained its whole queue with bottom halves off.
- It was selected with `!CONFIG_SMP`, which misses SMP kernels on one CPU.

In patch 2 encryption runs only by whoever owns the TX NAPI: its poll, or a
sending task for one budget. A nested transmit finds the NAPI owned and just
queues. Transmit works in turns of 16 packets; receive keeps the default NAPI
weight, because 16 cost 11% of TCP receive throughput on the board (GRO
flushed more often). A static key selects the mode when
`num_possible_cpus() == 1`.

## Before sending

1. Check that the cover letter's "Tested:" paragraph matches what was run.
2. Apply to a current `net-next`:
   ```sh
   git clone https://git.kernel.org/pub/scm/linux/kernel/git/netdev/net-next.git
   cd net-next && git am -3 ../000[12]-*.patch
   ```
3. Run the WireGuard selftests twice: once with several CPUs (workqueue path)
   and once with one (NAPI path). One CPU is enough; no `CONFIG_SMP=n` build is
   needed:
   ```sh
   make -C tools/testing/selftests/wireguard/qemu -j"$(nproc)"
   make -C tools/testing/selftests/wireguard/qemu -j"$(nproc)" NR_CPUS=1
   ```
4. Keep the `Assisted-by: LLM` lines. The kernel requires them for AI-assisted
   work (`Documentation/process/coding-assistants.rst`). Read every line, then
   sign off yourself; a tool must not add a `Signed-off-by`:
   `git rebase --exec 'git commit --amend --no-edit -s' HEAD~2`.
5. Run `scripts/checkpatch.pl --strict` on the result. On 6.18.54 with a
   mainline `checkpatch.pl`, the only findings are the missing `Signed-off-by`
   and the `Fixes:` id, which it cannot resolve without a git tree. The id is
   `e7096c131e51`, the commit that added WireGuard.

## Sending

`net-next` is closed during the merge window; check
<https://patchwork.hopto.org/net-next.html> first.

Patch 1 stands on its own: a reproduced bug in WireGuard as merged, with a
`Fixes:` tag. It is the least contested part and can go first, alone, as
`[PATCH net]` with the reproducer described in its commit message; patch 2
then follows as the RFC.

```sh
git format-patch -2 --subject-prefix="RFC PATCH net-next" --cover-letter -o out/
# copy the text of 0000-cover-letter.patch into out/0000-cover-letter.patch
./scripts/get_maintainer.pl out/000[12]-*.patch
git send-email --to=wireguard@lists.zx2c4.com --to=netdev@vger.kernel.org \
  --cc="Jason A. Donenfeld <Jason@zx2c4.com>" out/*.patch
```
