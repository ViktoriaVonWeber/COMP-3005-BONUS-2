# tests/test_m1_page.py — M1 §3.2 must reproduce exactly.
from storage.page import SlottedPage, NO_SPACE


def test_m1_3_2_worked_example():
    """empty page, insert 13, insert 20, insert 13, delete slot 1."""
    p = SlottedPage(0)
    assert (p.slot_count, p.free_end, p.free_space()) == (0, 4096, 4088)

    s0 = p.insert(b"x" * 13)
    assert s0 == 0
    assert (p.slot_count, p.free_end, p.free_space()) == (1, 4083, 4071)

    s1 = p.insert(b"y" * 20)
    assert s1 == 1
    assert (p.slot_count, p.free_end, p.free_space()) == (2, 4063, 4047)

    s2 = p.insert(b"z" * 13)
    assert s2 == 2
    assert (p.slot_count, p.free_end, p.free_space()) == (3, 4050, 4030)

    p.delete(1)
    assert (p.slot_count, p.free_end, p.free_space()) == (3, 4050, 4030)
    # deleting frees NO space (per §3.2 note)


def test_get_returns_exact_bytes():
    p = SlottedPage(0)
    p.insert(b"hello")
    p.insert(b"world")
    assert p.get(0) == b"hello"
    assert p.get(1) == b"world"


def test_dead_slot_errors():
    from engine.errors import EngineError
    p = SlottedPage(0)
    p.insert(b"a")
    p.delete(0)
    try:
        p.get(0)
        assert False, "should have raised"
    except EngineError:
        pass


def test_delete_already_dead_errors():
    from engine.errors import EngineError
    p = SlottedPage(0)
    p.insert(b"a")
    p.delete(0)
    try:
        p.delete(0)
        assert False, "should have raised"
    except EngineError:
        pass


def test_no_space_when_full():
    """Fill with 13-byte records until it refuses. §3.3 says 240 fit."""
    p = SlottedPage(0)
    n = 0
    while p.insert(b"x" * 13) is not NO_SPACE:
        n += 1
    assert n == 240   # §3.3: floor(4088 / (13+4)) = 240


def test_roundtrip_from_bytes():
    p = SlottedPage(0)
    p.insert(b"hello")
    p.insert(b"world")
    p2 = SlottedPage(0, data=bytes(p.data))
    assert p2.get(0) == b"hello"
    assert p2.get(1) == b"world"
