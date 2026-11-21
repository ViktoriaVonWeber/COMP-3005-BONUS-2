# storage/page.py — slotted page, exact M1 §3 layout.
# Layout:
#   offset  size  field
#        0     4  page_id          uint32
#        4     2  slot_count       uint16 (includes dead slots)
#        6     2  free_end         uint16 (lowest byte offset used by record data)
#        8     4  slot 0           uint16 offset, uint16 length
#       12     4  slot 1
#     free_end                      record data (newest at lowest offset)
#     4095                         end
import struct
from engine.errors import EngineError

PAGE_SIZE = 4096
HEADER_SIZE = 8
SLOT_SIZE = 4
NO_SPACE = None


class SlottedPage:
    def __init__(self, page_id, data=None):
        self.page_id = page_id
        if data is None:
            self.data = bytearray(PAGE_SIZE)
            struct.pack_into("<IHH", self.data, 0, page_id, 0, PAGE_SIZE)
        else:
            self.data = bytearray(data)

    @property
    def slot_count(self):
        return struct.unpack_from("<H", self.data, 4)[0]

    @property
    def free_end(self):
        return struct.unpack_from("<H", self.data, 6)[0]

    def free_space(self):
        return self.free_end - (HEADER_SIZE + SLOT_SIZE * self.slot_count)

    def insert(self, record: bytes):
        """Returns slot_no, or None (NO_SPACE) if it doesn't fit."""
        L = len(record)
        if self.free_space() < L + SLOT_SIZE:
            return NO_SPACE
        new_free_end = self.free_end - L
        self.data[new_free_end:new_free_end + L] = record
        struct.pack_into("<H", self.data, 6, new_free_end)         # free_end
        slot_no = self.slot_count
        struct.pack_into("<HH", self.data, HEADER_SIZE + SLOT_SIZE * slot_no,
                         new_free_end, L)
        struct.pack_into("<H", self.data, 4, slot_no + 1)          # slot_count
        return slot_no

    def get(self, slot_no):
        if slot_no < 0 or slot_no >= self.slot_count:
            raise EngineError("name", f"bad slot {slot_no}")
        off, L = struct.unpack_from("<HH", self.data, HEADER_SIZE + SLOT_SIZE * slot_no)
        if off == 0:
            raise EngineError("name", f"dead slot {slot_no}")
        return bytes(self.data[off:off + L])

    def delete(self, slot_no):
        if slot_no < 0 or slot_no >= self.slot_count:
            raise EngineError("name", f"bad slot {slot_no}")
        off, _L = struct.unpack_from("<HH", self.data, HEADER_SIZE + SLOT_SIZE * slot_no)
        if off == 0:
            raise EngineError("name", f"slot {slot_no} already dead")
        struct.pack_into("<HH", self.data, HEADER_SIZE + SLOT_SIZE * slot_no, 0, 0)
        # Note: bytes are NOT reclaimed; only compaction can do that.
