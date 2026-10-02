#!/usr/bin/env python3
"""WireGuard throughput benchmark with the board as tunnel endpoint.

Run once per kernel. Board side: one temporary WireGuard interface, iperf3
server and a 1 Hz counter sampler, all under a RAM directory removed at the
end. Host side: one container (standard kernel WireGuard) as the peer.
No SSH session is opened during a measurement: board counters come from the
sampler and are matched to runs through the board's uptime clock.

Measurements:
  TCP to/from board   --tcp-runs x --tcp-seconds, directions alternated
  UDP from board      --udp-runs x --udp-seconds, as fast as the board can send
  UDP to board        same, near capacity (0.4/10 Mbit/s) and flooded (200 Mbit/s)
  ping in the tunnel  idle, and during a TCP-to-board run
"""
import argparse
import json
import pathlib
import re
import statistics
import subprocess
import time

P = argparse.ArgumentParser()
P.add_argument('--board-helper', type=pathlib.Path, required=True, help='runs one shell command on the board as root')
P.add_argument('--board', required=True, help='board IPv4 address')
P.add_argument('--host', required=True, help="ssh name of the Linux host, or 'local' to run Docker here")
P.add_argument('--image', default='labgw-board-stand', help='host image with wireguard-tools and iperf3')
P.add_argument('--iperf3', type=pathlib.Path, required=True, help='static iperf3 for the board')
P.add_argument('--tools', default='/tuya/labgw/tools', help='board directory with ip and wg')
P.add_argument('--label', required=True)
P.add_argument('--ip', type=pathlib.Path, help='full iproute2 ip for the board (needed for --gso-max-size)')
P.add_argument('--gso-max-size', type=int, help='set gso_max_size on the board WireGuard interface')
P.add_argument('--threaded', action='store_true', help='enable threaded NAPI on the board WireGuard interface')
P.add_argument('--skip-flood', action='store_true')
P.add_argument('--capture', type=pathlib.Path, help='save a TCP header capture from the host peer to this pcap')
P.add_argument('--tcp-runs', type=int, default=11)
P.add_argument('--tcp-seconds', type=int, default=30)
P.add_argument('--udp-runs', type=int, default=5)
P.add_argument('--udp-seconds', type=int, default=15)
P.add_argument('--results', type=pathlib.Path, required=True)
A = P.parse_args()

D, T, NAME, PORT = '/var/tmp/wgab', A.tools, 'wgab-peer', 5301
BOARD_TUN, PEER_TUN = '10.254.198.1', '10.254.198.2'
R = {'label': A.label, 'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'runs': [], 'cleanup': []}
state = {'dir': False, 'iface': False, 'ctr': False}


def board(cmd, data=None, timeout=200):
    # The vendor sshd counts an aborted session as a failed login and then
    # closes new connections for 90-300 s: never abort early, wait it out.
    for _ in range(4):
        p = subprocess.run([str(A.board_helper), cmd], input=data, capture_output=True, timeout=timeout,
                           stdin=None if data is not None else subprocess.DEVNULL)
        if p.returncode != 255 or b'Connection closed' not in p.stderr:
            break
        print('board ssh locked out, waiting 120 s', flush=True)
        time.sleep(120)
    if p.returncode:
        raise RuntimeError('board: %s: %s' % (cmd[:60], p.stderr.decode(errors='replace')[-300:]))
    return p.stdout.decode(errors='replace')


def host_argv(cmd):
    return ['sh', '-c', cmd] if A.host == 'local' else ['ssh', '-o', 'BatchMode=yes', A.host, cmd]


def host(cmd, timeout=120, check=True):
    p = subprocess.run(host_argv(cmd), capture_output=True, text=True,
                       timeout=timeout, stdin=subprocess.DEVNULL)
    if check and p.returncode:
        raise RuntimeError('host: %s: %s' % (cmd[:60], p.stderr[-300:]))
    return p.stdout


def peer(cmd, timeout=120):
    return host("docker exec %s sh -c '%s'" % (NAME, cmd), timeout=timeout, check=False)


def iperf(args, seconds):
    out = peer('iperf3 -c %s -p %d -t %d -J %s' % (BOARD_TUN, PORT, seconds, args), timeout=seconds + 60)
    return json.loads(out) if out.strip().startswith('{') else {'error': out[-300:]}


def run(kind, direction, args, seconds, extra=None):
    t0 = time.monotonic()
    raw = iperf(args, seconds)
    t1 = time.monotonic()
    end = raw.get('end', {}) if isinstance(raw, dict) else {}
    entry = {'kind': kind, 'direction': direction, 'args': args, 'seconds': seconds,
             'host_start': t0, 'host_end': t1, 'error': raw.get('error')}
    if kind == 'tcp':
        s = end.get('sum_received') or {}
        entry['mbit_s'] = round(s.get('bits_per_second', 0) / 1e6, 3) if s else None
        entry['retransmits'] = (end.get('sum_sent') or {}).get('retransmits')
    else:
        s = end.get('sum') or {}
        if s:
            recv = s.get('bytes', 0) * (1 - (s.get('lost_percent') or 0) / 100.0)
            size = int(re.search(r'-l (\d+)', args).group(1))
            entry['delivered_pps'] = round(recv / size / s.get('seconds', seconds))
            entry['delivered_mbit_s'] = round(recv * 8 / s.get('seconds', seconds) / 1e6, 3)
            entry['lost_percent'] = s.get('lost_percent')
    if extra:
        entry.update(extra)
    R['runs'].append(entry)
    print('%-3s %-10s %-26s %s' % (kind, direction, args, {k: entry.get(k) for k in
          ('mbit_s', 'retransmits', 'delivered_pps', 'lost_percent')}), flush=True)


def ping_stats(text):
    m = re.search(r'(\d+) packets transmitted, (\d+) received', text)
    r = re.search(r'= ([\d.]+)/([\d.]+)/([\d.]+)', text)
    return {'sent': int(m.group(1)), 'received': int(m.group(2)),
            'min_avg_max_ms': [float(x) for x in r.groups()]} if m and r else {'raw': text[-300:]}


try:
    if board('test -e %s && echo dir; test -e /sys/class/net/wgstage && echo iface; true' % D).strip():
        raise RuntimeError('leftover board state')
    if host('docker ps -a --format "{{.Names}}" | grep -x %s; true' % NAME).strip():
        raise RuntimeError('leftover host container')
    R['board_kernel'] = board('cat /proc/version').strip()
    R['host_link'] = host(("route -n get %s | grep interface" if A.host == 'local' else
                           "ip route get %s | head -n 1") % A.board).strip()

    state['dir'] = True
    board('mkdir -p %s && cat > %s/iperf3 && chmod 700 %s/iperf3' % (D, D, D), data=A.iperf3.read_bytes())
    host('docker run -d --name %s --cap-add NET_ADMIN %s sleep 7200' % (NAME, A.image))
    state['ctr'] = True
    ppub = peer('umask 077; wg genkey > /tmp/key && wg pubkey < /tmp/key').strip()
    state['iface'] = True
    # One session for all board-side setup, including the 1 Hz sampler.
    sampler = ('while :; do read up idle < /proc/uptime; echo "U $up"; head -n 1 /proc/stat; '
               'grep ctxt /proc/stat; grep -e wgstage -e eth0 /proc/net/dev; grep "^Udp:" /proc/net/snmp | tail -n 1; '
               'echo "H $(%s/wg show wgstage latest-handshakes 2>/dev/null)"; sleep 1; done' % T)
    bpub = board(
        'umask 077 && %(T)s/wg genkey > %(D)s/key && %(T)s/ip link add wgstage type wireguard && '
        '%(T)s/wg set wgstage private-key %(D)s/key listen-port 51873 peer %(ppub)s allowed-ips %(peer)s/32 && '
        '%(T)s/ip addr add %(bt)s/30 dev wgstage && %(T)s/ip link set wgstage up && '
        '%(D)s/iperf3 -s -D -p %(port)d < /dev/null > /dev/null 2>&1 && '
        '( sh -c \'%(sampler)s\' > %(D)s/samples 2>&1 < /dev/null & echo $! > %(D)s/sampler.pid ) && '
        '%(T)s/wg pubkey < %(D)s/key && cut -d" " -f1 /proc/uptime'
        % {'T': T, 'D': D, 'ppub': ppub, 'peer': PEER_TUN, 'bt': BOARD_TUN, 'port': PORT, 'sampler': sampler}).split()
    host_sync = time.monotonic()
    bpub, board_sync = bpub[0], float(bpub[1])
    R['clock_sync'] = {'host_monotonic': host_sync, 'board_uptime': board_sync}
    peer('ip link add wg-stage type wireguard && wg set wg-stage private-key /tmp/key peer %s endpoint %s:51873 '
         'allowed-ips %s/32 persistent-keepalive 1 && ip addr add %s/30 dev wg-stage && ip link set wg-stage up'
         % (bpub, A.board, BOARD_TUN, PEER_TUN))
    deadline = time.monotonic() + 30
    while ' 0% packet loss' not in peer('ping -c 1 -W 2 %s' % BOARD_TUN):
        if time.monotonic() > deadline:
            raise RuntimeError('tunnel did not come up')
    R['mtu'] = peer('cat /sys/class/net/wg-stage/mtu').strip()
    tweaks = []
    if A.gso_max_size:
        board('cat > %s/ip && chmod 700 %s/ip' % (D, D), data=A.ip.read_bytes())
        tweaks.append('%s/ip link set dev wgstage gso_max_size %d' % (D, A.gso_max_size))
    if A.threaded:
        tweaks.append('echo 1 > /sys/class/net/wgstage/threaded')
    if tweaks:
        board(' && '.join(tweaks))
    R['tweaks'] = {'gso_max_size': A.gso_max_size, 'threaded': A.threaded,
                   'state': board('cat /sys/class/net/wgstage/threaded /sys/class/net/wgstage/gso_max_size 2>&1; '
                                  "grep '^Udp:' /proc/net/snmp")}

    if A.capture:
        host('docker exec -d %s tcpdump -i wg-stage -s 96 -w /tmp/capture.pcap tcp port %d' % (NAME, PORT))
        host('docker exec -d %s tcpdump -i eth0 -s 64 -w /tmp/outer.pcap udp port 51873' % NAME)
        time.sleep(2)
    R['ping_idle'] = ping_stats(peer('ping -c 30 -i 0.2 %s' % BOARD_TUN, timeout=60))
    iperf('', 5)  # warm-up, not recorded

    for i in range(A.tcp_runs):
        run('tcp', 'to_board', '', A.tcp_seconds, {'rep': i + 1})
        run('tcp', 'from_board', '-R', A.tcp_seconds, {'rep': i + 1})
    # UDP: board sending as fast as it can; board receiving at a rate near its
    # capacity (loss shows the margin) and under a 200 Mbit/s flood (overload).
    for size, near in ((64, '0.4M'), (1380, '10M')):
        for i in range(A.udp_runs):
            run('udp', 'from_board', '-R -u -b 200M -l %d' % size, A.udp_seconds, {'rep': i + 1})
            run('udp', 'to_board', '-u -b %s -l %d' % (near, size), A.udp_seconds, {'rep': i + 1, 'load': 'near'})
            if not A.skip_flood:
                run('udp', 'to_board', '-u -b 200M -l %d' % size, A.udp_seconds, {'rep': i + 1, 'load': 'flood'})

    # Latency under load: ping while a TCP-to-board run is going.
    pinger = subprocess.Popen(host_argv("sleep 5; docker exec %s ping -c 60 -i 0.25 %s" % (NAME, BOARD_TUN)),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    run('tcp', 'to_board', '', 25, {'rep': 'with_ping'})
    R['ping_loaded'] = ping_stats(pinger.communicate(timeout=60)[0])
    R['success'] = True
except Exception as error:
    R['success'] = False
    R['error'] = str(error)
    print('FAILED: %s' % error, flush=True)
finally:
    steps = []
    if A.capture and state['ctr']:
        def save_capture():
            peer('pkill -INT -x tcpdump; sleep 2')
            if A.host == 'local':
                host('docker cp %s:/tmp/capture.pcap %s && docker cp %s:/tmp/outer.pcap %s' % (
                    NAME, A.capture, NAME, A.capture.with_suffix('.outer.pcap')))
                return
            host('docker cp %s:/tmp/capture.pcap /tmp/wgab-capture.pcap && '
                 'docker cp %s:/tmp/outer.pcap /tmp/wgab-outer.pcap' % (NAME, NAME))
            subprocess.run(['scp', '-q', '%s:/tmp/wgab-capture.pcap' % A.host, str(A.capture)], check=True)
            subprocess.run(['scp', '-q', '%s:/tmp/wgab-outer.pcap' % A.host,
                            str(A.capture.with_suffix('.outer.pcap'))], check=True)
            host('rm -f /tmp/wgab-capture.pcap /tmp/wgab-outer.pcap')
        steps.append(('capture saved', save_capture))
    if state['dir']:
        steps.append(('board sampler, iperf3 stopped; samples fetched', lambda: R.__setitem__('samples', board(
            'kill $(cat %s/sampler.pid 2>/dev/null) 2>/dev/null; killall iperf3 2>/dev/null; cat %s/samples 2>/dev/null; true' % (D, D)))))
    if state['iface']:
        steps.append(('board interface removed', lambda: board('test ! -e /sys/class/net/wgstage || %s/ip link del wgstage' % T)))
    if state['dir']:
        steps.append(('board RAM dir and key removed', lambda: board('rm -rf %s' % D)))
    if state['ctr']:
        steps.append(('host container removed', lambda: host('docker rm -f %s' % NAME)))
    for label, action in steps:
        try:
            action()
            R['cleanup'].append({'step': label, 'done': True})
        except Exception as e:
            R['cleanup'].append({'step': label, 'done': False, 'error': str(e)})
    A.results.parent.mkdir(parents=True, exist_ok=True)
    A.results.write_text(json.dumps(R, indent=1) + '\n')
    print('results: %s' % A.results, flush=True)
