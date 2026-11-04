#!/usr/bin/env python3
"""run_wan_handshake_bench.py — TLS handshake cost over the real internet.

The loopback benchmark (`run_handshake_bench.py`) isolates the *computational*
cost of post-quantum TLS: no network latency, so the larger ML-DSA-65
certificates and X25519MLKEM768 key share show up as CPU time (~+12 ms, 1.8x).

The intuition that a WAN would dilute this is **wrong**, and measuring it is
the point of this script: against a Mumbai VPS at ~99 ms RTT, PQC cost +124 ms
rather than +12 ms — about one extra round trip, not extra compute.

What causes that round trip is not settled here. `measure_handshake_bytes.py`
shows the PQC handshake flight is 16 001 B against a 14 600 B initial
congestion window, which is consistent with it, but no packet capture confirms
it. Session resumption recovers part of the cost (PQC 1.36x on a WAN against
1.08x on loopback) yet leaves PQC ~47 ms slower than classical even resumed,
because a resumed PQC ClientHello still carries a 5.8 KB ticket. See
docs/PROJECT_REPORT.md §13.

It starts a plain `openssl s_server` on the VPS over SSH, times `s_client`
handshakes from this machine against it, and tears the server down. The VPN
daemon is not involved — this measures TLS alone, the same way the loopback
benchmark does, so the two are directly comparable.

    bash scripts/deploy_vps.sh ubuntu@<ip>      # certs must already be there
    .venv/bin/python3 benchmark/run_wan_handshake_bench.py ubuntu@<ip>

Writes benchmark/results/wan_handshake_{mode}.csv (trial_id, mode, ms) and
prints medians, the PQC/classical ratio, and the measured TCP round-trip time
for context.
"""
import argparse
import csv
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from benchmark.run_handshake_bench import _do_handshake, _wait_port, RESULTS_DIR

REMOTE_DIR = '/opt/pqvpn'


def _ssh(target, command, check=True, timeout=60):
    r = subprocess.run(['ssh', '-o', 'ConnectTimeout=10', target, command],
                       capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f'ssh failed ({r.returncode}): {r.stderr.strip()[:300]}')
    return r


def _remote_server_cmd(mode: str, port: int, naccept: int) -> str:
    """The s_server invocation to run on the VPS, mode-appropriate."""
    cert_dir = f'{REMOTE_DIR}/pki/certs/{"pqc" if mode == "pqc" else "classical"}'
    if mode == 'pqc':
        env = (f'PATH=/opt/openssl-pqc/bin:$PATH '
               f'LD_LIBRARY_PATH=/opt/openssl-pqc/lib64:/opt/openssl-pqc/lib '
               f'OPENSSL_CONF={REMOTE_DIR}/pki/openssl-pqc-runtime.cnf ')
        extra = '-provider oqsprovider -provider default -groups X25519MLKEM768 '
    else:
        env, extra = '', ''
    return (f'{env}nohup setsid openssl s_server {extra}'
            f'-accept 0.0.0.0:{port} '
            f'-cert {cert_dir}/server.crt -key {cert_dir}/server.key '
            f'-CAfile {cert_dir}/ca.crt -Verify 1 -quiet '
            f'-naccept {naccept} > /tmp/wanbench_{mode}.log 2>&1 < /dev/null & echo started')


def _tcp_rtt(host: str, port: int, samples: int = 10) -> float | None:
    """Median time to complete a TCP connect — the network floor a handshake pays."""
    import socket
    times = []
    for _ in range(samples):
        t0 = time.perf_counter()
        try:
            s = socket.create_connection((host, port), timeout=5)
            s.close()
            times.append((time.perf_counter() - t0) * 1000)
        except OSError:
            pass
        time.sleep(0.05)
    return statistics.median(times) if times else None


def run_resume_mode(target: str, host: str, mode: str, trials: int, port: int,
                    out_dir: str) -> dict | None:
    """Full vs resumed handshakes over the WAN against one long-lived s_server.

    Resumption skips the certificate exchange, so if the PQC penalty really is
    an extra round trip caused by the size of that exchange, resuming should
    recover it — that is the hypothesis this measures.
    """
    naccept = 2 * trials + 5
    _ssh(target, f'pkill -f "s_server.*:{port}" 2>/dev/null; true', check=False)
    _ssh(target, _remote_server_cmd(mode, port, naccept))
    if not _wait_port(host, port, seconds=15):
        _ssh(target, f'pkill -f "s_server.*:{port}"', check=False)
        print(f'  {mode}: remote s_server never opened {port}', file=sys.stderr)
        return None

    cert_dir = os.path.join(PROJECT_DIR, 'pki', 'certs',
                            'pqc' if mode == 'pqc' else 'classical')
    ca = os.path.join(cert_dir, 'ca.crt')
    crt = os.path.join(cert_dir, 'client.crt')
    key = os.path.join(cert_dir, 'client.key')
    config_dir = os.path.join(PROJECT_DIR, 'pki')

    os.makedirs(out_dir, exist_ok=True)
    # The ticket is key material, not a result: keep it out of out_dir (which is
    # committed) and delete it when the run ends. Two of these once reached the
    # public repo from here via `git add -A`.
    ticket_dir = tempfile.mkdtemp(prefix='pqvpn-wanbench-')
    ticket = os.path.join(ticket_dir, f'wan_{mode}.tick')

    try:
        rc_p, _ = _do_handshake(host, port, crt, key, ca, mode, config_dir, sess_out=ticket)
        if rc_p != 0 or not os.path.isfile(ticket):
            print(f'  {mode}: could not prime a ticket (rc={rc_p})', file=sys.stderr)
            _ssh(target, f'pkill -f "s_server.*:{port}"', check=False)
            return None
        return _resume_trials(target, host, mode, trials, port, out_dir,
                              crt, key, ca, config_dir, ticket)
    finally:
        shutil.rmtree(ticket_dir, ignore_errors=True)


def _resume_trials(target, host, mode, trials, port, out_dir,
                   crt, key, ca, config_dir, ticket) -> dict | None:

    rows, times = [], {'full': [], 'resumed': []}
    for trial in range(1, trials + 1):
        rc1, ms1 = _do_handshake(host, port, crt, key, ca, mode, config_dir)
        rows.append((trial, mode, 'full', round(ms1, 2) if rc1 == 0 else 'err'))
        if rc1 == 0:
            times['full'].append(ms1)
        rc2, ms2 = _do_handshake(host, port, crt, key, ca, mode, config_dir, sess_in=ticket)
        rows.append((trial, mode, 'resumed', round(ms2, 2) if rc2 == 0 else 'err'))
        if rc2 == 0:
            times['resumed'].append(ms2)
        print(f'\r  {mode}: {trial}/{trials}', end='', file=sys.stderr, flush=True)
    print('', file=sys.stderr)

    _ssh(target, f'pkill -f "s_server.*:{port}" 2>/dev/null; true', check=False)
    if not times['full'] or not times['resumed']:
        print(f'  {mode}: incomplete resumption data', file=sys.stderr)
        return None

    path = os.path.join(out_dir, f'wan_handshake_resume_{mode}.csv')
    with open(path, 'w', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(['trial_id', 'mode', 'kind', 'ms'])
        w.writerows(rows)

    full_m = statistics.median(times['full'])
    res_m = statistics.median(times['resumed'])
    print(f'  {mode}: full median={full_m:.2f}ms  resumed median={res_m:.2f}ms  '
          f'saved {full_m - res_m:.1f}ms ({full_m / res_m:.2f}x)')
    print(f'  -> {path}')
    return dict(full=full_m, resumed=res_m, n=len(times['full']))


def run_mode(target: str, host: str, mode: str, trials: int, port: int,
             out_dir: str) -> dict | None:
    naccept = trials + 5  # a few spare accepts for retries
    _ssh(target, f'pkill -f "s_server.*:{port}" 2>/dev/null; true', check=False)
    _ssh(target, _remote_server_cmd(mode, port, naccept))
    if not _wait_port(host, port, seconds=15):
        _ssh(target, f'pkill -f "s_server.*:{port}"', check=False)
        print(f'  {mode}: remote s_server never opened {port} — '
              f'check the cloud firewall and /tmp/wanbench_{mode}.log', file=sys.stderr)
        return None

    cert_dir = os.path.join(PROJECT_DIR, 'pki', 'certs',
                            'pqc' if mode == 'pqc' else 'classical')
    ca = os.path.join(cert_dir, 'ca.crt')
    crt = os.path.join(cert_dir, 'client.crt')
    key = os.path.join(cert_dir, 'client.key')
    config_dir = os.path.join(PROJECT_DIR, 'pki')

    rows, times = [], []
    for trial in range(1, trials + 1):
        rc, ms = _do_handshake(host, port, crt, key, ca, mode, config_dir)
        if rc == 0:
            rows.append((trial, mode, round(ms, 2)))
            times.append(ms)
        else:
            rows.append((trial, mode, 'err'))
        print(f'\r  {mode}: {trial}/{trials}', end='', file=sys.stderr, flush=True)
    print('', file=sys.stderr)

    _ssh(target, f'pkill -f "s_server.*:{port}" 2>/dev/null; true', check=False)

    if not times:
        print(f'  {mode}: no successful handshakes', file=sys.stderr)
        return None

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f'wan_handshake_{mode}.csv')
    with open(path, 'w', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(['trial_id', 'mode', 'ms'])
        w.writerows(rows)

    st = dict(n=len(times), median=statistics.median(times),
              lo=min(times), hi=max(times), path=path)
    print(f'  {mode}: n={st["n"]} median={st["median"]:.2f}ms '
          f'min={st["lo"]:.2f}ms max={st["hi"]:.2f}ms')
    print(f'  -> {path}')
    return st


def main():
    ap = argparse.ArgumentParser(description='WAN TLS handshake benchmark against a deployed VPS')
    ap.add_argument('target', help='ssh target, e.g. ubuntu@203.0.113.5')
    ap.add_argument('--modes', nargs='+', default=['classical', 'pqc'])
    ap.add_argument('--trials', type=int, default=25)
    ap.add_argument('--port', type=int, default=14433,
                    help='port to test on; must be open in the cloud firewall '
                         'and idle (default: 14433, the last VPN data-port slot)')
    ap.add_argument('--out-dir', default=RESULTS_DIR)
    ap.add_argument('--resume', action='store_true',
                    help='measure full vs resumed handshakes instead')
    args = ap.parse_args()

    host = args.target.split('@')[-1]
    print(f'WAN handshake benchmark against {host}:{args.port}')

    rtt = _tcp_rtt(host, 22)
    if rtt:
        print(f'  TCP round-trip to the host: {rtt:.1f} ms (the floor any handshake pays)')

    results = {}
    for mode in args.modes:
        if args.resume:
            st = run_resume_mode(args.target, host, mode, args.trials, args.port, args.out_dir)
        else:
            st = run_mode(args.target, host, mode, args.trials, args.port, args.out_dir)
        if st:
            results[mode] = st

    if args.resume:
        if rtt:
            print(f'\n(one round trip on this path = {rtt:.1f} ms)')
        return

    if 'classical' in results and 'pqc' in results:
        c, p = results['classical']['median'], results['pqc']['median']
        print(f'\nPQC/classical = {p / c:.2f}x over the WAN '
              f'({p - c:+.1f} ms absolute)')
        if rtt:
            print(f'The extra {p - c:.1f} ms sits on top of a {rtt:.1f} ms '
                  f'round-trip floor; on loopback the same difference is the '
                  f'whole cost.')


if __name__ == '__main__':
    main()
