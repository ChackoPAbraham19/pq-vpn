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

    subprocess.run(
        ['ip', 'addr', 'add', addr, 'dev', name],
        check=True, capture_output=True
    )
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
