import ipaddress
import json
import struct

TYPE_DATA = 0x00
TYPE_CONTROL = 0x01
TYPE_LEN = 1
HEADER_LEN = TYPE_LEN + 4  # [type:1][length:4][payload]

KEEPALIVE = b'KA'  # control-frame payload for keepalives

# Keys the server pushes over TLS as the first frame of a session. The client
# applies ip/gateway/dns system-wide, so it validates the shape before acting.
CONFIG_KEYS = ('id', 'ip', 'gateway', 'dns', 'port', 'subnet')


def validate_config(cfg) -> dict:
    """Return cfg if it is a well-formed config-push, else raise ValueError."""
    if not isinstance(cfg, dict):
        raise ValueError('config-push is not an object')
    missing = [k for k in CONFIG_KEYS if k not in cfg]
    if missing:
        raise ValueError(f'config-push missing keys: {missing}')
    for key in ('ip', 'gateway'):
        try:
            ipaddress.IPv4Address(cfg[key])
        except (ipaddress.AddressValueError, TypeError) as e:
            raise ValueError(f'config-push {key} invalid: {e}')
    for dns in str(cfg['dns']).split(','):
        try:
            ipaddress.IPv4Address(dns.strip())
        except ipaddress.AddressValueError as e:
            raise ValueError(f'config-push dns invalid: {e}')
    if not isinstance(cfg['port'], int) or not (0 < cfg['port'] < 65536):
        raise ValueError(f'config-push port invalid: {cfg["port"]!r}')
    try:
        ipaddress.ip_network(cfg['subnet'], strict=False)
    except ValueError as e:
        raise ValueError(f'config-push subnet invalid: {e}')
    return cfg

def pack_frame(data: bytes, ftype: int = TYPE_DATA) -> bytes:
    return struct.pack('!BI', ftype, len(data)) + data

def unpack_frames(buffer: bytes):
    """Yield (ftype, payload) tuples for complete frames in buffer."""
    offset = 0
    while offset + HEADER_LEN <= len(buffer):
        ftype = buffer[offset]
        length = struct.unpack('!I', buffer[offset + TYPE_LEN:offset + HEADER_LEN])[0]
        end = offset + HEADER_LEN + length
        if end > len(buffer):
            break
        yield ftype, buffer[offset + HEADER_LEN:end]
        offset = end

def pack_config(config: dict) -> bytes:
    """Pack a JSON config-push as a control frame."""
    return pack_frame(json.dumps(config).encode(), ftype=TYPE_CONTROL)

def pack_keepalive() -> bytes:
    """Pack a keepalive control frame."""
    return pack_frame(KEEPALIVE, ftype=TYPE_CONTROL)

def is_keepalive(ftype: int, payload: bytes) -> bool:
    return ftype == TYPE_CONTROL and payload == KEEPALIVE

def unpack_config(buffer: bytes):
    """Return (config_dict, remaining_buffer) from a stream head.

    Consumes only complete TYPE_CONTROL frames; any trailing partial frame
    data or extra traffic is preserved in remaining_buffer for the next read.
    """
    offset = 0
    cfg = None
    while offset + HEADER_LEN <= len(buffer):
        ftype = buffer[offset]
        length = struct.unpack('!I', buffer[offset + TYPE_LEN:offset + HEADER_LEN])[0]
        end = offset + HEADER_LEN + length
        if end > len(buffer):
            break
        payload = buffer[offset + HEADER_LEN:end]
        if ftype == TYPE_CONTROL:
            cfg = json.loads(payload)
            offset = end
            break
        offset = end
    return cfg, buffer[offset:]