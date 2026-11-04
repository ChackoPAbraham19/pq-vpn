#!/usr/bin/env python3
import os
import csv
import sys
import argparse
import statistics
from collections import defaultdict

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results')
OUT_DIR = os.path.join(RESULTS_DIR, 'plots')


def _read_handshake(path: str) -> dict:
    data = defaultdict(list)
    if not os.path.exists(path):
        return data
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            data[row['mode']].append(float(row['ms']))
    return data


def _read_resume(path: str) -> dict:
    data = defaultdict(list)
    if not os.path.exists(path):
        return data
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['ms'] == 'err':
                continue
            data[row['kind']].append(float(row['ms']))
    return data


def _read_latency(path: str) -> dict:
    """Pool repetitions: median of the per-run averages, with the true min/max.

    Each row is one independent ping run. Averaging the averages would hide a
    single bad run; the median does not, and min/max keep the extremes visible.
    """
    acc = defaultdict(lambda: {'min': [], 'avg': [], 'max': [], 'mdev': []})
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        for row in csv.DictReader(f):
            for k, col in (('min', 'min_ms'), ('avg', 'avg_ms'),
                           ('max', 'max_ms'), ('mdev', 'mdev_ms')):
                if row.get(col):
                    acc[row['mode']][k].append(float(row[col]))
    out = {}
    for mode, d in acc.items():
        if not d['avg']:
            continue
        out[mode] = {
            'min': min(d['min']) if d['min'] else 0.0,
            'avg': statistics.median(d['avg']),
            'max': max(d['max']) if d['max'] else 0.0,
            'mdev': statistics.median(d['mdev']) if d['mdev'] else 0.0,
            'avg_lo': min(d['avg']),
            'avg_hi': max(d['avg']),
            'n': len(d['avg']),
        }
    return out


def _read_throughput(path: str) -> dict:
    """{mode: {protocol: {median, lo, hi, loss, n}}}, pooling repetitions."""
    acc = defaultdict(lambda: defaultdict(lambda: {'mbps': [], 'loss': []}))
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        for row in csv.DictReader(f):
            v = row.get('mbps', '')
            if not v or v == '?':
                continue
            cell = acc[row['mode']][row['protocol']]
            cell['mbps'].append(float(v))
            if row.get('loss_pct'):
                cell['loss'].append(float(row['loss_pct']))
    out = defaultdict(dict)
    for mode, protos in acc.items():
        for proto, cell in protos.items():
            if not cell['mbps']:
                continue
            out[mode][proto] = {
                'median': statistics.median(cell['mbps']),
                'lo': min(cell['mbps']),
                'hi': max(cell['mbps']),
                'loss': statistics.median(cell['loss']) if cell['loss'] else None,
                'n': len(cell['mbps']),
            }
    return out


def _read_transfer_sweep(path: str) -> dict:
    """{size_bytes: [mbps, ...]} plus setup cost, from the multi-size CSV."""
    rate, setup = {}, {}
    if not os.path.exists(path):
        return {'rate': rate, 'setup': setup}
    with open(path) as f:
        for row in csv.DictReader(f):
            if row.get('sha256_ok') != 'yes' or not row.get('mbps'):
                continue
            size = int(row['size_bytes'])
            rate.setdefault(size, []).append(float(row['mbps']))
            if row.get('total_ms'):
                setup.setdefault(size, []).append(float(row['total_ms']))
    return {'rate': rate, 'setup': setup}


def _read_scaling(path: str) -> dict:
    """{test: {variant: [value, ...]}} from scaling_<mode>.csv, all repetitions."""
    out = defaultdict(lambda: defaultdict(list))
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for row in csv.DictReader(f):
            if not row.get('value'):
                continue
            try:
                out[row['test']][row['variant']].append(float(row['value']))
            except ValueError:
                pass
    return out


def _plot_handshake(data: dict):
    if not data:
        return
    fig, ax = plt.subplots(figsize=(8, 5))
    modes = list(data.keys())
    values = [np.median(data[m]) for m in modes]
    colors = ['#4c72b0', '#dd8452']
    bars = ax.bar(modes, values, color=colors[:len(modes)], width=0.5, edgecolor='black')
    for bar, v in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                f'{v:.1f}ms', ha='center', va='bottom')
    ax.set_ylabel('Median Handshake Time (ms)')
    ax.set_title('TLS Handshake Benchmark')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    plt.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, 'handshake.png'), dpi=150)
    print(f'  handshake.png')


def _plot_resume(data: dict):
    if not data:
        return
    fig, ax = plt.subplots(figsize=(8, 5))
    kinds = ['full', 'resumed']
    kinds = [k for k in kinds if data.get(k)]
    values = [np.median(data[k]) for k in kinds]
    colors = ['#4c72b0', '#55a868']
    bars = ax.bar(kinds, values, color=colors[:len(kinds)], width=0.5, edgecolor='black')
    for bar, v in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                f'{v:.1f}ms', ha='center', va='bottom')
    ax.set_ylabel('Median Handshake Time (ms)')
    ax.set_title('Session Resumption: Full vs Resumed Handshake')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    plt.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, 'handshake_resume.png'), dpi=150)
    print(f'  handshake_resume.png')


def _plot_latency(data: dict):
    if not data:
        return
    fig, ax = plt.subplots(figsize=(8, 5))
    modes = list(data.keys())
    x = np.arange(len(modes))
    width = 0.25
    metrics = [
        ('min', [data[m]['min'] for m in modes], '#4c72b0'),
        ('avg', [data[m]['avg'] for m in modes], '#dd8452'),
        ('max', [data[m]['max'] for m in modes], '#c44e52'),
    ]
    for i, (label, vals, color) in enumerate(metrics):
        # The avg bar carries a whisker across repetitions; min and max are
        # already extremes, so an error bar on them would be meaningless.
        err = None
        if label == 'avg' and all('avg_lo' in data[m] for m in modes):
            err = [[data[m]['avg'] - data[m]['avg_lo'] for m in modes],
                   [data[m]['avg_hi'] - data[m]['avg'] for m in modes]]
        bars = ax.bar(x + i * width, vals, width, label=label, color=color,
                      edgecolor='black', yerr=err, capsize=4, ecolor='#333333')
        for bar, v in zip(bars, vals):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.3,
                    f'{v:.1f}', ha='center', va='bottom', fontsize=8)
    ax.set_xticks(x + width)
    ax.set_xticklabels(modes)
    ax.set_ylabel('RTT (ms)')
    ax.margins(y=0.12)   # headroom, or the tallest bar's label is clipped
    n = max((data[m].get('n', 1) for m in modes), default=1)
    ax.set_title(f'Ping latency, idle tunnel (median of {n} runs x 100 pings)')
    ax.legend()
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    plt.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, 'latency.png'), dpi=150)
    print(f'  latency.png')


def _plot_throughput(data: dict):
    if not data:
        return
    fig, ax = plt.subplots(figsize=(9, 5))
    modes = list(data.keys())
    x = np.arange(len(modes))
    # Only plot protocols actually present, so an older 2-column CSV still works.
    order = [('tcp', 'TCP, 1 stream', '#4c72b0'),
             ('udp_1000m', 'UDP, 1 Gbps offered', '#dd8452'),
             ('udp_max', 'UDP, line rate (-b 0)', '#c44e52'),
             ('udp', 'UDP', '#dd8452')]
    protos = [t for t in order if any(t[0] in data[m] for m in modes)]
    if ('udp_1000m', 'UDP, 1 Gbps offered', '#dd8452') in protos:
        protos = [t for t in protos if t[0] != 'udp']
    width = 0.8 / max(len(protos), 1)
    for i, (key, label, color) in enumerate(protos):
        vals, err_lo, err_hi = [], [], []
        for m in modes:
            cell = data[m].get(key)
            v = cell['median'] if cell else 0.0
            vals.append(v)
            err_lo.append(v - cell['lo'] if cell else 0.0)
            err_hi.append(cell['hi'] - v if cell else 0.0)
        bars = ax.bar(x + i * width, vals, width, label=label, color=color,
                      edgecolor='black', yerr=[err_lo, err_hi], capsize=4,
                      ecolor='#333333')
        for bar, v, m in zip(bars, vals, modes):
            cell = data[m].get(key)
            # A UDP rate without its loss is not interpretable, so print both.
            txt = f'{v:.0f}'
            if cell and cell.get('loss') is not None:
                txt += f'\n{cell["loss"]:.0f}% lost'
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 8,
                    txt, ha='center', va='bottom', fontsize=7.5)
    ax.set_xticks(x + width * (len(protos) - 1) / 2)
    ax.set_xticklabels(modes)
    ax.set_ylabel('Throughput (Mbps)')
    n = max((c['n'] for m in modes for c in data[m].values()), default=1)
    ax.set_title(f'Throughput through the tunnel (median of {n} runs, bars show min-max)')
    ax.legend()
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    plt.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, 'throughput.png'), dpi=150)
    print(f'  throughput.png')


def _plot_transfer_sweep(by_mode: dict):
    """Throughput against transfer size, log x, with the min-max band.

    One measure across a range, so a line: the question is the shape of the
    curve and whether the two modes converge once the transfer is big enough
    for the handshake to stop mattering.
    """
    if not any(d['rate'] for d in by_mode.values()):
        return
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    colors = {'classical': '#4c72b0', 'pqc': '#dd8452'}
    for mode, d in by_mode.items():
        if not d['rate']:
            continue
        sizes = sorted(d['rate'])
        med = [np.median(d['rate'][x]) for x in sizes]
        lo = [min(d['rate'][x]) for x in sizes]
        hi = [max(d['rate'][x]) for x in sizes]
        c = colors.get(mode, '#555555')
        ax1.plot(sizes, med, marker='o', color=c, label=mode)
        ax1.fill_between(sizes, lo, hi, color=c, alpha=0.15)
        if d['setup']:
            ssz = sorted(d['setup'])
            ax2.plot(ssz, [np.median(d['setup'][x]) for x in ssz],
                     marker='o', color=c, label=mode)
    for ax in (ax1, ax2):
        ax.set_xscale('log')
        ax.set_xlabel('Transfer size (bytes)')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.grid(alpha=0.25)
        ax.legend(frameon=False)
    ax1.set_ylabel('Throughput (Mbps)')
    ax1.set_title('Sustained rate rises until the relay caps it')
    ax2.set_yscale('log')
    ax2.set_ylabel('Total time (ms)')
    ax2.set_title('Below ~100 KB, fixed per-transfer cost dominates')
    fig.suptitle('Transfer through the tunnel, by size (median, min-max band)',
                 fontweight='bold')
    plt.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, 'transfer_sweep.png'), dpi=150)
    print('  transfer_sweep.png')


def _clients_verdict(has: dict) -> str:
    """Same, for the concurrent-client series."""
    ends = []
    for d in has.values():
        cl = d.get('clients', {})
        if '1' in cl and '3' in cl:
            ends.append(statistics.median(cl['3']) / statistics.median(cl['1']))
    if not ends:
        return 'Total throughput vs concurrent clients'
    r = statistics.median(ends)
    if r < 0.85:
        return f'Total throughput FALLS as clients are added ({r:.2f}x at 3)'
    if r > 1.15:
        return f'Total throughput rises with clients ({r:.1f}x at 3)'
    return 'Total throughput is flat as clients are added'


def _streams_verdict(has: dict) -> str:
    """Say what the parallel-stream series actually does, from the numbers."""
    ends = []
    for d in has.values():
        ps = d.get('parallel_streams', {})
        if '1' in ps and '8' in ps:
            ends.append(statistics.median(ps['8']) / statistics.median(ps['1']))
    if not ends:
        return 'Throughput vs parallel TCP streams'
    r = statistics.median(ends)
    if r > 1.15:
        return f'Parallel streams raise the ceiling ({r:.1f}x from 1 to 8)'
    if r < 0.85:
        return f'Parallel streams lower the ceiling ({r:.2f}x from 1 to 8)'
    return 'A single stream already saturates the relay'


def _plot_scaling(by_mode: dict):
    """Parallel streams and concurrent clients: does anything raise the ceiling?"""
    has = {k: v for k, v in by_mode.items() if v}
    if not has:
        return
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.6))
    colors = {'classical': '#4c72b0', 'pqc': '#dd8452'}
    drew = False
    def series(ax, points, c, mode):
        """Median line with a min-max band across repetitions."""
        xs = sorted(points, key=lambda v: int(v))
        xi = [int(x) for x in xs]
        med = [statistics.median(points[x]) for x in xs]
        ax.plot(xi, med, marker='o', color=c, label=mode)
        lo = [min(points[x]) for x in xs]
        hi = [max(points[x]) for x in xs]
        if any(h > l for l, h in zip(lo, hi)):
            ax.fill_between(xi, lo, hi, color=c, alpha=0.15, linewidth=0)

    for mode, d in has.items():
        c = colors.get(mode, '#555555')
        ps = d.get('parallel_streams', {})
        if ps:
            series(ax1, ps, c, mode)
            drew = True
        cl = d.get('clients', {})
        if cl:
            series(ax2, cl, c, mode)
            drew = True
    if not drew:
        plt.close(fig)
        return
    ax1.set_xlabel('Parallel TCP streams')
    # Title derived from the data, not asserted. An earlier run showed 759 ->
    # 1010 Mbps from 1 to 8 streams and the title said so; a later run with
    # repetitions showed the series flat, and a hardcoded title would have
    # kept claiming a rise the plot underneath it no longer showed.
    ax1.set_title(_streams_verdict(has))
    ax1.set_xticks([1, 2, 4, 8])
    ax2.set_xlabel('Concurrent clients')
    ax2.set_title(_clients_verdict(has))
    ax2.set_xticks([1, 2, 3])
    for ax in (ax1, ax2):
        ax.set_ylabel('Total throughput (Mbps)')
        ax.set_ylim(bottom=0)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.grid(alpha=0.25)
        ax.legend(frameon=False)
    plt.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, 'scaling.png'), dpi=150)
    print('  scaling.png')


def _read_wan(path: str) -> list:
    """Per-trial ms from a WAN handshake CSV (trial_id, mode, ms)."""
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [float(r['ms']) for r in csv.DictReader(f) if r['ms'] != 'err']


def _read_wan_resume(path: str) -> dict:
    data = defaultdict(list)
    if not os.path.exists(path):
        return data
    with open(path) as f:
        for row in csv.DictReader(f):
            if row['ms'] == 'err':
                continue
            data[row['kind']].append(float(row['ms']))
    return data


def _read_bytes(path: str) -> dict:
    """Handshake bytes per mode/phase/direction, tickets excluded.

    NewSessionTicket is sent after the handshake completes, so it does not
    compete for the initial congestion window the flight has to fit into.
    """
    data = defaultdict(int)
    if not os.path.exists(path):
        return data
    with open(path) as f:
        for row in csv.DictReader(f):
            if row['message'] == 'NewSessionTicket':
                continue
            data[(row['mode'], row['phase'], row['direction'])] += int(row['bytes'])
    return data


def _plot_wan(full_c: list, full_p: list, res_c: dict, res_p: dict):
    """The headline result, as two panels — the runs had different RTTs
    (99.2 ms and 51.6 ms), so their bars must not be read against each other."""
    if not full_c or not full_p:
        return
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 5))
    colors = ['#4c72b0', '#dd8452']

    vals = [np.median(full_c), np.median(full_p)]
    bars = ax1.bar(['classical', 'pqc'], vals, color=colors, width=0.5, edgecolor='black')
    for bar, v in zip(bars, vals):
        ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 6,
                 f'{v:.0f}ms', ha='center', va='bottom')
    ax1.axhline(99.2, color='grey', linestyle=':', linewidth=1)
    ax1.text(1.45, 103, '1 RTT', color='grey', ha='right', fontsize=8)
    ax1.axhline(198.4, color='grey', linestyle=':', linewidth=1)
    ax1.text(1.45, 202, '2 RTT', color='grey', ha='right', fontsize=8)
    ax1.axhline(297.6, color='grey', linestyle=':', linewidth=1)
    ax1.text(1.45, 301, '3 RTT', color='grey', ha='right', fontsize=8)
    ax1.set_ylabel('Median handshake (ms)')
    ax1.set_title('Full handshake, 25 trials (RTT 99.2 ms)')

    if res_c.get('full') and res_p.get('full'):
        labels = ['classical\nfull', 'classical\nresumed', 'pqc\nfull', 'pqc\nresumed']
        vals2 = [np.median(res_c['full']), np.median(res_c['resumed']),
                 np.median(res_p['full']), np.median(res_p['resumed'])]
        bars2 = ax2.bar(labels, vals2, width=0.6, edgecolor='black',
                        color=[colors[0], colors[0], colors[1], colors[1]],
                        alpha=1.0)
        for bar, v in zip(bars2, vals2):
            ax2.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 4,
                     f'{v:.0f}ms', ha='center', va='bottom')
        ax2.set_ylabel('Median handshake (ms)')
        ax2.set_title('Full vs resumed, 20 trials (RTT 51.6 ms)')

    for ax in (ax1, ax2):
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
    fig.suptitle('WAN handshake: laptop to Mumbai VPS', fontweight='bold')
    plt.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, 'wan_handshake.png'), dpi=150)
    print('  wan_handshake.png')


def _plot_handshake_bytes(data: dict):
    """Bytes on the wire per flight, against the initial congestion window."""
    if not data:
        return
    initcwnd = 10 * 1460
    rows = [('full', '>>>', 'Full: client to server'),
            ('full', '<<<', 'Full: server to client'),
            ('resumed', '>>>', 'Resumed: client to server'),
            ('resumed', '<<<', 'Resumed: server to client')]
    rows = [r for r in rows if data.get(('pqc', r[0], r[1]))]
    if not rows:
        return
    labels = [r[2] for r in rows]
    cls = [data.get(('classical', r[0], r[1]), 0) for r in rows]
    pqc = [data.get(('pqc', r[0], r[1]), 0) for r in rows]

    y = np.arange(len(rows))
    fig, ax = plt.subplots(figsize=(9, 4.6))
    ax.barh(y + 0.19, cls, height=0.36, color='#4c72b0', edgecolor='black', label='classical')
    ax.barh(y - 0.19, pqc, height=0.36, color='#dd8452', edgecolor='black', label='pqc')
    for i, v in enumerate(cls):
        ax.text(v + 250, i + 0.19, f'{v:,}', va='center', fontsize=8)
    for i, v in enumerate(pqc):
        ax.text(v + 250, i - 0.19, f'{v:,}', va='center', fontsize=8)
    ax.axvline(initcwnd, color='crimson', linestyle='--', linewidth=1.2)
    ax.text(initcwnd + 250, len(rows) - 0.45,
            f'initcwnd 10 x 1460 = {initcwnd:,} B', color='crimson', fontsize=8)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlabel('Handshake bytes on the wire (tickets excluded)')
    ax.set_title('Only the PQC flight exceeds the initial congestion window')
    ax.set_xlim(0, max(max(pqc), initcwnd) * 1.18)
    ax.legend(frameon=False)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    plt.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, 'handshake_bytes.png'), dpi=150)
    print('  handshake_bytes.png')


def main():
    parser = argparse.ArgumentParser(description='PQ-VPN Benchmark Plotter')
    parser.add_argument('--dir', default=RESULTS_DIR,
                        help=f'Results directory (default: {RESULTS_DIR})')
    args = parser.parse_args()

    # Plots belong next to the CSVs they were made from. Without this, --dir
    # read one directory and wrote into another -- so a --quick run would
    # silently refresh the PUBLISHED plots from unrelated data.
    global OUT_DIR
    OUT_DIR = os.path.join(os.path.abspath(args.dir), 'plots')
    os.makedirs(OUT_DIR, exist_ok=True)

    print('Reading results...')
    hs = _read_handshake(os.path.join(args.dir, 'handshake_classical.csv'))
    hs2 = _read_handshake(os.path.join(args.dir, 'handshake_pqc.csv'))
    hs.update(hs2)

    lat = _read_latency(os.path.join(args.dir, 'latency_classical.csv'))
    lat2 = _read_latency(os.path.join(args.dir, 'latency_pqc.csv'))
    lat.update(lat2)

    tp = _read_throughput(os.path.join(args.dir, 'throughput_classical.csv'))
    tp2 = _read_throughput(os.path.join(args.dir, 'throughput_pqc.csv'))
    tp.update(tp2)

    ft = {m: _read_transfer_sweep(os.path.join(args.dir, f'file_transfer_{m}.csv'))
          for m in ('classical', 'pqc')}
    sc = {m: _read_scaling(os.path.join(args.dir, f'scaling_{m}.csv'))
          for m in ('classical', 'pqc')}

    rs = _read_resume(os.path.join(args.dir, 'handshake_resume_classical.csv'))
    rs2 = _read_resume(os.path.join(args.dir, 'handshake_resume_pqc.csv'))
    rs.update(rs2)

    wan_c = _read_wan(os.path.join(args.dir, 'wan_handshake_classical.csv'))
    wan_p = _read_wan(os.path.join(args.dir, 'wan_handshake_pqc.csv'))
    wres_c = _read_wan_resume(os.path.join(args.dir, 'wan_handshake_resume_classical.csv'))
    wres_p = _read_wan_resume(os.path.join(args.dir, 'wan_handshake_resume_pqc.csv'))
    hb = _read_bytes(os.path.join(args.dir, 'handshake_bytes.csv'))

    print('Generating plots...')
    _plot_handshake(hs)
    _plot_resume(rs)
    _plot_latency(lat)
    _plot_throughput(tp)
    _plot_transfer_sweep(ft)
    _plot_scaling(sc)
    _plot_wan(wan_c, wan_p, wres_c, wres_p)
    _plot_handshake_bytes(hb)
    print(f'Plots saved to: {OUT_DIR}')


if __name__ == '__main__':
    main()
