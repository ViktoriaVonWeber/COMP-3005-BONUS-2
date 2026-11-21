# tests/test_m1_buffer.py — M1 §5.3 must reproduce for both policies.
from storage.buffer import BufferPool
from storage.page import PAGE_SIZE


class FakeDisk:
    def __init__(self):
        self.pages = {}
        self.next_pid = 0
    def read_page(self, pid):
        if pid not in self.pages:
            self.pages[pid] = bytes(PAGE_SIZE)
        return self.pages[pid]
    def write_page(self, pid, data):
        self.pages[pid] = bytes(data)
    def allocate_page(self):
        pid = self.next_pid
        self.next_pid += 1
        self.pages[pid] = bytes(PAGE_SIZE)
        return pid
    def num_pages(self):
        return self.next_pid


def run_trace(policy):
    disk = FakeDisk()
    # Pre-allocate pages 1..4 so read_page can find them
    for pid in (1, 2, 3, 4):
        disk.pages[pid] = bytes(PAGE_SIZE)
    disk.next_pid = 5
    bp = BufferPool(disk, frames=3, policy=policy)
    for pid in (1, 2, 3, 1, 4, 2):
        bp.fetch_page(pid)
        bp.unpin_page(pid, is_dirty=False)
    return bp.stats()


def test_m1_5_3_lru():
    s = run_trace("lru")
    assert s["hits"] == 1
    assert s["misses"] == 5
    assert s["evictions"] == 2
    assert s["reads"] == 5
    assert s["writes"] == 0


def test_m1_5_3_clock():
    s = run_trace("clock")
    assert s["hits"] == 2
    assert s["misses"] == 4
    assert s["evictions"] == 1
    assert s["reads"] == 4
    assert s["writes"] == 0


def test_evict_pinned_page_errors():
    from engine.errors import EngineError
    disk = FakeDisk()
    for pid in (1, 2):
        disk.pages[pid] = bytes(PAGE_SIZE)
    disk.next_pid = 3
    bp = BufferPool(disk, frames=1, policy="lru")
    bp.fetch_page(1)                # pin page 1 (never unpin)
    try:
        bp.fetch_page(2)            # needs a frame, but page 1 is pinned
        assert False, "should have raised"
    except EngineError as e:
        assert "no unpinned frame" in str(e)


def test_unpin_at_zero_errors():
    from engine.errors import EngineError
    disk = FakeDisk()
    disk.pages[1] = bytes(PAGE_SIZE)
    disk.next_pid = 2
    bp = BufferPool(disk, frames=2, policy="lru")
    bp.fetch_page(1)
    bp.unpin_page(1, is_dirty=False)
    try:
        bp.unpin_page(1, is_dirty=False)
        assert False, "should have raised"
    except EngineError:
        pass
