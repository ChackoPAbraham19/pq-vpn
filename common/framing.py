import struct

HEADER_LEN = 4

def pack_frame(data: bytes) -> bytes:
    return struct.pack('!I', len(data)) + data

def unpack_frames(buffer: bytes):
    offset = 0
    while offset + HEADER_LEN <= len(buffer):
        length = struct.unpack('!I', buffer[offset:offset + HEADER_LEN])[0]
        end = offset + HEADER_LEN + length
        if end > len(buffer):
            break
        yield buffer[offset + HEADER_LEN:end]
        offset = end
