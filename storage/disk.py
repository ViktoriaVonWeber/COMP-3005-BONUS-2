# storage/disk.py — the only code in the system permitted to touch a file.
# Byte-for-byte: fixed 4096 pages, no header outside the pages.
import os
from storage.page import PAGE_SIZE

class DiskManager:
    def __init__(self, path):
        self.path = path
        self.reads = 0
        self.writes = 0
        if not os.path.exists(path):
            open(path, "wb").close()
        self.fd = open(path, "r+b")

    def num_pages(self):
        self.fd.seek(0, os.SEEK_END)
        size = self.fd.tell()
        return size // PAGE_SIZE

    def read_page(self, page_id):
        self.fd.seek(page_id * PAGE_SIZE)
        data = self.fd.read(PAGE_SIZE)
        if len(data) != PAGE_SIZE:
            raise IOError(f"short read on page {page_id} of {self.path}")
        self.reads += 1
        return data

    def write_page(self, page_id, data):
        assert len(data) == PAGE_SIZE, f"page must be {PAGE_SIZE} bytes"
        self.fd.seek(page_id * PAGE_SIZE)
        self.fd.write(data)
        self.fd.flush()
        self.writes += 1

    def allocate_page(self):
        pid = self.num_pages()
        # extend the file by one page of zeros
        self.fd.seek(pid * PAGE_SIZE)
        self.fd.write(b"\x00" * PAGE_SIZE)
        self.fd.flush()
        return pid

    def close(self):
        self.fd.close()

    def stats(self):
        return {"reads": self.reads, "writes": self.writes}
