#!/usr/bin/env python3
"""net_config.py — server-side network plumbing for the PQ-VPN.

Sets up IP forwarding, NAT MASQUERADE for the tunnel subnet, FORWARD rules,
and an optional dnsmasq instance bound to the tunnel interface.

All commands can be printed instead of executed with dry_run=True so the
behaviour is testable without root.
"""
import os
import signal
import shutil
import subprocess
import sys

SYSCTL_CONF = '/etc/sysctl.d/99-pqvpn.conf'


def _run(cmd, dry_run=False, check=True):
    if dry_run:
        print(f'[net_config] would run: {" ".join(cmd)}')
        return None
    try:
        return subprocess.run(cmd, check=check, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f'[net_config] command failed ({e}): {" ".join(cmd)}', file=sys.stderr)
        if e.stderr:
            sys.stderr.write(e.stderr.decode(errors='replace'))
        raise


def _rule_exists(args, dry_run=False) -> bool:
    """Check whether an iptables rule already exists (idempotent add).

    `args` is the full command tail AFTER the action flag, e.g.
    ['FORWARD', '-i', 'tun0', ...] with the action already included by the
    caller (we pass a '-C' form here).
    """
    if dry_run:
        return False
    try:
        out = subprocess.run(
            ['iptables'] + args, capture_output=True
        ).returncode
        return out == 0
    except FileNotFoundError:
        return False


def setup_nat(config: dict, dry_run=False):
    """Enable forwarding + NAT for the tunnel subnet out the egress iface.

    Config keys:
      tun_net        e.g. '10.8.0.0/24'
      tun_name       e.g. 'tun0'
      outbound_iface e.g. 'veth-ns1' (netns) / 'eth0' (VPS)
      nat            bool, default True
      persist_sysctl bool, default False — write /etc/sysctl.d/99-pqvpn.conf
      mss_clamp      bool, default True — TCPMSS clamp on the tunnel (PMTU blackholes)
    """
    if not config.get('nat', True):
        print('[net_config] NAT disabled by config')
        return
    tun_net = config.get('tun_net', '10.8.0.0/24')
    tun_name = config.get('tun_name', 'tun0')
    egress = config.get('outbound_iface', 'veth-ns1')

    _run(['sysctl', '-w', 'net.ipv4.ip_forward=1'], dry_run=dry_run)

    if config.get('persist_sysctl'):
        if dry_run:
            print(f'[net_config] would write {SYSCTL_CONF}')
        else:
            with open(SYSCTL_CONF, 'w') as f:
                f.write('# PQ-VPN\nnet.ipv4.ip_forward = 1\n')

    # iptables wants: iptables -t <table> -{A,C,D} <chain> <spec>
    # (the action flag must hug the chain name — 'iptables -A -t nat ...'
    # is a usage error, which is what broke the first live netns run)
    nat_spec = ['-s', tun_net, '-o', egress, '-j', 'MASQUERADE']
    fwd_out = ['-i', tun_name, '-o', egress, '-j', 'ACCEPT']
    fwd_in = ['-i', egress, '-o', tun_name, '-j', 'ACCEPT']

    checks_adds = [
        (['-t', 'nat', '-C', 'POSTROUTING'] + nat_spec,
         ['-t', 'nat', '-A', 'POSTROUTING'] + nat_spec),
        (['-C', 'FORWARD'] + fwd_out,
         ['-A', 'FORWARD'] + fwd_out),
        (['-C', 'FORWARD'] + fwd_in,
         ['-A', 'FORWARD'] + fwd_in),
    ]
    if config.get('mss_clamp', True):
        # The tunnel MTU (1400) is smaller than the upstream's (1500). Without
        # clamping, TCP peers on the internet pick a 1460-byte MSS, their
        # full-size segments cannot enter the tunnel, and path-MTU discovery
        # often fails silently: pages half-load, downloads stall. Clamp SYNs
        # in both directions to the path MTU — standard for any full-tunnel VPN.
        for spec in _mss_specs(tun_name):
            checks_adds.append((['-t', 'mangle', '-C', 'FORWARD'] + spec,
                                ['-t', 'mangle', '-A', 'FORWARD'] + spec))
    for check_cmd, add_cmd in checks_adds:
        if not _rule_exists(check_cmd, dry_run):
            _run(['iptables'] + add_cmd, dry_run=dry_run)


def _mss_specs(tun_name: str) -> list[list[str]]:
    """TCPMSS clamp rules for SYNs entering and leaving the tunnel."""
    tail = ['-p', 'tcp', '--tcp-flags', 'SYN,RST', 'SYN', '-j', 'TCPMSS', '--clamp-mss-to-pmtu']
    return [['-o', tun_name] + tail, ['-i', tun_name] + tail]


def teardown_nat(config: dict, dry_run=False):
    """Remove the NAT/forwarding rules added by setup_nat."""
    if not config.get('nat', True):
        return
    tun_net = config.get('tun_net', '10.8.0.0/24')
    tun_name = config.get('tun_name', 'tun0')
    egress = config.get('outbound_iface', 'veth-ns1')

    nat_spec = ['-s', tun_net, '-o', egress, '-j', 'MASQUERADE']
    fwd_out = ['-i', tun_name, '-o', egress, '-j', 'ACCEPT']
    fwd_in = ['-i', egress, '-o', tun_name, '-j', 'ACCEPT']

    deletes = [
        ['-t', 'nat', '-D', 'POSTROUTING'] + nat_spec,
        ['-D', 'FORWARD'] + fwd_out,
        ['-D', 'FORWARD'] + fwd_in,
    ]
    if config.get('mss_clamp', True):
        deletes += [['-t', 'mangle', '-D', 'FORWARD'] + spec for spec in _mss_specs(tun_name)]
    for del_cmd in deletes:
        _run(['iptables'] + del_cmd, dry_run=dry_run, check=False)


def start_dnsmasq(config: dict, dry_run=False):
    """Run dnsmasq bound to the tunnel interface.

    Config keys:
      dnsmasq        bool, default False
      dns_upstream   str, default '1.1.1.1' (may be a comma list)
      tun_name       str
      gateway        str — dnsmasq listen address
    Returns the Popen handle (to be terminated on shutdown) or None.
    """
    if not config.get('dnsmasq', False):
        return None
    if dry_run:
        print('[net_config] would run dnsmasq on tun0')
        return None
    if not shutil.which('dnsmasq'):
        print('[net_config] dnsmasq not installed; skipping', file=sys.stderr)
        return None

    tun_name = config.get('tun_name', 'tun0')
    gateway = config.get('gateway', '10.8.0.1')
    upstream = config.get('dns_upstream', '1.1.1.1')
    cmd = [
        'dnsmasq', '--keep-in-foreground', '--no-daemon',
        f'--interface={tun_name}', f'--listen-address={gateway}',
        '--no-resolv', f'--server={upstream}', '--bind-interfaces',
        '--log-queries', '--log-facility=-',
    ]
    print(f'[net_config] starting dnsmasq (pid will be assigned)')
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def stop_dnsmasq(proc):
    if proc is None:
        return
    try:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=3)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def drain_dnsmasq(proc, stop):
    """Forward dnsmasq log lines to stderr until stop is set."""
    if proc is None or proc.stdout is None:
        return
    while not stop.is_set():
        line = proc.stdout.readline()
        if not line:
            break
        sys.stderr.write('[dnsmasq] ' + line.decode(errors='replace'))
        sys.stderr.flush()