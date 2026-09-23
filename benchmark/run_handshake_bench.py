#!/usr/bin/env python3
import os
import sys
import csv
import time
import socket
import subprocess
import argparse

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results')
TIMEOUT = 8


def _wait_port(host, port, seconds=5):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            s = socket.create_connection((host, port), timeout=1)
            s.close()
            return True
        except (ConnectionRefusedError, OSError, socket.timeout):
            time.sleep(0.1)
    return False


def _start_server(host, port, cert_path, key_path, ca_path, mode, config_dir=None):
    cmd = ['openssl', 's_server']
    env = os.environ.copy()
    if mode == 'pqc':
        cmd.extend(['-provider', 'oqsprovider', '-provider', 'default'])
        env['PATH'] = f'/opt/openssl-pqc/bin:' + env.get('PATH', '')
        env['LD_LIBRARY_PATH'] = f'/opt/openssl-pqc/lib64:/opt/openssl-pqc/lib:' + env.get('LD_LIBRARY_PATH', '')
        env.pop('LD_PRELOAD', None)
        if config_dir:
            env['OPENSSL_CONF'] = os.path.join(config_dir, 'openssl-pqc-runtime.cnf')
        else:
            env['OPENSSL_CONF'] = os.path.join(
                PROJECT_DIR, 'pki', 'openssl-pqc-runtime.cnf'
            )
    cmd.extend([
        '-accept', f'{host}:{port}',
        '-cert', cert_path,
        '-key', key_path,
        '-CAfile', ca_path,
        '-Verify', '1',
        '-quiet',
    ])
    if mode == 'pqc':
        cmd.extend(['-groups', 'X25519MLKEM768'])
    return subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
    )


def _do_handshake(host, port, cert_path, key_path, ca_path, mode, config_dir=None,
                  sess_in=None, sess_out=None):
    env = os.environ.copy()
    if mode == 'pqc':
        env['PATH'] = f'/opt/openssl-pqc/bin:' + env.get('PATH', '')
        env['LD_LIBRARY_PATH'] = f'/opt/openssl-pqc/lib64:/opt/openssl-pqc/lib:' + env.get('LD_LIBRARY_PATH', '')
        env.pop('LD_PRELOAD', None)
        if config_dir:
            env['OPENSSL_CONF'] = os.path.join(config_dir, 'openssl-pqc-runtime.cnf')
        else:
            env['OPENSSL_CONF'] = os.path.join(
                os.path.dirname(__file__), '..', 'pki', 'openssl-pqc-runtime.cnf'
            )

    cmd = ['openssl', 's_client']
    if mode == 'pqc':
        cmd.extend(['-provider', 'oqsprovider', '-provider', 'default'])
    cmd.extend([
        '-connect', f'{host}:{port}',
        '-cert', cert_path,
        '-key', key_path,
        '-CAfile', ca_path,
        '-verify', '1',
        '-quiet',
    ])
    if mode == 'pqc':
        cmd.extend(['-groups', 'X25519MLKEM768'])
    if sess_in:
        cmd.extend(['-sess_in', sess_in])
    if sess_out:
        cmd.extend(['-sess_out', sess_out])

    start = time.perf_counter()
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    proc.stdin.close()
    proc.wait()
    elapsed = (time.perf_counter() - start) * 1000
    return proc.returncode, elapsed


def run_benchmark(mode: str, config_dir: str | None = None, trials: int = 20,
                  out_dir: str | None = None):
    host = '127.0.0.1'
    port = 14431

    cert_dir = f'{PROJECT_DIR}/pki/certs/classical' if mode == 'classical' else f'{PROJECT_DIR}/pki/certs/pqc'
    ca_path = os.path.join(cert_dir, 'ca.crt')
    server_cert = os.path.join(cert_dir, 'server.crt')
    server_key = os.path.join(cert_dir, 'server.key')
    client_cert = os.path.join(cert_dir, 'client.crt')
    client_key = os.path.join(cert_dir, 'client.key')

    server_proc = _start_server(
        host=host, port=port,
        cert_path=server_cert, key_path=server_key, ca_path=ca_path,
        mode=mode, config_dir=config_dir,
    )

    if not _wait_port(host, port):
        server_proc.terminate()
        raise RuntimeError('Server did not start in time')

    results = []
    for trial in range(1, trials + 1):
        rc, elapsed = _do_handshake(
            host, port,
            client_cert, client_key, ca_path,
            mode, config_dir,
        )
        if rc == 0:
            results.append((trial, elapsed))
        else:
            print(f'  trial {trial}: failed (rc={rc})', file=sys.stderr)

    server_proc.terminate()
    server_proc.wait()

    out_dir = out_dir or RESULTS_DIR
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, f'handshake_{mode}.csv')
    with open(csv_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['trial_id', 'mode', 'ms'])
        for trial, elapsed in results:
            w.writerow([trial, mode, round(elapsed, 2)])

    if results:
        times = [r[1] for r in results]
        print(f'  {mode}: {len(times)} trials, median={sorted(times)[len(times)//2]:.2f}ms, '
              f'min={min(times):.2f}ms, max={max(times):.2f}ms')
    else:
        print(f'  {mode}: no successful trials')
    print(f'  -> {csv_path}')
    return csv_path


def run_resumption_benchmark(mode: str, config_dir: str | None = None,
                             trials: int = 20, sess=None, out_dir: str | None = None):
    """Benchmark full vs resumed handshakes against one long-lived s_server.

    `sess` is a path to a ticket file. Conn 1 primes the ticket (full handshake,
    writes the ticket). Each timed trial after uses `-sess_in` to resume.
    Returns CSV rows sorted so full and resumed interleave per trial.
    """
    host = '127.0.0.1'
    port = 14432

    cert_dir = f'{PROJECT_DIR}/pki/certs/classical' if mode == 'classical' else f'{PROJECT_DIR}/pki/certs/pqc'
    ca_path = os.path.join(cert_dir, 'ca.crt')
    server_cert = os.path.join(cert_dir, 'server.crt')
    server_key = os.path.join(cert_dir, 'server.key')
    client_cert = os.path.join(cert_dir, 'client.crt')
    client_key = os.path.join(cert_dir, 'client.key')

    sess_in = sess

    server_proc = _start_server(
        host=host, port=port,
        cert_path=server_cert, key_path=server_key, ca_path=ca_path,
        mode=mode, config_dir=config_dir,
    )

    if not _wait_port(host, port):
        server_proc.terminate()
        raise RuntimeError('Server did not start in time')

    # Prime a ticket once so the resumed trials have something to offer.
    rc_p, _ = _do_handshake(
        host, port, client_cert, client_key, ca_path, mode, config_dir,
        sess_out=sess_in,
    )
    primed = rc_p == 0 and sess_in and os.path.isfile(sess_in)
    if not primed:
        print(f'  {mode}: WARNING could not prime a session ticket '
              f'(rc={rc_p}), resumed trials will fall back to full handshakes',
              file=sys.stderr)

    results = []
    for trial in range(1, trials + 1):
        rc1, full_ms = _do_handshake(
            host, port, client_cert, client_key, ca_path, mode, config_dir,
        )
        results.append((trial, 'full', round(full_ms, 2)) if rc1 == 0 else (trial, 'full', 'err'))

        rc2, res_ms = _do_handshake(
            host, port, client_cert, client_key, ca_path, mode, config_dir,
            sess_in=sess_in,
        )
        results.append((trial, 'resumed', round(res_ms, 2)) if rc2 == 0 else (trial, 'resumed', 'err'))

    server_proc.terminate()
    server_proc.wait()

    # Honour --out-dir here too: without this a --resume run always wrote into
    # benchmark/results/, so the guard that keeps dry-runs off the committed
    # CSVs only worked for the non-resume path.
    out_dir = out_dir or RESULTS_DIR
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, f'handshake_resume_{mode}.csv')
    with open(csv_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['trial_id', 'mode', 'kind', 'ms'])
        for trial, kind, ms in results:
            w.writerow([trial, mode, kind, ms])

    summary = {}
    for kind in ('full', 'resumed'):
        times = [float(r[2]) for r in results if r[1] == kind and isinstance(r[2], (int, float))]
        if times:
            summary[kind] = (sorted(times)[len(times)//2], min(times), max(times))
            print(f'  {mode}/{kind}: n={len(times)} median={summary[kind][0]:.2f}ms '
                  f'min={summary[kind][1]:.2f}ms max={summary[kind][2]:.2f}ms')
        else:
            print(f'  {mode}/{kind}: no successful trials')
    if 'full' in summary and 'resumed' in summary and summary['full'][0]:
        speedup = summary['full'][0] / summary['resumed'][0] if summary['resumed'][0] else None
        if speedup:
            print(f'  {mode}: resumed handshake {speedup:.2f}x faster (median)')
    print(f'  -> {csv_path}')
    return csv_path


def main():
    parser = argparse.ArgumentParser(description='PQ-VPN Handshake Benchmark')
    parser.add_argument('--modes', nargs='+', default=['classical', 'pqc'],
                        help='Modes to benchmark (default: classical pqc)')
    parser.add_argument('--trials', type=int, default=20,
                        help='Number of trials per mode (default: 20)')
    parser.add_argument('--resume', action='store_true',
                        help='Benchmark full vs resumed handshakes instead')
    parser.add_argument('--out-dir', default=RESULTS_DIR,
                        help=f'Where to write CSVs (default: {RESULTS_DIR})')
    args = parser.parse_args()

    for mode in args.modes:
        if args.resume:
            print(f'Benchmarking full-vs-resumed handshake: {mode}')
            # The ticket lives beside the CSVs; create the directory first or
            # `s_client -sess_out` fails (rc=2) and every resumed trial falls
            # back to a full handshake, silently ruining the comparison.
            os.makedirs(args.out_dir, exist_ok=True)
            ticket = os.path.join(args.out_dir, f'{mode}.tick')
            try:
                os.remove(ticket)
            except FileNotFoundError:
                pass
            run_resumption_benchmark(mode, os.path.join(PROJECT_DIR, 'pki'),
                                     trials=args.trials, sess=ticket,
                                     out_dir=args.out_dir)
        else:
            print(f'Benchmarking handshake: {mode}')
            config_dir = os.path.join(PROJECT_DIR, 'pki')
            run_benchmark(mode, config_dir, trials=args.trials, out_dir=args.out_dir)


if __name__ == '__main__':
    main()
