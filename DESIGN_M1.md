# DESIGN_M1 - Storage Engine and Buffer Pool

## 1. Record format (M1 section 2)

- INT: 4 bytes, signed two's complement, little-endian.
- VARCHAR(n): 2-byte unsigned length L, then L UTF-8 bytes. L <= n <= 255.
- No record header, no null bitmap (no NULLs in this project).
- Fixed length when all columns are INT; variable otherwise.

Worked example from M1 section 2, verified by
tests/test_m1_record.py::test_m1_2_worked_example:
schema (a INT, b VARCHAR(5), c INT), tuple (42, 'bob', 7) gives 13 bytes,
hex 2A 00 00 00 03 00 62 6F 62 07 00 00 00.

## 2. Page layout (M1 section 3) - from a real page in my file

Layout (all fields required by M1 section 3):

    offset  size  field
         0     4  page_id          uint32
         4     2  slot_count       uint16   includes dead slots
         6     2  free_end         uint16   lowest byte offset used by record data
         8     4  slot 0           uint16 offset, uint16 length
        12     4  slot 1
       ...
     free_end                      record data (newest at lowest offset)
      4095                         end of page

Real page dump - /tmp/testdb/Alerts.db, page 1, schema
(id INT, severity INT, ioc VARCHAR(32)), 3 rows loaded:

    page_id=1  slot_count=3  free_end=4045  free_space=4025

    slots:
      [0] offset=4079 length= 17  raw=01000000090000000700312e322e332e34
      [1] offset=4062 length= 17  raw=02000000050000000700352e362e372e38
      [2] offset=4045 length= 17  raw=03000000080000000700312e322e332e34

    first 64 bytes of page (hex):
      010000000300cd0fef0f1100de0f1100cd0f11000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000

Decoding slot 0: 01 00 00 00 (id=1), 09 00 00 00 (severity=9),
07 00 (VARCHAR length 7), 31 2e 32 2e 33 2e 34 ("1.2.3.4"). 17 bytes.

Worked example from M1 section 3.2, verified by
tests/test_m1_page.py::test_m1_3_2_worked_example:
empty, insert 13, insert 20, insert 13, delete slot 1 gives exactly
the slot_count / free_end / free_space numbers in M1 section 3.2.
Deleting slot 1 frees no usable space (bytes are not reclaimed).

## 3. Free-space strategy (M1 section 3.1)

free_space = free_end - (8 + 4 * slot_count). An insert of L bytes needs
L + 4 bytes of free space (L for the record, 4 for the new slot). If
free_space < L + 4, insert returns NO_SPACE and the heap file moves on
to the next directory entry or allocates a new data page.

Fixed-length capacity for a schema with total record length L is
floor(4088 / (L + 4)) (M1 section 3.3). For L = 17 the capacity is 194
records per page.

## 4. Compaction decision

Not implemented. M1 section 3.2 says compaction is optional. I chose not
to implement it because:

- It adds complexity (moving records requires updating slot offsets; if
  slot numbers ever change, rids break).
- The heap file's free-space directory still routes inserts to pages with
  room, so an uncompacted page is wasteful but not incorrect.
- M1 section 3.2 says "if you implement it, document when it runs and
  show the before and after in your design document." Not implementing
  it requires no further documentation.

Documented here per M1 section 3.2.

## 5. Heap file (M1 section 4)

Directory page layout (M1 section 4):

    offset  size  field
         0     4  page_id          uint32
         4     4  next_dir_page    uint32, 0xFFFFFFFF if none
         8     2  entry_count      uint16
        10     6  entry 0          uint32 data_page_id, uint16 free_bytes
        16     6  entry 1
       ...

capacity = floor((4096 - 10) / 6) = 681 entries per directory page.

- insert(record): first-fit across all directory entries in dir-page
  order; if no entry has enough free space, allocate a new data page and
  add an entry. A fresh heap file starts with page 0 as the first
  directory page, created by _ensure_dir_page() before the first insert.
- get(rid): at most 2 page accesses (rid embeds the page id directly).
- delete(rid): mark the slot dead in the data page, update the directory
  entry's free_bytes.
- scan(): for each dir page, for each entry, for each live slot. At most
  one data page and one dir page pinned at a time (M1 section 4.1). The
  generator yields (rid, record) and moves on; nothing is held across
  entries.

## 6. Buffer pool (M1 section 5)

Frames hold: page_id, data, pin, dirty, ref (CLOCK), stamp (LRU).
page_table maps page_id to frame index.

LRU: every fetch (hit or miss) updates stamp to a monotonically
increasing counter. Victim = unpinned frame with the smallest stamp.

CLOCK: circular frames with a reference bit each and a clock_hand.
Every fetch (hit or miss) sets ref = 1. On a miss, the hand sweeps:
pinned frames are skipped; frames with ref = 1 are skipped and their ref
bit cleared; the first unpinned frame with ref = 0 is the victim. The
hand advances past every frame it inspects. swept > 2B means no victim
(all pinned).

Counting rules (M1 section 5, exact):

- hit = fetch of a resident page.
- miss = fetch that required a disk read.
- eviction = miss that replaced a resident page. The first B misses on
  an empty pool are misses, not evictions (checked by
  frame.page_id is not None).
- read = one disk.read_page call.
- write = one disk.write_page call (eviction of a dirty page, or
  flush_all).

Worked example from M1 section 5.3, verified by
tests/test_m1_buffer.py:
B = 3, trace 1 2 3 1 4 2:
- LRU: 1 hit, 5 misses, 2 evictions.
- CLOCK: 2 hits, 4 misses, 1 eviction.

Both match the M1 section 5.3 table.

Error cases (M1 section 9): evicting a pinned page returns
"no unpinned frame available"; unpinning at pin count 0 raises
EngineError. Both tested.

## 7. Catalog page format

One catalog page per table entry in catalog.db. Layout:

    offset  size  field
         0     4  magic bytes "CATL"
         4     2  name_len
         6     n  name UTF-8
       6+n     2  ncols
       8+n     1  type_code (1=INT, 2=VARCHAR)      \
       9+n     1  colname_len                      |
      ...       n  colname UTF-8                    |  repeated ncols times
      ...       1  varlen (0 for INT, n for VARCHAR)/
      ...       4  first_page uint32
      ...       4  num_pages uint32
      ...       4  num_tuples uint32
      ...       4  stats_len uint32 (0 = no stats yet)
      ...       k  stats JSON bytes (M4 will populate)

The stats_len / stats JSON fields are the reserved room M1 section 6
asks for so that M4 can add statistics without changing the page format.

## 8. CSV loader (M1 section 6)

Reads a CSV file with no header, one record per line. Fields separated
by commas; a field containing a comma must be wrapped in double quotes;
a double quote inside a quoted field is written as "". An INT that does
not parse, or a VARCHAR longer than its declared length, raises
EngineError("schema", "line N: column X: ...") and the load stops.

## 9. CLI (M1 section 7)

Five subcommands, exposed as python -m storage:

    storage create-table --db DIR --name R --schema "a:INT,b:VARCHAR(20),c:INT"
    storage load         --db DIR --table R --file r.csv
    storage scan         --db DIR --table R --frames 8 [--limit N] --json
    storage layout       --db DIR --table R --page 3
    storage trace        --db DIR --file trace.txt --frames 16 --policy lru|clock --json

The JSON shapes match M1 section 7 exactly (scan --json outputs
{table, schema, rows, stats}; trace --json outputs
{policy, frames, hits, misses, evictions, reads, writes}).
