#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2015-2019 Jason A. Donenfeld <Jason@zx2c4.com>. All Rights Reserved.
#
# Reproducer for patch 1/2: a peer removed under load is never freed.
# Replaces tools/testing/selftests/wireguard/netns.sh for one run:
#   cp repro-peer-leak.sh tools/testing/selftests/wireguard/netns.sh
#   make -C tools/testing/selftests/wireguard/qemu NR_CPUS=1
# A leak shows as "Error: wg0: Peer N: merely created" and a failed run.
# Unpatched net-next leaked about once per 430 removals (x86 KVM, 1 vCPU).
#
# This script tests the below topology:
#
# ┌─────────────────────┐   ┌──────────────────────────────────┐   ┌─────────────────────┐
# │   $ns1 namespace    │   │          $ns0 namespace          │   │   $ns2 namespace    │
# │                     │   │                                  │   │                     │
# │┌────────┐           │   │            ┌────────┐            │   │           ┌────────┐│
# ││  wg0   │───────────┼───┼────────────│   lo   │────────────┼───┼───────────│  wg0   ││
# │├────────┴──────────┐│   │    ┌───────┴────────┴────────┐   │   │┌──────────┴────────┤│
# ││192.168.241.1/24   ││   │    │(ns1)         (ns2)      │   │   ││192.168.241.2/24   ││
# ││fd00::1/24         ││   │    │127.0.0.1:1   127.0.0.1:2│   │   ││fd00::2/24         ││
# │└───────────────────┘│   │    │[::]:1        [::]:2     │   │   │└───────────────────┘│
# └─────────────────────┘   │    └─────────────────────────┘   │   └─────────────────────┘
#                           └──────────────────────────────────┘
#
# After the topology is prepared we run a series of TCP/UDP iperf3 tests between the
# wireguard peers in $ns1 and $ns2. Note that $ns0 is the endpoint for the wg0
# interfaces in $ns1 and $ns2. See https://www.wireguard.com/netns/ for further
# details on how this is accomplished.
set -e
shopt -s extglob

exec 3>&1
export LANG=C
export WG_HIDE_KEYS=never
NPROC=( /sys/devices/system/cpu/cpu+([0-9]) ); NPROC=${#NPROC[@]}
netns0="wg-test-$$-0"
netns1="wg-test-$$-1"
netns2="wg-test-$$-2"
pretty() { echo -e "\x1b[32m\x1b[1m[+] ${1:+NS$1: }${2}\x1b[0m" >&3; }
pp() { pretty "" "$*"; "$@"; }
maybe_exec() { if [[ $BASHPID -eq $$ ]]; then "$@"; else exec "$@"; fi; }
n0() { pretty 0 "$*"; maybe_exec ip netns exec $netns0 "$@"; }
n1() { pretty 1 "$*"; maybe_exec ip netns exec $netns1 "$@"; }
n2() { pretty 2 "$*"; maybe_exec ip netns exec $netns2 "$@"; }
ip0() { pretty 0 "ip $*"; ip -n $netns0 "$@"; }
ip1() { pretty 1 "ip $*"; ip -n $netns1 "$@"; }
ip2() { pretty 2 "ip $*"; ip -n $netns2 "$@"; }
sleep() { read -t "$1" -N 1 || true; }
waitiperf() { pretty "${1//*-}" "wait for iperf:${3:-5201} pid $2"; while [[ $(ss -N "$1" -tlpH "sport = ${3:-5201}") != *\"iperf3\",pid=$2,fd=* ]]; do sleep 0.1; done; }
waitncatudp() { pretty "${1//*-}" "wait for udp:1111 pid $2"; while [[ $(ss -N "$1" -ulpH 'sport = 1111') != *\"ncat\",pid=$2,fd=* ]]; do sleep 0.1; done; }
waitiface() { pretty "${1//*-}" "wait for $2 to come up"; ip netns exec "$1" bash -c "while [[ \$(< \"/sys/class/net/$2/operstate\") != up ]]; do read -t .1 -N 0 || true; done;"; }

cleanup() {
	set +e
	exec 2>/dev/null
	printf "$orig_message_cost" > /proc/sys/net/core/message_cost
	ip0 link del dev wg0
	ip0 link del dev wg1
	ip1 link del dev wg0
	ip1 link del dev wg1
	ip2 link del dev wg0
	ip2 link del dev wg1
	local to_kill="$(ip netns pids $netns0) $(ip netns pids $netns1) $(ip netns pids $netns2)"
	[[ -n $to_kill ]] && kill $to_kill
	pp ip netns del $netns1
	pp ip netns del $netns2
	pp ip netns del $netns0
	exit
}

orig_message_cost="$(< /proc/sys/net/core/message_cost)"
trap cleanup EXIT
printf 0 > /proc/sys/net/core/message_cost

ip netns del $netns0 2>/dev/null || true
ip netns del $netns1 2>/dev/null || true
ip netns del $netns2 2>/dev/null || true
pp ip netns add $netns0
pp ip netns add $netns1
pp ip netns add $netns2
ip0 link set up dev lo

ip0 link add dev wg0 type wireguard
ip0 link set wg0 netns $netns1
ip0 link add dev wg0 type wireguard
ip0 link set wg0 netns $netns2
key1="$(pp wg genkey)"
key2="$(pp wg genkey)"
key3="$(pp wg genkey)"
key4="$(pp wg genkey)"
pub1="$(pp wg pubkey <<<"$key1")"
pub2="$(pp wg pubkey <<<"$key2")"
pub3="$(pp wg pubkey <<<"$key3")"
pub4="$(pp wg pubkey <<<"$key4")"
psk="$(pp wg genpsk)"
[[ -n $key1 && -n $key2 && -n $psk ]]

configure_peers() {
	ip1 addr add 192.168.241.1/24 dev wg0
	ip1 addr add fd00::1/112 dev wg0

	ip2 addr add 192.168.241.2/24 dev wg0
	ip2 addr add fd00::2/112 dev wg0

	n1 wg set wg0 \
		private-key <(echo "$key1") \
		listen-port 1 \
		peer "$pub2" \
			preshared-key <(echo "$psk") \
			allowed-ips 192.168.241.2/32,fd00::2/128
	n2 wg set wg0 \
		private-key <(echo "$key2") \
		listen-port 2 \
		peer "$pub1" \
			preshared-key <(echo "$psk") \
			allowed-ips 192.168.241.1/32,fd00::1/128

	ip1 link set up dev wg0
	ip2 link set up dev wg0
}
configure_peers
n1 wg set wg0 peer "$pub2" endpoint 127.0.0.1:2
n2 wg set wg0 peer "$pub1" endpoint 127.0.0.1:1

# Reproducer: remove a peer while a UDP flood keeps its receive queue full.
# Every packet on the per-peer queues holds a peer reference, so packets left
# there after removal keep the peer from ever being destroyed.
for ((i = 0; i < 200; ++i)); do
	n1 iperf3 -s -1 -B 192.168.241.1 &
	server=$!
	waitiperf $netns1 $server
	n2 iperf3 -Z -t 3 -b 0 -u -l 64 -P 4 -c 192.168.241.1 > /dev/null 2>&1 &
	client=$!
	sleep 1
	n1 wg set wg0 peer "$pub2" remove
	kill $client $server 2>/dev/null || true
	wait $client $server 2>/dev/null || true
	n1 wg set wg0 peer "$pub2" preshared-key <(echo "$psk") \
		allowed-ips 192.168.241.2/32,fd00::2/128 endpoint 127.0.0.1:2
done
pretty "" "removed a peer under load 200 times"

ip1 link del dev wg0
ip2 link del dev wg0
sleep 2 # Wait for cleanup and grace periods
declare -A objects
while read -t 0.1 -r line 2>/dev/null || [[ $? -ne 142 ]]; do
	[[ $line =~ .*(wg[0-9]+:\ [A-Z][a-z]+\ ?[0-9]*)\ .*(created|destroyed).* ]] || continue
	objects["${BASH_REMATCH[1]}"]+="${BASH_REMATCH[2]}"
done < /dev/kmsg
alldeleted=1
for object in "${!objects[@]}"; do
	if [[ ${objects["$object"]} != *createddestroyed && ${objects["$object"]} != *createdcreateddestroyeddestroyed ]]; then
		echo "Error: $object: merely ${objects["$object"]}" >&3
		alldeleted=0
	fi
done
[[ $alldeleted -eq 1 ]]
pretty "" "Objects that were created were also destroyed."
