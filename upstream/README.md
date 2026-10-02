# Sending the WireGuard change upstream

[`0001-wireguard-queueing-skip-the-crypt-workqueue-on-unipr.patch`](0001-wireguard-queueing-skip-the-crypt-workqueue-on-unipr.patch)
is the cleaned-up form of [`../patches/0005`](../patches/0005-wireguard-inline-crypto-up.patch):
neutral comments, a commit message with the motivation and the numbers, and
no change in behaviour. It applies to Linux 6.18.54. `checkpatch.pl` reports
no warnings; its only error is the missing `Signed-off-by`, which the author
must add.

## Before sending

1. Rebase on `net-next` and rebuild. Check the WireGuard files changed little
   since 6.18:
   ```sh
   git clone https://git.kernel.org/pub/scm/linux/kernel/git/netdev/net-next.git
   cd net-next && git am -3 ../0001-wireguard-queueing-skip-the-crypt-workqueue-on-unipr.patch
   ```
2. Sign it off (Developer Certificate of Origin, by the author, not a tool):
   `git commit --amend -s`.
3. Run the kernel's WireGuard selftest on an SMP and a UP build:
   `tools/testing/selftests/wireguard/qemu` (`make -C tools/testing/selftests/wireguard/qemu`),
   with `CONFIG_SMP=n` set for the second run.
4. Run `scripts/checkpatch.pl --strict` on the result.

## Sending

`net-next` is closed during the merge window; check
<https://patchwork.hopto.org/net-next.html> first. Then:

```sh
git format-patch -1 --subject-prefix="PATCH net-next" --cover-letter -o out/
# paste cover-letter.txt into out/0000-cover-letter.patch
./scripts/get_maintainer.pl out/0001-*.patch
git send-email --to=wireguard@lists.zx2c4.com --to=netdev@vger.kernel.org \
  --cc="Jason A. Donenfeld <Jason@zx2c4.com>" out/*.patch
```

`get_maintainer.pl` on 6.18 lists Jason A. Donenfeld (WireGuard maintainer),
the netdev maintainers, `wireguard@lists.zx2c4.com` and `netdev@vger.kernel.org`.
