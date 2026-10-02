#!/usr/bin/env python3
"""Compare wg_ab_bench.py results: medians and ranges, board CPU and context
switches per tunnel packet from the 1 Hz board samples (edges trimmed)."""
import json
import statistics
import sys

TRIM = 3.0


def samples(text):
    out, cur = [], None
    for line in text.splitlines():
        f = line.split()
        if not f:
            continue
        if f[0] == 'U':
            cur = {'t': float(f[1])}
            out.append(cur)
        elif cur is None:
            continue
        elif f[0] == 'cpu':
            cur['cpu'] = [int(x) for x in f[1:8]]
        elif f[0] == 'ctxt':
            cur['ctxt'] = int(f[1])
        elif line.strip().startswith('wgstage:'):
            v = line.split(':', 1)[1].split()
            cur['wg_pkts'] = int(v[1]) + int(v[9])
    return [s for s in out if 'cpu' in s and 'ctxt' in s and 'wg_pkts' in s]


def window(run, sync, smp):
    t0 = sync['board_uptime'] + run['host_start'] - sync['host_monotonic'] + TRIM
    t1 = sync['board_uptime'] + run['host_end'] - sync['host_monotonic'] - TRIM
    inside = [s for s in smp if t0 <= s['t'] <= t1]
    if len(inside) < 2:
        return {}
    a, b = inside[0], inside[-1]
    d = [y - x for x, y in zip(a['cpu'], b['cpu'])]
    total = sum(d) or 1
    pk = b['wg_pkts'] - a['wg_pkts']
    return {'cpu_busy': 100 * (1 - (d[3] + d[4]) / total), 'sys': 100 * d[2] / total,
            'softirq': 100 * d[6] / total, 'user': 100 * d[0] / total,
            'ctxt_per_pkt': (b['ctxt'] - a['ctxt']) / pk if pk else None}


def summarise(path):
    r = json.load(open(path))
    smp = samples(r.get('samples', ''))
    groups = {}
    for run in r['runs']:
        if run.get('rep') == 'with_ping':
            continue
        key = (run['kind'], run['direction'], run['args'])
        value = run.get('mbit_s') if run['kind'] == 'tcp' else run.get('delivered_pps')
        if value is None:
            continue
        g = groups.setdefault(key, {'v': [], 'loss': [], 'retr': [], 'w': []})
        g['v'].append(value)
        if run.get('lost_percent') is not None:
            g['loss'].append(run['lost_percent'])
        if run.get('retransmits') is not None:
            g['retr'].append(run['retransmits'])
        g['w'].append(window(run, r['clock_sync'], smp))
    return r, groups


def fmt(g, unit):
    v = g['v']
    w = [x for x in g['w'] if x]
    cpu = statistics.median([x['cpu_busy'] for x in w]) if w else None
    cs = [x['ctxt_per_pkt'] for x in w if x.get('ctxt_per_pkt') is not None]
    extra = ''
    if g['loss']:
        extra += ' loss %.1f%%' % statistics.median(g['loss'])
    if g['retr']:
        extra += ' retr %d' % statistics.median(g['retr'])
    return '%s %s (%s–%s, n=%d)%s | CPU %s | ctxt/pkt %s' % (
        ('%.2f' if unit == 'Mbit/s' else '%d') % statistics.median(v), unit,
        ('%.2f' if unit == 'Mbit/s' else '%d') % min(v), ('%.2f' if unit == 'Mbit/s' else '%d') % max(v),
        len(v), extra, '%.0f%%' % cpu if cpu is not None else '–',
        '%.2f' % statistics.median(cs) if cs else '–')


def main():
    results = [summarise(p) for p in sys.argv[1:]]
    keys = sorted({k for _, g in results for k in g}, key=lambda k: (k[0] != 'tcp', k[2], k[1]))
    print('| Test | ' + ' | '.join(r['label'] for r, _ in results) + ' |')
    print('| --- |' + ' --- |' * len(results))
    for k in keys:
        unit = 'Mbit/s' if k[0] == 'tcp' else 'pps'
        name = '%s %s %s' % (k[0].upper(), k[1].replace('_', ' '), k[2].replace('-R ', '').strip())
        print('| %s | %s |' % (name, ' | '.join(fmt(g[k], unit) if k in g else '–' for _, g in results)))
    for r, _ in results:
        print('\n%s: ping idle %s, under load %s; %s' % (r['label'], r.get('ping_idle', {}).get('min_avg_max_ms'),
              r.get('ping_loaded', {}).get('min_avg_max_ms'), r.get('board_kernel', '')[:60]))


if __name__ == '__main__':
    main()
