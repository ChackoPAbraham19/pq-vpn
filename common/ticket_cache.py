import os
import hashlib
import tempfile

CACHE_ROOT = os.path.expanduser('~/.pqvpn')
CACHE_DIR = os.path.join(CACHE_ROOT, 'cache')


def cache_dir() -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return CACHE_DIR


def _ticket_name(server_host: str) -> str:
    digest = hashlib.sha256(server_host.encode()).hexdigest()[:16]
    return f'{server_host.replace("/", "_")}.{digest}.tick'


def ticket_path(server_host: str) -> str:
    """Full path of the ticket cache file for a server (creates cache dir)."""
    return os.path.join(cache_dir(), _ticket_name(server_host))


def load_ticket(server_host: str) -> str | None:
    """Path to an existing ticket file for the server, or None."""
    path = ticket_path(server_host)
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        return path
    return None


def save_ticket_file(server_host: str) -> str:
    """A writable path for a fresh ticket (overwrites any stale cache)."""
    return ticket_path(server_host)


def rotate_ticket(server_host: str, current: str | None) -> str:
    """Write the in-use ticket atomically; return the stable cache path.

    `current` may be a temp file used by this session; the new ticket lands at
    the stable path so it survives across daemon restarts.
    """
    stable = ticket_path(server_host)
    if current and os.path.isfile(current) and os.path.abspath(current) != os.path.abspath(stable):
        tmp = stable + f'.tmp{os.getpid()}'
        with open(tmp, 'wb') as dst:
            with open(current, 'rb') as src:
                dst.write(src.read())
        os.replace(tmp, stable)
    return stable


def clear_ticket(server_host: str) -> None:
    path = ticket_path(server_host)
    if os.path.isfile(path):
        os.remove(path)
