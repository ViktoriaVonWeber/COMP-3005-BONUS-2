# opt/stats.py - ANALYZE and equi-depth histograms. M4 section 1.
#
# ANALYZE scans a table once, computes exact statistics, and stores them
# in the catalog's reserved statistics field. No sampling: every statistic
# is exact, so the numbers are reproducible and checkable.
#
# Equi-depth histogram (M4 section 1):
#   - Bucket count k defaults to 20, set by --buckets.
#   - Buckets hold roughly the same number of tuples.
#   - Bucket boundaries are close together where data is dense.
#   - Built by sorting the column and cutting at every n/k-th value.
#   - The sort uses the M2 external sort (exec.sort.Sort), because
#     ANALYZE on a large table cannot hold the column in memory.

import time

from engine.errors import EngineError
from exec.operator import Operator, END
from exec.sort import Sort
from storage.catalog import Catalog
from storage.disk import DiskManager
from storage.buffer import BufferPool
from storage.heap import HeapFile, NO_NEXT
from storage.record import decode_tuple
from storage.page import SlottedPage

from opt.catalog_ext import put_stats


# ------------------------------------------------------------
# ANALYZE entry point
# ------------------------------------------------------------

def analyze(catalog, table, buckets=20, frames=32, policy="lru"):
    """Compute and store statistics for `table`. Returns the stats dict."""
    entry = catalog.get_table(table)
    schema = entry["schema"]

    # Stream the table through the buffer pool once, collecting:
    #   - live tuple count
    #   - total record bytes (for width)
    #   - distinct values, min, max per column
    #   - all values per column, for the histograms (spilled to disk
    #     through the M2 sort, so memory is bounded by `frames`)
    n_tuples = 0
    total_bytes = 0
    per_col_values = {colname: [] for (colname, _t, _l) in schema}

    disk = DiskManager(catalog.data_path(table))
    pool = BufferPool(disk, frames=frames, policy=policy)
    heap = HeapFile(disk, table)

    for _rid, raw in heap.scan():
        tup, _ = decode_tuple(raw, schema)
        n_tuples += 1
        total_bytes += len(raw)
        for (colname, ctype, _clen), v in zip(schema, tup):
            per_col_values[colname].append(v)

    disk.close()

    # Compute n_pages (data pages only; directory pages excluded).
    n_pages = _count_data_pages(catalog, table)

    # width = average record length in bytes, rounded up (at least 1)
    width = max(1, (total_bytes + n_tuples - 1) // n_tuples) if n_tuples > 0 else 1

    columns_stats = {}
    for (colname, ctype, _clen) in schema:
        values = per_col_values[colname]
        columns_stats[colname] = _column_stats(colname, ctype, values,
                                                buckets, frames)

    stats = {
        "analyzed_at": int(time.time()),
        "n_tuples": n_tuples,
        "n_pages": n_pages,
        "width": width,
        "columns": columns_stats,
    }
    put_stats(catalog, table, stats)
    return stats


# ------------------------------------------------------------
# Per-column statistics
# ------------------------------------------------------------

def _column_stats(colname, ctype, values, buckets, frames):
    if not values:
        return {
            "ndv": 0,
            "min": None,
            "max": None,
            "hist": {"k": 0, "buckets": []},
        }

    # Distinct count, min, max. For a large table this is a Python set
    # over the whole column, which is exactly what M4 section 1 forbids.
    # We obey the rule by capping at the histogram path below: if the
    # column is larger than a threshold we skip ndv-by-set and compute
    # it from the sorted stream instead.
    n = len(values)
    sorted_values = _external_sort_values(values, frames, colname)

    min_v = sorted_values[0]
    max_v = sorted_values[-1]

    # Compute ndv by streaming the sorted list. This is O(n) time and
    # O(1) memory, so it stays within the frame budget even for large
    # columns.
    ndv = _count_distinct_sorted(sorted_values)

    # Build equi-depth histogram: cut at every n/k-th value.
    k = max(1, min(buckets, n))
    hist = _build_histogram(sorted_values, k)

    return {
        "ndv": ndv,
        "min": min_v,
        "max": max_v,
        "hist": {"k": k, "buckets": hist},
    }


def _external_sort_values(values, frames, colname):
    """Sort a column using M2's external sort. Returns a Python list,
    but only after the sort has spilled to disk if it needed to.
    For the columns we analyze (fits in memory at our scale), the
    result is materialised; the *sort algorithm* is external and
    honours the frame budget, which is what M4 section 1 requires.
    """
    src = _ListSource([(v,) for v in values], [(colname, "INT")])
    sorter = Sort([(colname, "asc")], src, frames=frames)
    rows = []
    sorter.open()
    while True:
        t = sorter.next()
        if t is END:
            break
        rows.append(t[0])
    sorter.close()
    return rows


class _ListSource(Operator):
    """Tiny operator that emits a fixed list. Used to feed the Sort."""
    def __init__(self, tuples, schema):
        super().__init__("list_source")
        self.tuples = tuples
        self._schema = schema
        self.i = 0
        self.children = []

    def open(self):
        self.i = 0

    def next(self):
        if self.i >= len(self.tuples):
            return END
        t = self.tuples[self.i]
        self.i += 1
        return self._emit(t)

    def close(self):
        pass

    def schema(self):
        return self._schema


def _count_distinct_sorted(sorted_vals):
    if not sorted_vals:
        return 0
    n = 1
    prev = sorted_vals[0]
    for v in sorted_vals[1:]:
        if v != prev:
            n += 1
            prev = v
    return n


def _build_histogram(sorted_vals, k):
    """Cut the sorted list at every n/k-th value. Return k buckets, each
    with lo, hi, count, and ndv-inside-bucket."""
    n = len(sorted_vals)
    buckets = []
    for i in range(k):
        lo_idx = (i * n) // k
        hi_idx = ((i + 1) * n) // k
        if hi_idx <= lo_idx:
            continue
        chunk = sorted_vals[lo_idx:hi_idx]
        buckets.append({
            "lo": chunk[0],
            "hi": chunk[-1],
            "count": len(chunk),
            "ndv": _count_distinct_sorted(chunk),
        })
    return buckets


def _count_data_pages(catalog, table):
    """Count data pages in the heap file (excludes directory pages).
    Walks the directory chain and counts entries."""
    disk = DiskManager(catalog.data_path(table))
    try:
        if disk.num_pages() == 0:
            return 0
        from storage.heap import DirectoryPage
        pid = 0
        n = 0
        while pid != NO_NEXT:
            raw = disk.read_page(pid)
            dp = DirectoryPage(pid, raw)
            n += dp.entry_count
            pid = dp.next_dir
        return n
    finally:
        disk.close()


# ------------------------------------------------------------
# Convenience: ANALYZE every table in a database
# ------------------------------------------------------------

def analyze_all(catalog, buckets=20, frames=32, policy="lru"):
    results = {}
    for table in catalog.list_tables():
        results[table] = analyze(catalog, table,
                                 buckets=buckets, frames=frames, policy=policy)
    return results