import json
import struct

TYPE_DATA = 0x00
TYPE_CONTROL = 0x01
TYPE_LEN = 1
HEADER_LEN = TYPE_LEN + 4  # [type:1][length:4][payload]

KEEPALIVE = b'KA'  # control-frame payload for keepalives

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