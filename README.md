# Project 2 - Database System

A four-module database system: storage engine, execution engine, SQL front
end, query optimizer. Building checkpoint by checkpoint per Project 2
section 7.

## Current status

- Week 1 - Refactored Project 1's relational algebra engine into a
  library. Done.
- Module 1 - Storage engine and buffer pool. Done.
- Module 2 - Execution engine. In progress.
- Module 3 - SQL front end. Not started.
- Module 4 - Query optimizer. Not started.

## Layout

    engine/       refactored Project 1 modules (lexer, parser, tree, ...)
    storage/      Module 1: disk, page, heap, buffer, catalog, loader, cli
    tests/        tests/fixtures/ holds P1 data files for regression
    p1_reference/ original Project 1 files, kept for reference

## Running M1

    python -m storage create-table --db DIR --name R --schema "a:INT,b:VARCHAR(20),c:INT"
    python -m storage load         --db DIR --table R --file r.csv
    python -m storage scan         --db DIR --table R --frames 8 [--limit N] --json
    python -m storage layout       --db DIR --table R --page 3
    python -m storage trace        --db DIR --file trace.txt --frames 16 --policy lru|clock --json

## Tests

    python -m pytest tests/ -v

## Known limitations

- Scan stats: scan --json currently returns empty stats because
  HeapFile.scan() reads pages via DiskManager directly rather than
  through BufferPool.fetch_page. The scan itself obeys M1 section 4.1
  (never pins more than one data page and one directory page), but the
  counters are attributed to DiskManager, not the pool. Fix scheduled
  before M2.
- No compaction: M1 section 3.2 marks compaction optional; not
  implemented.

## Where to read more

- DESIGN_M1.md - byte-level layout of records, pages, heap directory,
  catalog, and buffer policies.
- REPORT_M1.md - M1's three required measurements.
- DESIGN_LOG.md - session-by-session log, including AI failures.

## Running M2

    python -m exec run   --db DB_DIR --plan plan.json --frames 32 --json
    python -m exec trace --db DB_DIR --plan plan.json --frames 32

Plan JSON format is M2 section 3.2. Malformed plans are rejected
before execution with a message naming the offending node.

## M2 known limitations

See DESIGN_M2.md section 8. The most important: joins and set ops
materialise their inputs in memory rather than streaming through the
buffer pool. This affects measured page reads but not correctness of
the 109 passing tests.

## Measurements

    python measure_m2.py

Writes results/m2_measurements.json. See REPORT_M2.md for analysis.

## Running M2

    python -m exec run   --db DB_DIR --plan plan.json --frames 32 --json
    python -m exec trace --db DB_DIR --plan plan.json --frames 32

Plan JSON format is M2 section 3.2. Malformed plans are rejected
before execution with a message naming the offending node.

## M2 known limitations

See DESIGN_M2.md section 8. The most important: joins and set ops
materialise their inputs in memory rather than streaming through the
buffer pool. This affects measured page reads but not correctness of
the 109 passing tests.

## Measurements

    python measure_m2.py

Writes results/m2_measurements.json. See REPORT_M2.md for analysis.
