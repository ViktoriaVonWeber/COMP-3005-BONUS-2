# tests/test_m1_record.py — M1 §2 hex example must reproduce exactly.
from storage.record import encode_tuple, decode_tuple

SCHEMA = [("a", "INT", None), ("b", "VARCHAR", 5), ("c", "INT", None)]

def test_m1_2_worked_example():
    """(42, 'bob', 7) under (a INT, b VARCHAR(5), c INT) → 13 bytes,
    hex 2A 00 00 00 03 00 62 6F 62 07 00 00 00."""
    data = encode_tuple((42, "bob", 7), SCHEMA)
    assert len(data) == 13
    assert data.hex().upper() == "2A0000000300626F6207000000"

def test_roundtrip():
    for tup in [(0, "", 0), (-1, "x", -1), (2147483647, "hello", -2147483648)]:
        data = encode_tuple(tup, SCHEMA)
        back, consumed = decode_tuple(data, SCHEMA)
        assert back == tup
        assert consumed == len(data)

def test_varchar_overflow():
    from engine.errors import EngineError
    try:
        encode_tuple((1, "toolong", 1), SCHEMA)   # 7 bytes > 5
        assert False, "should have raised"
    except EngineError as e:
        assert e.category == "schema"

def test_varchar_n_too_big():
    from engine.errors import EngineError
    try:
        encode_tuple((1, "x", 1), [("a", "VARCHAR", 300)])
        assert False, "should have raised"
    except EngineError as e:
        assert e.category == "schema"
