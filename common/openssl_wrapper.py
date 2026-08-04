import os
import subprocess

PQC_OPENSSL_PREFIX = '/opt/openssl-pqc'


def _make_env(mode: str, config_dir: str | None = None) -> dict:
    env = os.environ.copy()
    if mode == 'pqc':
        env['PATH'] = f'{PQC_OPENSSL_PREFIX}/bin:' + env.get('PATH', '')
        env['LD_LIBRARY_PATH'] = f'{PQC_OPENSSL_PREFIX}/lib64:' + env.get('LD_LIBRARY_PATH', '')
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
) -> subprocess.Popen:
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
    ])
    if mode == 'pqc':
        cmd.extend(['-groups', groups])

    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_make_env(mode, config_dir),
    )
