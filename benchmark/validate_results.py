#!/usr/bin/env python3
"""validate_results.py — check a benchmark sweep before anyone believes it.

Every bad run this project has had was silent. The IPC listener died and status
kept looking fine for four days; a line-splitting JSON parser reported nothing
and that was indistinguishable from a tunnel carrying nothing; --quick
overwrote a seven-size sweep with three sizes; a multi-client total came from
an unknown number of clients. In each case the data looked plausible and was
wrong.

So this runs at the end of every sweep and says PASS or FAIL per file, with
reasons. It checks three kinds of thing:

  structure   the rows that should exist, exist (right sizes, right reps)
  integrity   nothing failed or verified incorrectly, no empty measurements
  sanity      results that physics says must hold, hold

The sanity checks are the ones that would have caught the last bad run. Both
modes run identical data-plane code with the same AES-256-GCM cipher, so a
large classical-vs-PQC gap in bulk throughput is not a finding about
cryptography -- it is evidence the measurement was disturbed.

Exit code 0 = everything passed, 1 = at least one FAIL. Warnings do not fail
the run; they are printed for a human to judge.

Usage: validate_results.py [--dir benchmark/results] [--reps 3] [--quick]
"""
import argparse
import csv
import os
import statistics
import sys

MODES = ('classical', 'pqc')
FULL_SIZES = [1 << 10, 100 << 10, 1 << 20, 10 << 20, 100 << 20, 500 << 20, 1 << 30]
# Sizes where the two modes must agree: large enough that per-transfer setup is
# amortised and only the relay is being measured. Below ~10 MB the figures are
# genuinely noisy (one honest run showed 1.34x at 1 MB), so the check starts
# there -- but it must start somewhere, because the run it was written for
# showed 1.65-1.73x across 10 MB, 100 MB and 1 GB, which no amount of noise
# explains.
BULK = [10 << 20, 100 << 20, 500 << 20, 1 << 30]
# How far apart the modes may be on bulk transfer before it is suspicious.
# Both modes use AES-256-GCM and identical relay code; the handshake is long
# finished by then. Observed honest spread between modes is a few percent.
BULK_TOLERANCE = 1.25

results = []  # (level, file, message); level in {'FAIL', 'WARN'}


def fail(where, msg):
    results.append(('FAIL', where, msg))


def warn(where, msg):
    results.append(('WARN', where, msg))


def read(path):
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return list(csv.DictReader(fh))


def num(row, key):
    v = (row.get(key) or '').strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def check_handshake(d, expect_n):
    for mode in MODES:
        name = f'handshake_{mode}.csv'
        rows = read(os.path.join(d, name))
        if rows is None:
            fail(name, 'missing')
            continue
        vals = [num(r, 'ms') for r in rows]
        bad = sum(1 for v in vals if v is None)
        if bad:
            fail(name, f'{bad} unparseable timing(s)')
        if len(rows) != expect_n:
            warn(name, f'n={len(rows)}, expected {expect_n}')
        good = [v for v in vals if v is not None]
        if good and (min(good) <= 0 or max(good) > 10_000):
            fail(name, f'implausible timings: min={min(good)} max={max(good)}')


def check_resume(d, expect_per_kind):
    for mode in MODES:
        name = f'handshake_resume_{mode}.csv'
        rows = read(os.path.join(d, name))
        if rows is None:
            fail(name, 'missing')
            continue
        kinds = {}
        for r in rows:
            kinds.setdefault(r.get('kind'), []).append(num(r, 'ms'))
        for kind in ('full', 'resumed'):
            got = kinds.get(kind)
            if not got:
                fail(name, f'no "{kind}" rows')
            elif len(got) != expect_per_kind:
                warn(name, f'{kind}: n={len(got)}, expected {expect_per_kind}')
        full, res = kinds.get('full') or [], kinds.get('resumed') or []
        full = [v for v in full if v]
        res = [v for v in res if v]
        if full and res and statistics.median(res) > statistics.median(full):
            warn(name, 'resumed handshake is SLOWER than full -- resumption may not be working')


def check_latency(d, reps):
    for mode in MODES:
        name = f'latency_{mode}.csv'
        rows = read(os.path.join(d, name))
        if rows is None:
            fail(name, 'missing')
            continue
        if len(rows) != reps:
            warn(name, f'{len(rows)} repetition(s), expected {reps}')
        for r in rows:
            if num(r, 'avg_ms') is None:
                fail(name, f'rep {r.get("rep")}: no average')


def check_throughput(d, reps, expect_flood):
    for mode in MODES:
        name = f'throughput_{mode}.csv'
        rows = read(os.path.join(d, name))
        if rows is None:
            fail(name, 'missing')
            continue
        by = {}
        for r in rows:
            by.setdefault(r.get('protocol'), []).append(r)
        wanted = ['tcp', 'udp_1000m'] + (['udp_max'] if expect_flood else [])
        for proto in wanted:
            got = by.get(proto)
            if not got:
                fail(name, f'no {proto} rows')
                continue
            if len(got) != reps:
                warn(name, f'{proto}: {len(got)} rep(s), expected {reps}')
            empty = sum(1 for r in got if num(r, 'mbps') is None)
            if empty:
                fail(name, f'{proto}: {empty} failed measurement(s)')
            if proto.startswith('udp'):
                if any((r.get('loss_pct') or '').strip() == '' and num(r, 'mbps') is not None
                       for r in got):
                    fail(name, f'{proto}: UDP row without a loss figure')


def check_scaling(d, reps):
    for mode in MODES:
        name = f'scaling_{mode}.csv'
        rows = read(os.path.join(d, name))
        if rows is None:
            fail(name, 'missing')
            continue
        if 'detail' not in (rows[0] if rows else {}):
            warn(name, 'no detail column -- written by an older script')
        by = {}
        for r in rows:
            by.setdefault((r.get('test'), r.get('variant')), []).append(r)
        expected = ([('parallel_streams', s) for s in ('1', '2', '4', '8')]
                    + [('latency', 'idle'), ('latency', 'under_load')]
                    + [('clients', c) for c in ('1', '2', '3')])
        for key in expected:
            got = by.get(key)
            if not got:
                fail(name, f'missing {key[0]}/{key[1]}')
                continue
            if len(got) != reps:
                warn(name, f'{key[0]}/{key[1]}: {len(got)} rep(s), expected {reps}')
            incomplete = [r for r in got if 'INCOMPLETE' in (r.get('detail') or '')]
            if incomplete:
                fail(name, f'{key[0]}/{key[1]}: {len(incomplete)} rep(s) had missing clients '
                           f'({incomplete[0].get("detail")}) -- not an N-client measurement')
            empty = [r for r in got if num(r, 'value') is None and r not in incomplete]
            if empty:
                fail(name, f'{key[0]}/{key[1]}: {len(empty)} rep(s) produced no value')
        # The multi-client story only means anything if 3 clients really ran.
        three = by.get(('clients', '3')) or []
        if three and all('INCOMPLETE' in (r.get('detail') or '') for r in three):
            fail(name, 'no valid 3-client measurement at all')


def check_transfer(d, sizes, reps):
    med = {}
    for mode in MODES:
        name = f'file_transfer_{mode}.csv'
        rows = read(os.path.join(d, name))
        if rows is None:
            fail(name, 'missing')
            continue
        by = {}
        for r in rows:
            try:
                by.setdefault(int(r['size_bytes']), []).append(r)
            except (KeyError, ValueError):
                fail(name, 'unparseable size_bytes')
        missing = [s for s in sizes if s not in by]
        if missing:
            fail(name, f'missing size(s): {missing}')
        extra = [s for s in by if s not in sizes]
        if extra:
            warn(name, f'unexpected size(s): {extra}')
        for size, got in by.items():
            if len(got) != reps:
                warn(name, f'{size} B: {len(got)} rep(s), expected {reps}')
        bad = [r for r in rows if (r.get('sha256_ok') or '') != 'yes']
        if bad:
            fail(name, f'{len(bad)} transfer(s) did not verify (sha256_ok != yes)')
        med[mode] = {s: statistics.median([num(r, 'mbps') for r in g
                                           if num(r, 'mbps') is not None])
                     for s, g in by.items()
                     if any(num(r, 'mbps') is not None for r in g)}
    # Sanity: at bulk sizes the modes must agree. They share the data plane.
    if len(med) == 2:
        for size in BULK:
            a, b = med['classical'].get(size), med['pqc'].get(size)
            if not a or not b:
                continue
            ratio = max(a, b) / min(a, b)
            if ratio > BULK_TOLERANCE:
                faster = 'pqc' if b > a else 'classical'
                fail('file_transfer_*.csv',
                     f'at {size // (1 << 20)} MB the modes differ by {ratio:.2f}x '
                     f'({faster} faster: classical {a:.0f} vs pqc {b:.0f} Mbps). '
                     f'Both modes share the data plane and cipher, so this is a '
                     f'disturbed measurement, not a result.')


# How far the full-handshake medians of section 1 and section 2 may drift apart.
# They are separate runs and absolute handshake times on a desktop swing a lot
# (one documented set of repetition medians spanned 14.54-18.85 ms), so the
# tolerance is loose. It is not loose enough to admit the run it was written
# for, where section 2 put the PQC full handshake at 16.96 ms against section
# 1's 29.24 ms -- 1.72x apart, for the same handshake measured by the same
# function minutes later. Seven re-measurements all landed at 27-32 ms.
HANDSHAKE_AGREEMENT = 1.4


def check_handshake_agreement(d):
    """Section 1 and section 2 time the SAME full handshake. They must agree."""
    for mode in MODES:
        plain = read(os.path.join(d, f'handshake_{mode}.csv'))
        resume = read(os.path.join(d, f'handshake_resume_{mode}.csv'))
        if not plain or not resume:
            continue
        a = [num(r, 'ms') for r in plain]
        a = [v for v in a if v]
        b = [num(r, 'ms') for r in resume if r.get('kind') == 'full']
        b = [v for v in b if v]
        if not a or not b:
            continue
        ma, mb = statistics.median(a), statistics.median(b)
        ratio = max(ma, mb) / min(ma, mb)
        if ratio > HANDSHAKE_AGREEMENT:
            fail(f'handshake_{mode}.csv',
                 f'the full handshake measures {ma:.2f} ms on its own but '
                 f'{mb:.2f} ms in the resumption benchmark ({ratio:.2f}x apart). '
                 f'Both are the same handshake through the same code, so one of '
                 f'the two runs did not measure what it claims.')


def check_cross_consistency(d):
    """Throughput and the transfer sweep measure the same relay. Compare them."""
    for mode in MODES:
        tp = read(os.path.join(d, f'throughput_{mode}.csv')) or []
        ft = read(os.path.join(d, f'file_transfer_{mode}.csv')) or []
        tcp = [num(r, 'mbps') for r in tp if r.get('protocol') == 'tcp']
        tcp = [v for v in tcp if v]
        bulk = [num(r, 'mbps') for r in ft
                if r.get('size_bytes') == str(1 << 30) and num(r, 'mbps')]
        if not tcp or not bulk:
            continue
        t, b = statistics.median(tcp), statistics.median(bulk)
        # A single iperf3 stream is expected to sit BELOW a long bulk transfer,
        # but not by a wild margin. Far apart means one of them is not
        # measuring the relay.
        if b / t > 3.0 or t / b > 1.5:
            warn(f'{mode}', f'single-stream TCP {t:.0f} Mbps vs 1 GB bulk {b:.0f} Mbps '
                            f'({b / t:.2f}x apart) -- check both before quoting either')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--dir', default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  'results'))
    ap.add_argument('--reps', type=int, default=3, help='scaling repetitions expected')
    ap.add_argument('--lat-reps', type=int, default=None, help='latency repetitions (default: --reps)')
    ap.add_argument('--tput-reps', type=int, default=None, help='throughput repetitions (default: --reps)')
    ap.add_argument('--hs-n', type=int, default=125, help='pooled handshake trials expected')
    ap.add_argument('--rs-n', type=int, default=60, help='resumption trials per kind expected')
    ap.add_argument('--transfer-reps', type=int, default=3)
    ap.add_argument('--sizes', default='', help='space-separated byte counts; default is the full sweep')
    ap.add_argument('--no-flood', action='store_true', help='udp_max was skipped')
    args = ap.parse_args()

    d = args.dir
    sizes = [int(x) for x in args.sizes.split()] if args.sizes else FULL_SIZES

    print(f'Validating {d}')
    check_handshake(d, args.hs_n)
    check_resume(d, args.rs_n)
    check_latency(d, args.lat_reps if args.lat_reps is not None else args.reps)
    check_throughput(d, args.tput_reps if args.tput_reps is not None else args.reps,
                     expect_flood=not args.no_flood)
    check_scaling(d, args.reps)
    check_transfer(d, sizes, args.transfer_reps)
    check_handshake_agreement(d)
    check_cross_consistency(d)

    fails = [r for r in results if r[0] == 'FAIL']
    warns = [r for r in results if r[0] == 'WARN']
    for level, where, msg in fails + warns:
        marker = 'FAIL' if level == 'FAIL' else 'warn'
        print(f'  [{marker}] {where}: {msg}')
    if not results:
        print('  all checks passed')
    print(f'\n{len(fails)} failure(s), {len(warns)} warning(s)')
    if fails:
        print('DO NOT PUBLISH THIS RUN until the failures above are explained.')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
