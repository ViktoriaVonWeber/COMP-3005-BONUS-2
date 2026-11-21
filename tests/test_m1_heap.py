# tests/test_m1_heap.py — M1 §4 behaviour.
import struct
from storage.heap import HeapFile, DirectoryPage, DIR_CAPACITY, NO_NEXT
from storage.page import SlottedPage, PAGE_SIZE


class FakeDisk:
    """In-memory DiskManager stand-in."""
    def __init__(self):
        self.pages = {}       # pid -> bytes
        self.next_pid = 0

    def read_page(self, pid):
        if pid not in self.pages:
            raise KeyError(pid)
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


def make_heap():
    d = FakeDisk()
    # first dir page at 0
    dp = DirectoryPage(0)
    d.write_page(0, dp.data)
    d.next_pid = 1
    return HeapFile(d, "R"), d


def test_insert_and_get():
    hf, _ = make_heap()
    rid = hf.insert(b"hello")
    assert hf.get(rid) == b"hello"


def test_multiple_inserts_same_page():
    hf, _ = make_heap()
    rids = [hf.insert(f"r{i}".encode()) for i in range(10)]
    for i, rid in enumerate(rids):
        assert hf.get(rid) == f"r{i}".encode()
    # all on the same data page (page 1, since 0 is the dir page)
    assert all(rid[0] == rids[0][0] for rid in rids)


def test_directory_has_one_entry():
    hf, disk = make_heap()
    hf.insert(b"hello")
    dp = DirectoryPage(0, disk.read_page(0))
    assert dp.entry_count == 1
    assert dp.get_entry(0)[0] == 1   # data page id


def test_scan_returns_all_in_order():
    hf, _ = make_heap()
    expected = []
    for i in range(50):
        rid = hf.insert(f"row{i}".encode())
        expected.append((rid, f"row{i}".encode()))
    got = list(hf.scan())
    assert got == expected


def test_delete_removes_from_scan():
    hf, _ = make_heap()
    rid0 = hf.insert(b"a")
    rid1 = hf.insert(b"b")
    hf.delete(rid0)
    got = [r for _rid, r in hf.scan()]
    assert got == [b"b"]


def test_scan_handles_many_pages():
    hf, _ = make_heap()
    # each record is 200 bytes, so ~20 records per 4KB page
    for i in range(100):
        hf.insert((f"{i:0200d}").encode())
    assert len(list(hf.scan())) == 100
