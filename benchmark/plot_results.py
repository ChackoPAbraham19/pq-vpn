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

    print('Generating plots...')
    _plot_handshake(hs)
    _plot_latency(lat)
    _plot_throughput(tp)
    _plot_file_transfer(ft)
    print(f'Plots saved to: {OUT_DIR}')


if __name__ == '__main__':
    main()
