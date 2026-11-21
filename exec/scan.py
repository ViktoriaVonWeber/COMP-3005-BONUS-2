# exec/scan.py - seqscan and filter. M2 sections 2.1 and 2.2.
from exec.operator import Operator, END
from storage.heap import DirectoryPage, NO_NEXT
from storage.record import decode_tuple
from storage.page import SlottedPage
from engine.condition import parse_condition, eval_condition
from engine.errors import EngineError


class SeqScan(Operator):
    """Stream every live tuple of a table through the buffer pool."""

    def __init__(self, table, catalog, buffer_pool):
        super().__init__("seqscan")
        self.table = table
        self.catalog = catalog
        self.pool = buffer_pool
        self.children = []
        self._schema = None
        self._dir_pid = None
        self._dir_entry_i = 0
        self._data_pid = None
        self._data_slot = 0
        self._data_page = None
        self._open = False
        self._table_schema = None

    def schema(self):
        return self._schema

    def open(self):
        entry = self.catalog.get_table(self.table)
        self._table_schema = entry["schema"]
        self._schema = [(f"{self.table}.{c[0]}", c[1]) for c in entry["schema"]]
        self._dir_pid = entry["first_page"]
        self._dir_entry_i = 0
        self._data_pid = None
        self._data_slot = 0
        self._data_page = None
        self._open = True

    def _fetch_counted(self, page_id):
        before = self.pool.misses
        frame = self.pool.fetch_page(page_id)
        self.page_reads += (self.pool.misses - before)
        return frame

    def next(self):
        if not self._open:
            raise EngineError("schema", "SeqScan.next() before open()")
        while True:
            if self._data_pid is not None:
                if self._data_slot < self._data_page.slot_count:
                    slot = self._data_slot
                    self._data_slot += 1
                    try:
                        raw = self._data_page.get(slot)
                    except EngineError:
                        continue
                    tup, _ = decode_tuple(raw, self._table_schema)
                    return self._emit(tup)
                self.pool.unpin_page(self._data_pid, is_dirty=False)
                self._data_pid = None
                self._data_page = None

            while True:
                if self._dir_pid is None or self._dir_pid == NO_NEXT:
                    return END
                frame = self._fetch_counted(self._dir_pid)
                dp = DirectoryPage(self._dir_pid, frame.data)
                if self._dir_entry_i < dp.entry_count:
                    data_pid, _free = dp.get_entry(self._dir_entry_i)
                    self._dir_entry_i += 1
                    self.pool.unpin_page(self._dir_pid, is_dirty=False)
                    dframe = self._fetch_counted(data_pid)
                    self._data_pid = data_pid
                    self._data_page = SlottedPage(data_pid, dframe.data)
                    self._data_slot = 0
                    break
                else:
                    next_dir = dp.next_dir
                    self.pool.unpin_page(self._dir_pid, is_dirty=False)
                    self._dir_pid = next_dir
                    self._dir_entry_i = 0

    def close(self):
        if self._data_pid is not None:
            self.pool.unpin_page(self._data_pid, is_dirty=False)
            self._data_pid = None
            self._data_page = None
        self._open = False


class Filter(Operator):
    """Pass through tuples where `condition` evaluates true."""

    def __init__(self, condition, child):
        super().__init__("filter")
        self.condition = condition
        self.child = child
        self.children = [child]
        self._parsed = None

    def schema(self):
        return self.child.schema()

    def open(self):
        self._parsed = parse_condition(self.condition)
        self.child.open()

    def next(self):
        schema = self.child.schema()
        while True:
            t = self.child.next()
            if t is END:
                return END
            self.comparisons += 1
            if eval_condition(self._parsed, t, schema):
                return self._emit(t)

    def close(self):
        self.child.close()
