#!/usr/bin/env python3
"""Sum one multi-client iperf3 round, and say how it was composed.

Lives in its own file rather than a heredoc inside run_scaling_bench.sh: the
previous inline version was rewritten twice and broke silently both times, once
because iperf3 -J emits multi-line JSON that a line-splitting parser never
reassembled. A parser that reports nothing looks exactly like a tunnel that
carried nothing, so this one makes the difference explicit.

Prints one line: "<total_mbps> <clients_found> <per_client>"
  total_mbps   sum of delivered rates, empty if nothing parsed
  clients_found  how many JSON files yielded a result
  per_client   e.g. "412+396+x" -- 'x' marks a client that produced nothing

Usage: _scaling_parse.py <dir-of-json-files>
"""
import glob
import json
import os
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print(" 0 -")
        return 2
    total, found, per = 0.0, 0, []
    for path in sorted(glob.glob(os.path.join(sys.argv[1], '*.json'))):
        try:
            with open(path) as fh:
                bps = json.load(fh)['end']['sum_received']['bits_per_second']
        except Exception:
            per.append('x')
            continue
        total += bps
        per.append(f'{bps / 1e6:.0f}')
        found += 1
    print(f"{total / 1e6:.2f}" if found else '',
          found,
          '+'.join(per) if per else '-')
    return 0


if __name__ == '__main__':
    sys.exit(main())
