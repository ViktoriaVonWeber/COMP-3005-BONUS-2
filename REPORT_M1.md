# REPORT_M1 - Storage Engine Measurements

## 1. Capacity - computed vs measured

Formula (M1 section 3.3): for a fixed-length record of L bytes,
records per page = floor(4088 / (L + 4)).

| schema | record L | computed records/page |
|---|---|---|
| Hosts(id INT, risk INT) | 8 | 340 |
| Iocs(indicator VARCHAR(20), confidence INT) | variable (22 + indicator len) | up to 157 at maximum indicator length |
| Traffic(src VARCHAR(200), ts INT) | variable (204 + src len) | up to 19 at maximum src length |

Measured values and gap explanations to be filled in after loading 1000
rows per schema. See DESIGN_M1.md section 3 for the formula, and section
2 of this report for the CLI commands used to measure.

## 2. Policies - hit rate

Pending publication of the five traces. The buffer pool's correctness is
verified independently by tests/test_m1_buffer.py reproducing M1 section
5.3 exactly for both LRU and CLOCK (1/5/2 and 2/4/1). When traces are
available, run:

    for t in traces/trace1.txt traces/trace2.txt traces/trace3.txt traces/trace4.txt traces/trace5.txt; do
        for B in 4 16 64 256; do
            for p in lru clock; do
                python -m storage trace --db /tmp/m1 --file $t --frames $B --policy $p --json
            done
        done
    done

and fill in the 40-row table (20 rows per policy). Sequential flooding
on trace 3 is expected to show near-zero hit rate for both policies at
B = 64; LRU's mechanism is: each scan evicts pages the next scan needs;
CLOCK's is: all reference bits are 1 after a scan, so the sweep clears
them without evicting anything useful. I would ship CLOCK - O(1) per
fetch, no worse than LRU on this workload.

## 3. Scan cost - 500-page table

To measure:

    python -m storage scan --db /tmp/m1 --table Traffic --frames 2 --json
    python -m storage scan --db /tmp/m1 --table Traffic --frames 8 --json
    python -m storage scan --db /tmp/m1 --table Traffic --frames 64 --json

Expected: page reads approximately 500 at every B. A scan holds one
data page and one directory page, so total reads = number of data pages
(plus a handful for the directory). It does not drop with B because
nothing in a single scan is reused across pages; it does not rise
because nothing is cached that would cause eviction. If reads fall as B
grows, something is caching across pages; if they rise, something is
wrong.
