import os
import subprocess

PQC_OPENSSL_PREFIX = '/opt/openssl-pqc'


def _make_env(mode: str, config_dir: str | None = None) -> dict:
    env = os.environ.copy()
    if mode == 'pqc':
        env['PATH'] = f'{PQC_OPENSSL_PREFIX}/bin:' + env.get('PATH', '')
        # OpenSSL installs to lib64, liboqs (needed by oqsprovider.so) to lib.
        env['LD_LIBRARY_PATH'] = (f'{PQC_OPENSSL_PREFIX}/lib64:{PQC_OPENSSL_PREFIX}/lib:'
                                  + env.get('LD_LIBRARY_PATH', ''))
        env.pop('LD_PRELOAD', None)
        if config_dir:
            env['OPENSSL_CONF'] = os.path.join(config_dir, 'openssl-pqc-runtime.cnf')
        else:
            env['OPENSSL_CONF'] = os.path.join(
                os.path.dirname(__file__), '..', 'pki', 'openssl-pqc-runtime.cnf'
            )
    return env


def start_tls_server(
    host: str,
    port: int,
    cert_path: str,
    key_path: str,
    ca_path: str,
    mode: str = 'classical',
    groups: str = 'X25519MLKEM768',
    config_dir: str | None = None,
    naccept: int | None = 1,
    crl_path: str | None = None,
) -> subprocess.Popen:
    """Spawn `openssl s_server`.

    naccept=1 (default) makes the process exit after its single client
    disconnects, so the relay sees EOF immediately and the slot/port is freed
    without waiting for the idle timer. Pass None for a long-lived listener
    (benchmarks prime tickets against one).

    crl_path: when the file exists, client certificates are checked against
    it and a revoked one is refused at the handshake (alert 44) — nothing the
    client sends ever reaches the relay. Read at spawn time, so a fresh CRL
    applies to the very next session without restarting the daemon.
    """
    cmd = ['openssl', 's_server']
    if mode == 'pqc':
        cmd.extend(['-provider', 'oqsprovider', '-provider', 'default'])
    cmd.extend([
        '-accept', f'{host}:{port}',
        '-cert', cert_path,
        '-key', key_path,
        '-CAfile', ca_path,
        '-Verify', '1',
        '-quiet',
    ])
    if naccept is not None:
        cmd.extend(['-naccept', str(naccept)])
    if crl_path and os.path.isfile(crl_path):
        cmd.extend(['-CRL', crl_path, '-crl_check', '-verify_return_error'])
    if mode == 'pqc':
        cmd.extend(['-groups', groups])

    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_make_env(mode, config_dir),
    )


def start_tls_client(
    host: str,
    port: int,
    cert_path: str,
    key_path: str,
    ca_path: str,
    mode: str = 'classical',
    groups: str = 'X25519MLKEM768',
    config_dir: str | None = None,
    sess_in: str | None = None,
    sess_out: str | None = None,
) -> subprocess.Popen:
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
        '-brief',
    ])
    if mode == 'pqc':
        cmd.extend(['-groups', groups])
    if sess_in:
        cmd.extend(['-sess_in', sess_in])
    if sess_out:
        cmd.extend(['-sess_out', sess_out])

    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_make_env(mode, config_dir),
    )


# Lines from `s_client -brief` stderr worth surfacing in status. Values are
# observed negotiation results, not config echoes.
_BRIEF_KEYS = {
    'Protocol version': 'protocol',
    'Ciphersuite': 'cipher',
    'Signature type': 'sig_type',
    'Server Temp Key': 'kex',
}


def parse_brief_line(line: str) -> tuple[str, str] | None:
    """Map one `s_client -brief` stderr line to a (key, value) status pair.

    Returns None for lines carrying no negotiation info.
    """
    line = line.strip()
    for prefix, key in _BRIEF_KEYS.items():
        if line.startswith(prefix + ':'):
            value = line[len(prefix) + 1:].strip()
            if value:
                return key, value
    return None
