# storage/heap.py — heap file as a linked list of directory pages. Exact M1 §4.
#
# Directory page layout:
#   offset  size  field
#        0     4  page_id          uint32
#        4     4  next_dir_page    uint32, 0xFFFFFFFF if none
#        8     2  entry_count      uint16
#       10     6  entry 0          uint32 data_page_id, uint16 free_bytes
#       16     6  entry 1
#   ...
# capacity = (4096 - 10) // 6 = 681 entries per directory page
import struct
from storage.page import SlottedPage, PAGE_SIZE
from engine.errors import EngineError

DIR_HEADER = 10
DIR_ENTRY = 6
DIR_CAPACITY = (PAGE_SIZE - DIR_HEADER) // DIR_ENTRY   # = 681
NO_NEXT = 0xFFFFFFFF


class DirectoryPage:
    """A data page that holds entries pointing to other data pages."""
    def __init__(self, page_id, data=None):
        self.page_id = page_id
        if data is None:
            self.data = bytearray(PAGE_SIZE)
            struct.pack_into("<IIH", self.data, 0, page_id, NO_NEXT, 0)
        else:
            self.data = bytearray(data)

    @property
    def next_dir(self):
        return struct.unpack_from("<I", self.data, 4)[0]

    @property
    def entry_count(self):
        return struct.unpack_from("<H", self.data, 8)[0]

    def get_entry(self, i):
        off = DIR_HEADER + DIR_ENTRY * i
        return struct.unpack_from("<IH", self.data, off)

    def set_entry(self, i, page_id, free_bytes):
        off = DIR_HEADER + DIR_ENTRY * i
        struct.pack_into("<IH", self.data, off, page_id, free_bytes)

    def add_entry(self, page_id, free_bytes):
        if self.entry_count >= DIR_CAPACITY:
            raise EngineError("schema", "directory page full")
        self.set_entry(self.entry_count, page_id, free_bytes)
        struct.pack_into("<H", self.data, 8, self.entry_count + 1)


class HeapFile:
    """A heap file with one file. Uses the DiskManager + BufferPool to read/write.

    For now, `disk` is anything with read_page / write_page / allocate_page.
    We test with an in-memory FakeDisk so we don't need storage/disk.py yet.
    """
    def __init__(self, disk, table_name):
        self.disk = disk
        self.table_name = table_name

    # ---------- low-level ----------
    def _read(self, page_id):
        return self.disk.read_page(page_id)

    def _write(self, page_id, data):
        self.disk.write_page(page_id, bytes(data))

    def _alloc(self):
        return self.disk.allocate_page()

    # ---------- directory walk ----------
    def _dir_chain(self):
        """Yield DirectoryPage objects for every dir page, in order."""
        if self.disk.num_pages() == 0:
            return
        pid = 0
        while pid != NO_NEXT:
            dp = DirectoryPage(pid, self._read(pid))
            yield dp
            pid = dp.next_dir

    # ---------- public API ----------
    def insert(self, record: bytes):
        """Insert bytes into the first data page with space. Returns (page_id, slot_no)."""
        self._ensure_dir_page()
        need = len(record) + 4
        # walk dir pages, first-fit
        for dp in self._dir_chain():
            for i in range(dp.entry_count):
                data_pid, free_bytes = dp.get_entry(i)
                if free_bytes >= need:
                    page = SlottedPage(data_pid, self._read(data_pid))
                    slot = page.insert(record)
                    if slot is None:
                        continue
                    self._write(data_pid, page.data)
                    dp.set_entry(i, data_pid, page.free_space())
                    self._write(dp.page_id, dp.data)
                    return (data_pid, slot)
        # no room, allocate a new data page
        new_pid = self._alloc()
        page = SlottedPage(new_pid)
        slot = page.insert(record)
        if slot is None:
            raise EngineError("schema",
                f"record of {len(record)} bytes does not fit on an empty page")
        self._write(new_pid, page.data)
        # find a dir page with room, else allocate one
        dp = self._dir_with_room()
        if dp is None:
            dp = self._alloc_dir()
        dp.add_entry(new_pid, page.free_space())
        self._write(dp.page_id, dp.data)
        return (new_pid, slot)

    def get(self, rid):
        page_id, slot = rid
        page = SlottedPage(page_id, self._read(page_id))
        return page.get(slot)

    def delete(self, rid):
        page_id, slot = rid
        page = SlottedPage(page_id, self._read(page_id))
        page.delete(slot)
        self._write(page_id, page.data)
        # update the directory free_bytes for this page
        for dp in self._dir_chain():
            for i in range(dp.entry_count):
                if dp.get_entry(i)[0] == page_id:
                    dp.set_entry(i, page_id, page.free_space())
                    self._write(dp.page_id, dp.data)
                    return
        raise EngineError("schema", f"page {page_id} not in directory")

    def scan(self):
        """Yield (rid, record) in heap order. Pins at most one dir page and one data page at a time."""
        for dp in self._dir_chain():
            for i in range(dp.entry_count):
                data_pid, _free = dp.get_entry(i)
                page = SlottedPage(data_pid, self._read(data_pid))
                for slot in range(page.slot_count):
                    try:
                        rec = page.get(slot)
                    except EngineError:
                        continue          # dead slot, skip
                    yield ((data_pid, slot), rec)

    # ---------- internals ----------

    def _ensure_dir_page(self):
        """If the file has no pages, page 0 must become the first directory page."""
        if self.disk.num_pages() == 0:
            pid = self.disk.allocate_page()      # will be 0
            dp = DirectoryPage(pid)
            self._write(pid, dp.data)

    def _dir_with_room(self):
        for dp in self._dir_chain():
            if dp.entry_count < DIR_CAPACITY:
                return dp
        return None

    def _alloc_dir(self):
        """Allocate a new directory page and link it at the tail."""
        new_pid = self._alloc()
        dp = DirectoryPage(new_pid)
        self._write(new_pid, dp.data)
        # link from the last existing dir page
        last = None
        for d in self._dir_chain():
            last = d
        if last is None:
            # first dir page must be page 0
            raise EngineError("schema", "heap file must start with a dir page at 0")
        struct.pack_into("<I", last.data, 4, new_pid)
        self._write(last.page_id, last.data)
        return dp
