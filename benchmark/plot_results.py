#!/usr/bin/env python3
import os
import csv
import sys
import argparse
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
    data = {}
    if not os.path.exists(path):
        return data
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            data[row['mode']] = {
                'min': float(row['min_ms']),
                'avg': float(row['avg_ms']),
                'max': float(row['max_ms']),
                'mdev': float(row['mdev_ms']),
            }
    return data


def _read_throughput(path: str) -> dict:
    data = defaultdict(dict)
    if not os.path.exists(path):
        return data
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            data[row['mode']][row['protocol']] = float(row['mbps']) if row['mbps'] != '?' else 0
    return data


def _read_file_transfer(path: str) -> dict:
    data = {}
    if not os.path.exists(path):
        return data
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            data[row['mode']] = {
                'seconds': float(row['seconds']),
                'sha256_ok': row['sha256_ok'],
            }
    return data


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
        bars = ax.bar(x + i * width, vals, width, label=label, color=color, edgecolor='black')
        for bar, v in zip(bars, vals):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.3,
                    f'{v:.1f}', ha='center', va='bottom', fontsize=8)
    ax.set_xticks(x + width)
    ax.set_xticklabels(modes)
    ax.set_ylabel('RTT (ms)')
    ax.set_title('Ping Latency Benchmark')
    ax.legend()
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    plt.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, 'latency.png'), dpi=150)
    print(f'  latency.png')


def _plot_throughput(data: dict):
    if not data:
        return
    fig, ax = plt.subplots(figsize=(8, 5))
    modes = list(data.keys())
    x = np.arange(len(modes))
    width = 0.3
    for i, proto in enumerate(['tcp', 'udp']):
        vals = [data[m].get(proto, 0) for m in modes]
        bars = ax.bar(x + i * width, vals, width, label=proto.upper(),
                      color=['#4c72b0', '#dd8452'][i], edgecolor='black')
        for bar, v in zip(bars, vals):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 5,
                    f'{v:.1f}', ha='center', va='bottom', fontsize=8)
    ax.set_xticks(x + width / 2)
    ax.set_xticklabels(modes)
    ax.set_ylabel('Throughput (Mbps)')
    ax.set_title('Throughput Benchmark (iperf3)')
    ax.legend()
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    plt.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, 'throughput.png'), dpi=150)
    print(f'  throughput.png')


def _plot_file_transfer(data: dict):
    if not data:
        return
    fig, ax = plt.subplots(figsize=(8, 5))
    modes = list(data.keys())
    vals = [data[m]['seconds'] for m in modes]
    colors = ['#4c72b0', '#dd8452']
    bars = ax.bar(modes, vals, color=colors[:len(modes)], width=0.5, edgecolor='black')
    for bar, v in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.1,
                f'{v:.1f}s', ha='center', va='bottom')
    ax.set_ylabel('Transfer Time (s)')
    ax.set_title(f'File Transfer Benchmark ({modes[0].replace("_", "MB") if modes else "?"})')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    plt.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, 'file_transfer.png'), dpi=150)
    print(f'  file_transfer.png')


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

    ft = _read_file_transfer(os.path.join(args.dir, 'file_transfer_classical.csv'))
    ft2 = _read_file_transfer(os.path.join(args.dir, 'file_transfer_pqc.csv'))
    ft.update(ft2)

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
    _plot_file_transfer(ft)
    _plot_wan(wan_c, wan_p, wres_c, wres_p)
    _plot_handshake_bytes(hb)
    print(f'Plots saved to: {OUT_DIR}')


if __name__ == '__main__':
    main()
