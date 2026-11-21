# storage/record.py — byte-level tuple encoding, exact M1 §2.
# Schema format: list of (name, type, length) where
#   type is "INT" or "VARCHAR"
#   length is None for INT, or n for VARCHAR(n)
import struct
from engine.errors import EngineError


def encode_int(v):
    # 4 bytes, signed, two's complement, little-endian
    if not isinstance(v, int):
        raise EngineError("type", f"expected INT, got {type(v).__name__}")
    if not (-2**31 <= v < 2**31):
        raise EngineError("schema", f"INT out of range: {v}")
    return struct.pack("<i", v)


def encode_varchar(s, n):
    # 2-byte unsigned length L, then L bytes UTF-8. L must not exceed n, n <= 255.
    if not isinstance(s, str):
        raise EngineError("type", f"expected VARCHAR, got {type(s).__name__}")
    if n is None or n > 255:
        raise EngineError("schema", f"VARCHAR length must be <= 255, got {n}")
    b = s.encode("utf-8")
    if len(b) > n:
        raise EngineError("schema",
            f"VARCHAR({n}) overflow: {len(b)} bytes for {s!r}")
    return struct.pack("<H", len(b)) + b


def encode_tuple(tup, schema):
    """schema = [("a","INT",None), ("b","VARCHAR",5), ...]"""
    if len(tup) != len(schema):
        raise EngineError("schema",
            f"expected {len(schema)} values, got {len(tup)}")
    out = b""
    for (name, typ, n), v in zip(schema, tup):
        if typ == "INT":
            out += encode_int(v)
        elif typ == "VARCHAR":
            out += encode_varchar(v, n)
        else:
            raise EngineError("schema", f"unknown type {typ!r}")
    return out


def decode_tuple(data, schema):
    """Inverse of encode_tuple. Returns a tuple."""
    out = []
    off = 0
    for (name, typ, n) in schema:
        if typ == "INT":
            if off + 4 > len(data):
                raise EngineError("schema", f"truncated INT for {name}")
            v = struct.unpack_from("<i", data, off)[0]
            off += 4
            out.append(v)
        elif typ == "VARCHAR":
            if off + 2 > len(data):
                raise EngineError("schema", f"truncated VARCHAR length for {name}")
            L = struct.unpack_from("<H", data, off)[0]
            off += 2
            if L > n:
                raise EngineError("schema",
                    f"VARCHAR({n}) value has length {L}")
            if off + L > len(data):
                raise EngineError("schema", f"truncated VARCHAR data for {name}")
            s = data[off:off+L].decode("utf-8")
            off += L
            out.append(s)
        else:
            raise EngineError("schema", f"unknown type {typ!r}")
    return tuple(out), off   # also return bytes consumed, useful for slotted pages
