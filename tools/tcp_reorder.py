#!/usr/bin/env python3
"""Classify TCP data arriving at the receiver: in order, out of order (a hole
later filled), or duplicate (bytes already received). Reads a pcap of raw IP
or Ethernet frames; no third-party modules."""
import struct
import sys
from collections import defaultdict


def packets(path):
    data = open(path, 'rb').read()
    magic = struct.unpack('<I', data[:4])[0]
    end = '<' if magic in (0xa1b2c3d4, 0xa1b23c4d) else '>'
    nano = magic in (0xa1b23c4d, 0x4d3cb2a1)
    linktype = struct.unpack(end + 'I', data[20:24])[0]
    off = 24
    while off + 16 <= len(data):
        sec, frac, caplen, _ = struct.unpack(end + 'IIII', data[off:off + 16])
        frame = data[off + 16:off + 16 + caplen]
        off += 16 + caplen
        t = sec + frac / (1e9 if nano else 1e6)
        if linktype == 1:
            frame = frame[14:]
        elif linktype == 113:
            frame = frame[16:]
        elif linktype == 276:
            frame = frame[20:]
        if frame and frame[0] >> 4 == 4:
            yield t, frame


def main(path):
    flows = defaultdict(lambda: {'max_end': None, 'seen': set(), 'holes': {}, 'n': 0, 'bytes': 0,
                                 'ooo': 0, 'dup': 0, 'hole_ms': []})
    for t, ip in packets(path):
        ihl = (ip[0] & 15) * 4
        if ip[9] != 6 or len(ip) < ihl + 20:
            continue
        total = struct.unpack('>H', ip[2:4])[0]
        src, dst = '.'.join(map(str, ip[12:16])), '.'.join(map(str, ip[16:20]))
        tcp = ip[ihl:]
        sport, dport, seq = struct.unpack('>HHI', tcp[:8])
        doff = (tcp[12] >> 4) * 4
        length = total - ihl - doff
        if length <= 0:
            continue
        f = flows[(src, sport, dst, dport)]
        f['n'] += 1
        f['bytes'] += length
        if f['max_end'] is None:
            f['max_end'] = seq
        rel = (seq - f['max_end']) & 0xffffffff
        if seq in f['seen']:
            f['dup'] += 1
            continue
        f['seen'].add(seq)
        if rel == 0:
            f['max_end'] = (seq + length) & 0xffffffff
        elif rel < 0x80000000:
            f['holes'][f['max_end']] = t  # gap opened at the expected sequence
            f['max_end'] = (seq + length) & 0xffffffff
        else:
            f['ooo'] += 1
            for start, opened in list(f['holes'].items()):
                if start == seq:
                    f['hole_ms'].append((t - opened) * 1000)
                    del f['holes'][start]
    for key, f in flows.items():
        if f['n'] < 100:
            continue
        ms = sorted(f['hole_ms'])
        print('%s:%d -> %s:%d  segments %d  MB %.1f  out-of-order %d  duplicate %d  holes filled %d '
              '(median %.1f ms, max %.1f ms)  holes never filled %d' % (
                  key[0], key[1], key[2], key[3], f['n'], f['bytes'] / 1e6, f['ooo'], f['dup'], len(ms),
                  ms[len(ms) // 2] if ms else 0, ms[-1] if ms else 0, len(f['holes'])))


if __name__ == '__main__':
    main(sys.argv[1])
