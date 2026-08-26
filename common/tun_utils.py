import fcntl
import struct
import subprocess
import os
from typing import IO

TUNSETIFF = 0x400454ca
IFF_TUN = 0x0001
IFF_NO_PI = 0x1000
DEFAULT_MTU = 1400

_open_tuns: dict[int, IO] = {}


def _keep_alive(fd: int, obj: IO) -> int:
    _open_tuns[fd] = obj
    return fd


def create_tun(name: str, addr: str, mtu: int = DEFAULT_MTU) -> int:
    tun = open('/dev/net/tun', 'rb+', buffering=0)
    ifr = struct.pack('16sH', name.encode()[:15], IFF_TUN | IFF_NO_PI)
    fcntl.ioctl(tun, TUNSETIFF, ifr)

    # Idempotent: a stale device from a crashed prior run may already hold
    # the address. "File exists" means we're done; anything else is real.
    result = subprocess.run(
        ['ip', 'addr', 'add', addr, 'dev', name],
        capture_output=True, text=True,
    )
    if result.returncode != 0 and 'File exists' not in result.stderr:
        raise RuntimeError(f'ip addr add {addr} dev {name} failed: {result.stderr.strip()}')
    subprocess.run(
        ['ip', 'link', 'set', 'dev', name, 'mtu', str(mtu)],
        check=True, capture_output=True
    )
    subprocess.run(
        ['ip', 'link', 'set', 'dev', name, 'up'],
        check=True, capture_output=True
    )

    return _keep_alive(tun.fileno(), tun)


def open_tun_by_name(name: str) -> int:
    tun = open('/dev/net/tun', 'rb+', buffering=0)
    ifr = struct.pack('16sH', name.encode()[:15], IFF_TUN | IFF_NO_PI)
    fcntl.ioctl(tun, TUNSETIFF, ifr)
    return _keep_alive(tun.fileno(), tun)


def destroy_tun(name: str, fd: int | None = None):
    """Release a TUN device so it can be re-created later.

    Closes our cached file object (a single-queue TUN held by an open fd
    returns EBUSY on re-attach) and deletes the interface. Safe to call
    twice or on a device that no longer exists.
    """
    obj = _open_tuns.pop(fd, None) if fd is not None else None
    if obj is not None:
        try:
            obj.close()
        except OSError:
            pass
    subprocess.run(['ip', 'link', 'del', name],
                   capture_output=True)
