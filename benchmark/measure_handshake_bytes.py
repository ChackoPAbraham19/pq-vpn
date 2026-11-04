#!/usr/bin/env python3
"""measure_handshake_bytes.py — how many bytes each TLS handshake puts on the wire.

The timing benchmarks say post-quantum TLS costs an extra round trip over a
WAN (`run_wan_handshake_bench.py`: +124 ms at 99 ms RTT). They cannot say
*why*. This script measures the sizes that any explanation has to work with,
by driving a real handshake under `openssl s_client -msg` and totalling the
handshake messages in each direction — full and resumed, both modes.

What it establishes:

  * whether a HelloRetryRequest occurs. It does not: the ClientHello already
    carries an X25519MLKEM768 key share, because both ends pass
    `-groups X25519MLKEM768` as their only group. An HRR would cost exactly
    one round trip and would have been the cheap explanation.
  * whether the flight exceeds TCP's initial congestion window. The PQC
    server's flight is ~15.8 KB against the Linux default of 10 x 1460 =
    14.6 KB, so it cannot be sent in one burst; the client's flight under
    mutual TLS is ~14.3 KB, at the same limit.
  * what resumption actually removes. Less than expected: a resumed PQC
    ClientHello is ~7.5 KB against ~1 KB classical, because the PSK identity
    is a 5.8 KB ticket, and the server still sends another 5.8 KB ticket.

Sizes are properties of the messages, not of the path, so loopback is the
right place to measure them.

    .venv/bin/python3 benchmark/measure_handshake_bytes.py

Writes benchmark/results/handshake_bytes.csv
(mode, phase, direction, message, bytes) and prints the summary table.
"""
import argparse
import csv
import os
import re
import subprocess
import sys
import threading
import tempfile
import time

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from benchmark.run_handshake_bench import _start_server, _wait_port, RESULTS_DIR

# `-msg` prints one line per record and per handshake message, e.g.
#   >>> TLS 1.3, Handshake [length 0631], ClientHello
# with >>> for sent (client -> server) and <<< for received.
MSG_RE = re.compile(
    r'(?P<dir>>>>|<<<) TLS [0-9.]+, Handshake \[length (?P<len>[0-9a-f]+)\], (?P<msg>\w+)'
)
# Standard Linux initial congestion window: 10 segments. Verified on the
# deployment VPS — its default route sets no initcwnd, so the default applies.
INITCWND_BYTES = 10 * 1460
# Longest we hold the connection open waiting for the TLS 1.3
# NewSessionTicket that -sess_out needs. Normally it arrives in well under a
# second and we stop waiting the moment it does.
TICKET_WAIT = 10.0


def _client_env(mode: str) -> dict:
    env = os.environ.copy()
    if mode == 'pqc':
        env['PATH'] = '/opt/openssl-pqc/bin:' + env.get('PATH', '')
        env['LD_LIBRARY_PATH'] = ('/opt/openssl-pqc/lib64:/opt/openssl-pqc/lib:'
                                  + env.get('LD_LIBRARY_PATH', ''))
        env.pop('LD_PRELOAD', None)
        env['OPENSSL_CONF'] = os.path.join(PROJECT_DIR, 'pki', 'openssl-pqc-runtime.cnf')
    return env


def _capture(host, port, cert, key, ca, mode, sess_in=None, sess_out=None):
    """One handshake under -msg. Returns (messages, reused).

    messages is a list of (direction, name, length). The client must stay
    connected briefly after the handshake or the TLS 1.3 NewSessionTicket —
    which is what -sess_out writes — never arrives.
    """
    cmd = ['openssl', 's_client']
    if mode == 'pqc':
        cmd.extend(['-provider', 'oqsprovider', '-provider', 'default'])
    cmd.extend([
        '-connect', f'{host}:{port}',
        '-cert', cert, '-key', key, '-CAfile', ca,
        '-verify', '1', '-msg',
    ])
    if mode == 'pqc':
        cmd.extend(['-groups', 'X25519MLKEM768'])
    if sess_in:
        cmd.extend(['-sess_in', sess_in])
    if sess_out:
        cmd.extend(['-sess_out', sess_out])

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=_client_env(mode))
    # Read continuously so we can tell when the ticket has landed. Closing
    # stdin immediately (what communicate() does) makes s_client quit before
    # the server's NewSessionTicket arrives, and -sess_out then writes
    # nothing; a fixed sleep instead is a race that fails intermittently for
    # PQC, whose two tickets are 5.8 KB each.
    chunks: list[bytes] = []
    reader = threading.Thread(
        target=lambda: chunks.extend(iter(lambda: proc.stdout.read(4096), b'')),
        daemon=True)
    reader.start()
    try:
        proc.stdin.write(b'GET / HTTP/1.0\r\n\r\n')
        proc.stdin.flush()
        deadline = time.time() + TICKET_WAIT
        while sess_out and time.time() < deadline:
            if b'NewSessionTicket' in b''.join(chunks):
                break
            time.sleep(0.1)
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    proc.stdin = None
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    reader.join(timeout=5)
    text = b''.join(chunks).decode(errors='replace')

    messages = [(m.group('dir'), m.group('msg'), int(m.group('len'), 16))
                for m in MSG_RE.finditer(text)]
    return messages, 'Reused, TLS' in text


def measure(mode: str, port: int) -> list[dict]:
    cert_dir = os.path.join(PROJECT_DIR, 'pki', 'certs',
                            'classical' if mode == 'classical' else 'pqc')
    ca = os.path.join(cert_dir, 'ca.crt')
    server = _start_server(
        host='127.0.0.1', port=port,
        cert_path=os.path.join(cert_dir, 'server.crt'),
        key_path=os.path.join(cert_dir, 'server.key'),
        ca_path=ca, mode=mode,
    )
    if not _wait_port('127.0.0.1', port):
        server.terminate()
        raise RuntimeError(f'{mode}: server did not start')

    rows = []
    tmp = tempfile.mkdtemp(prefix='pqvpn-bytes-')
    sess = os.path.join(tmp, 'session')
    try:
        # openssl only writes -sess_out when s_client exits, so whether the
        # ticket made it is not knowable until afterwards. Retry the priming
        # handshake rather than silently dropping the resumed row.
        for attempt in range(3):
            full, _ = _capture('127.0.0.1', port,
                               os.path.join(cert_dir, 'client.crt'),
                               os.path.join(cert_dir, 'client.key'),
                               ca, mode, sess_out=sess)
            if os.path.exists(sess):
                break
        rows += [{'mode': mode, 'phase': 'full', 'direction': d,
                  'message': n, 'bytes': b} for d, n, b in full]

        if os.path.exists(sess):
            time.sleep(0.3)
            res, reused = _capture('127.0.0.1', port,
                                   os.path.join(cert_dir, 'client.crt'),
                                   os.path.join(cert_dir, 'client.key'),
                                   ca, mode, sess_in=sess)
            if reused:
                rows += [{'mode': mode, 'phase': 'resumed', 'direction': d,
                          'message': n, 'bytes': b} for d, n, b in res]
            else:
                print(f'   {mode}: resumed handshake did not reuse the session; '
                      'skipping the resumed row', file=sys.stderr)
        else:
            print(f'   {mode}: no session ticket was written; '
                  'skipping the resumed row', file=sys.stderr)
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        for f in (sess,):
            if os.path.exists(f):
                os.unlink(f)
        os.rmdir(tmp)
    return rows


def _flight(rows, mode, phase, direction, tickets=False):
    """Handshake bytes sent in one direction.

    NewSessionTicket is excluded by default: it is sent *after* the handshake
    completes, so it does not compete for the initial congestion window that
    the handshake flight has to fit into. Pass tickets=True to total those
    separately — they matter for resumption, where the ticket becomes the
    next ClientHello's PSK identity.
    """
    return sum(r['bytes'] for r in rows
               if r['mode'] == mode and r['phase'] == phase
               and r['direction'] == direction
               and (r['message'] == 'NewSessionTicket') == tickets)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out-dir', default=RESULTS_DIR)
    args = parser.parse_args()

    rows = []
    for mode, port in (('classical', 14451), ('pqc', 14452)):
        print(f'Measuring {mode} handshake bytes...')
        rows += measure(mode, port)

    os.makedirs(args.out_dir, exist_ok=True)
    out = os.path.join(args.out_dir, 'handshake_bytes.csv')
    with open(out, 'w', newline='') as fh:
        w = csv.DictWriter(fh, lineterminator='\n', fieldnames=['mode', 'phase', 'direction',
                                           'message', 'bytes'])
        w.writeheader()
        w.writerows(rows)

    hrr = [r for r in rows if r['message'] == 'HelloRetryRequest']
    print()
    print('Handshake messages over 1000 bytes (>>> client sent, <<< server sent):')
    for mode in ('classical', 'pqc'):
        for phase in ('full', 'resumed'):
            big = [f"{r['direction']}{r['message']}={r['bytes']}"
                   for r in rows
                   if r['mode'] == mode and r['phase'] == phase and r['bytes'] > 1000]
            print(f'  {mode:10s} {phase:8s} {", ".join(big) if big else "(none)"}')

    print()
    print(f'Handshake flight per direction, against initcwnd 10 x 1460 = '
          f'{INITCWND_BYTES} bytes:')
    for mode in ('classical', 'pqc'):
        for phase in ('full', 'resumed'):
            if not any(r['mode'] == mode and r['phase'] == phase for r in rows):
                continue
            s = _flight(rows, mode, phase, '>>>')
            r = _flight(rows, mode, phase, '<<<')
            tk = _flight(rows, mode, phase, '<<<', tickets=True)
            mark = '  <-- exceeds initcwnd' if max(s, r) > INITCWND_BYTES else ''
            extra = f'  (+{tk} B of tickets after)' if tk else ''
            print(f'  {mode:10s} {phase:8s} client->server {s:6d} B | '
                  f'server->client {r:6d} B{mark}{extra}')

    print()
    print(f'HelloRetryRequest: {"PRESENT" if hrr else "absent in every handshake"}')
    print(f'-> {out}')


if __name__ == '__main__':
    main()
