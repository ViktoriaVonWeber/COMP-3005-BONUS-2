# storage/buffer.py — buffer pool with LRU and CLOCK. Exact M1 §5.
# Counting rules (must be exact):
#   hit       = fetch of a resident page
#   miss      = fetch that required a disk read
#   eviction  = miss that replaced a resident page
#               (first B misses on an empty pool are NOT evictions)
#   read      = one disk.read_page call
#   write     = one disk.write_page call
from engine.errors import EngineError


class Frame:
    __slots__ = ("page_id", "data", "pin", "dirty", "ref", "stamp")
    def __init__(self):
        self.page_id = None
        self.data = None
        self.pin = 0
        self.dirty = False
        self.ref = 0
        self.stamp = 0


class BufferPool:
    def __init__(self, disk, frames, policy):
        if policy not in ("lru", "clock"):
            raise EngineError("schema", f"unknown policy {policy!r}")
        self.disk = disk
        self.B = frames
        self.policy = policy
        self.frames = [Frame() for _ in range(frames)]
        self.page_table = {}     # page_id -> frame index
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.reads = 0
        self.writes = 0
        self.clock_hand = 0
        self.lru_stamp = 0

    # ---------- policy ----------
    def _victim(self):
        if self.policy == "lru":
            best = None
            for i, f in enumerate(self.frames):
                if f.pin != 0:
                    continue
                if best is None or f.stamp < self.frames[best].stamp:
                    best = i
            return best
        # CLOCK
        swept = 0
        while swept <= 2 * self.B:
            f = self.frames[self.clock_hand]
            i = self.clock_hand
            self.clock_hand = (self.clock_hand + 1) % self.B
            if f.pin > 0:
                swept += 1
                continue
            if f.ref == 1:
                f.ref = 0
                swept += 1
                continue
            return i
        return None

    def _touch(self, frame):
        self.lru_stamp += 1
        frame.stamp = self.lru_stamp
        frame.ref = 1

    # ---------- public API ----------
    def fetch_page(self, page_id):
        if page_id in self.page_table:
            f = self.frames[self.page_table[page_id]]
            f.pin += 1
            self._touch(f)
            self.hits += 1
            return f

        idx = self._victim()
        if idx is None:
            raise EngineError("schema", "no unpinned frame available")

        f = self.frames[idx]
        if f.page_id is not None:
            # evicting an existing page
            if f.dirty:
                self.disk.write_page(f.page_id, bytes(f.data))
                self.writes += 1
            del self.page_table[f.page_id]
            self.evictions += 1

        f.page_id = page_id
        f.data = bytearray(self.disk.read_page(page_id))
        self.reads += 1
        f.pin = 1
        f.dirty = False
        self._touch(f)
        self.page_table[page_id] = idx
        self.misses += 1
        return f

    def unpin_page(self, page_id, is_dirty):
        if page_id not in self.page_table:
            raise EngineError("schema", f"unpin non-resident page {page_id}")
        f = self.frames[self.page_table[page_id]]
        if f.pin == 0:
            raise EngineError("schema", f"unpin page {page_id} at pin 0")
        f.dirty = f.dirty or is_dirty
        f.pin -= 1

    def new_page(self):
        idx = self._victim()
        if idx is None:
            raise EngineError("schema", "no unpinned frame available")
        f = self.frames[idx]
        if f.page_id is not None:
            if f.dirty:
                self.disk.write_page(f.page_id, bytes(f.data))
                self.writes += 1
            del self.page_table[f.page_id]
            self.evictions += 1
        pid = self.disk.allocate_page()
        f.page_id = pid
        f.data = bytearray(4096)
        f.pin = 1
        f.dirty = True
        self._touch(f)
        self.page_table[pid] = idx
        self.misses += 1
        return f

    def flush_all(self):
        for f in self.frames:
            if f.page_id is not None and f.dirty:
                self.disk.write_page(f.page_id, bytes(f.data))
                self.writes += 1
                f.dirty = False

    def stats(self):
        return {
            "hits": self.hits, "misses": self.misses,
            "evictions": self.evictions, "reads": self.reads,
            "writes": self.writes,
        }
