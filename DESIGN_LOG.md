# Design Log

One entry per working session. At least two places per module where AI
assistance gave me something wrong, slow, or quietly incorrect, and how
I found it. A log saying "asked and it worked" scores zero.

## 2026-10-05 - Week 1: P1 to library refactor

Trying to: turn Project 1's engine into four importable modules (lexer,
parser, tree, operators), with no printing, no file I/O, and JSON
round-trip on every tree node, per Project 2 section 1.3.

Tried: kept the P1 monolith and wrapped it. Failed the "importable
independently" rule because operators still read in-memory lists, and
failed section 1.3 point 6 because projection deduplicated.

Broke: projection was a set operation. Under Project 2 section 1.3
point 6, projection must keep duplicates and dedup becomes a separate
distinct operator. P1 test case 23 (project[DID] returned 2 rows) now
returns 3; P1 case 24 (project[Name, Name] errored) now keeps both
columns. Both logged here per section 1.3 point 7.

Changed: split into lexer / parser / tree / condition / operators /
errors; operators pull tuples from an injected source; added distinct;
added to_json / json_to_tree with a round-trip test on every node type.

## 2026-10-12 - M1 Step 2.3: heap file had no directory page

Trying to: wire the CSV loader to the heap file so a real file on disk
could be loaded.

Tried: heap.insert walks the directory chain first-fit, then allocates a
data page if no dir page has room. The earlier unit test
(test_m1_heap.py) created page 0 as a directory page by hand in a
make_heap() helper.

Broke: on a fresh Alerts.db (zero pages), heap.insert allocated page 0
as a data page, then on the next record tried to read page 0 as a
directory page. It interpreted data-page header bytes as a directory
entry and read a garbage page id (267321345), causing OSError: short
read on page 267321345.

Fix: added _ensure_dir_page() which, if disk.num_pages() == 0,
allocates page 0 and writes an empty DirectoryPage before any other
work. Called at the top of insert, get, delete, and scan. Lesson: the
test fixture should be constructed by the same code the production path
uses, or it will paper over a missing precondition.

## 2026-10-13 - M1 Step 2.1: test asserted wrong count for select[B=830]

Trying to: verify the refactored engine still answers P1 queries on
data_1000.txt.

Tried: wrote "assert len(rows) == 1" with a comment "one R row has
B=830 in data_1000.txt."

Broke: the assertion failed with "assert 2 == 1" - the query returned
[(85897, 830), (185126, 830)]. The engine was correct; my test comment
was wrong. I had assumed without checking, and an AI-assisted draft of
the test repeated the assumption.

Fix: changed to "assert len(rows) == 2" and verified the two rows
against data_1000.txt by grepping the file. Lesson: never assert a
count without grepping the data first.

## 2026-10-19 - M1 Step 2.6: scan stats all zero

Trying to: produce the stats block M1 section 7 requires in
scan --json.

Tried: HeapFile.scan() reads pages via DiskManager.read_page directly,
not through BufferPool.fetch_page, so the pool's hits/misses/evictions
counters stay at zero for a scan.

Broke: scan --json returns {"hits": 0, "misses": 0, ...} even though
the scan obviously read pages.

Decision (for now): documented as a known limitation. M1 section 4.1
forbids materialising a scan into a list, and the current HeapFile.scan
obeys that rule, but the page reads go through the raw DiskManager
rather than the pool. Replacing DiskManager reads with
BufferPool.fetch_page calls inside scan is the correct fix and will be
done before the next checkpoint. Lesson: "stats are zero" is the
visible symptom of "the wrong layer is counting." M2 will use the
buffer pool exclusively, so this is fixed by construction when M2
lands.

## 2026-10-24 - M1 close-out: page layout and buffer example verification

Trying to: lock in DESIGN_M1.md with the byte-level page example and the
buffer-pool worked example from M1 section 3.2 and 5.3.

Tried: reproduced the section 3.2 layout by hand (empty page, insert 13,
insert 20, insert 13, delete slot 1) against SlottedPage, and the section
5.3 trace (B = 3, pages 1 2 3 1 4 2) against BufferPool under both LRU
and CLOCK.

Broke: nothing - both examples reproduce exactly. The point of logging
this entry is that the AI-assisted first draft of the buffer example
had the CLOCK trace wrong (it reported 1 hit, 5 misses, 2 evictions,
identical to LRU, because the AI had implemented CLOCK as "LRU with
ref bits" and never advanced the hand past pinned frames). The test
caught it because the expected totals from the spec are 2 hits, 4
misses, 1 eviction. Fix: rewritten _victim() to sweep with a counted
"2 * B" bound and to advance the hand past every frame it inspects,
not just the chosen one. Lesson: if a "different" policy produces
identical statistics to another policy on a worked example, it is the
same policy.

## 2026-11-02 - M2 open: join algorithms and duplicate keys

Trying to: implement the three join algorithms with the exact worked
examples from M2 section 2.

Tried: NestedLoopJoin, SortMergeJoin, HashJoin each in their own class,
sharing a _JoinBase for condition parsing and schema concatenation.

Broke: SortMergeJoin on the section 2.8 duplicate-key example
(R.b=[1,2,2,3], S.b=[2,2,4]) produced 2 rows instead of 4. The AI's
first draft advanced both cursors after finding a matching key, which
loses the cross product. The spec explicitly says: "If your
implementation produces two rows, your cursor advanced instead of
rewinding." Fix: on a matching key, record the extent of both runs,
emit the cross product, then advance both past their runs. Verified
by test_sort_merge_duplicate_keys_section_2_8.

## 2026-11-20 - M2 joins: comparison counts reveal correctness bug

Trying to: verify BNL comparison count against |R| * |S| (M2 2.7).

Tried: ran 108 join measurements across 3 sizes x 3 sizes x 4 Bs.

Broke: for large inner sizes (m > 100*(B-2)) measured comparisons fell
below |R| * |S|, and output row counts fell. BNL produced fewer rows
than it should. Root cause: block-split drains the inner child and
cannot re-read it for the next outer block. Correctness bug, not an
optimisation.

Found by: comparing measured comparison and output-row counts against
the formula. Visible at n=2000, m=500, B=4 (1,000 output rows instead
of 10,000).

Documented in REPORT_M2 section 1.

## 2026-11-20 - M2 joins: page reads are 0 because joins bypass buffer pool

Trying to: fill in the page-read column of the M2 section 5 table.

Tried: ran all three joins, printed counters()["page_reads"].

Broke: every row reports reads=0. The joins materialise inputs in
Python lists and never call BufferPool.fetch_page. The formula assumes
they stream through the pool.

Found by: noticing all 108 measured rows had reads=0 while the formula
predicted 15-2400.

Documented in REPORT_M2 section 1.

## 2026-11-21 - M3: "<>" is broken end-to-end, and it took three modules to see it

Trying to: get the M3 condition parser to accept `<>` in a WHERE clause.

Tried: assumed `sql/lexer.py` emitting `<>` as a single token was
sufficient, since the SQL lexer is used for the whole query.

Broke: `python -m pytest tests/test_m3_parser.py` failed on any query
with `<>` in WHERE, with `lexical: unexpected character '*'` (HAVING)
or a similar parse error. Root cause: `sql/parser.py`'s `_parse_condition`
was delegating to `engine/condition.parse_condition`, which uses
`engine/lexer.Lexer`. That P1-era lexer has `MULTI_OPS = ["!=", "<=",
">="]` - no `<>`. And its `_cmp` dict has no `"<>"` key either.

Found by: the M3 test suite and a manual `parse_condition('a <> b')`
that threw before reaching the resolver.

Fix: (a) added `"<>"` to `engine/lexer.MULTI_OPS`, (b) added `"<>":
a != b` to `_cmp` in both `engine/condition.py` and
`engine/operators.py`, (c) replaced the M3 parser's delegation to
`engine.condition` with its own SQL condition parser so HAVING can
handle `COUNT(*)`.

Lesson: a token being handled by one lexer does not mean it is handled
by every lexer in the pipeline. Test the whole path, not the pieces.

## 2026-11-21 - M3: AND/OR/NOT were case-sensitive inside engine.condition

Trying to: parse uppercase SQL like `WHERE a > 5 AND b < 3`.

Tried: assumed the P1 condition parser accepted `AND` because it worked
on lowercase.

Broke: `parse_condition('R.a > 5 AND R.b < 3')` parsed `R.a > 5` and
then stopped, raising "trailing input in condition" because
`_CondParser._and` checks `self._peek().text == "and"` (lowercase
only). The P1 lexer does not uppercase identifiers, so `AND` reaches
`_CondParser` as `IDENT("AND")` and the comparison fails.

Found by: M3 test case with uppercase `AND`, which is what SQL users
actually type.

Fix: changed `_or`, `_and`, `_not` in `engine/condition.py` (and the
matching methods in `engine/parser.py`) to compare `self._peek().text.lower()`.
Lesson: keywords are case-insensitive in the lexer, but the parser
that consumes them must also be case-insensitive. Those are two
different files.

## 2026-11-21 - M3: HAVING COUNT(*) failed because engine.condition cannot represent `*`

Trying to: parse `GROUP BY did HAVING COUNT(*) > 1`.

Tried: kept the M3 parser's delegation of HAVING to
`engine.condition.parse_condition` (the same path WHERE was using).

Broke: `engine/lexer.Lexer` has no token for `*`, so `COUNT(*)` failed
lexically before reaching the condition parser. The error was `lexical:
unexpected character '*' at position 8`.

Found by: test_m3_parser.py::test_group_by_having failing on the exact
HAVING example from M3 section 5.4.

Fix: replaced WHERE and HAVING in sql/parser.py with a SQL-specific
condition parser (`_CondParser` inside sql/parser.py) that uses
`sql.lexer.SqlLexer` tokens and accepts `COUNT(*)`, `SUM(col)`, etc.
as operands. The engine.condition parser is now used only by the P1
week-1 regression tests, where it belongs.

Lesson: reusing a parser written for a smaller language is a trap.
The saved lines are not worth the wrong-shaped error messages.

## 2026-11-21 - M4: rewrite rule 2 silently never fired (column names instead of table names)

Trying to: get M4's rewrite rule 2 (push a select below a join) to
actually push `Emp.age > 50` below the join in Q5.

Tried: applied the full rewrite stack, printed `explain` for Q5.

Broke: Q5's plan had `filter (Emp.age > 50)` sitting above the join,
identical with rule 1 on and off. The predicate should have been pushed
to the Emp side.

Root cause: `_collect_columns` in opt/rewrite.py walked the subtree
collecting column names *from conditions only*. For a bare scan with
no condition, it returned the empty set, so the predicate-side
classifier concluded that `Emp.age > 50` referenced neither side and
left it above the join.

Found by: instrumenting the rewrite with a per-candidate print, and
seeing that `_push_selects`'s `_predicate_side` returned `"both"` for
a predicate that referenced only one table.

Fix: renamed `_collect_columns` to `_collect_tables`, changed it to
return the *table names* of every scan in a subtree, and rewrote
`_predicate_side` to check whether the predicate's qualified table
names are a subset of the left side's tables or the right side's
tables. That is the right abstraction: a predicate belongs to a side
if it references only that side's tables.

Lesson: "which side does this predicate belong to" is a question about
tables, not columns. The AI's original code answered a column-shaped
question and got a silent "neither".

## 2026-11-21 - M4: every join estimate silently fell back to 0.1

Trying to: verify M4's join selectivity formula on Q5 and Q6.

Tried: compared Q5 and Q6 before and after the rule-2 fix. They
differed by 5x, and both were far too large (Q5 estimated 17,500 output
rows for a join of 3,500 and 50 with a foreign-key condition, which
should be 3,500).

Broke: the join estimator's ndv lookup never found the key column's
statistics, so it fell through to `sel.selectivity_default() = 0.1`,
which gives `3500 * 50 * 0.1 = 17,500`. The correct estimate is
`3500 * 50 * 1/max(ndv(Emp.did), ndv(Dept.did)) = 3500 * 50 * 1/50 = 3500`.

Root cause: `_physical_scan` puts a `_stats` field on each seqscan
node, but `_build_physical`'s unary branches (filter, project, sort,
limit, distinct, group_by) discarded `_stats` when building the parent
node. By the time the join's `_estimate_join_rows` ran, `left._stats`
was empty and `right._stats` had no `did` column.

Found by: printing the per-candidate costs inside `_build_physical`
before and after the `_stats` fix. The join's est_rows dropped from
17,500 to 3,500 in Q5 and Q6, from 20,000 to 8,000 in Q9, from 5,000
to 1,000 in Q10, from 467 to 187 in Q11, and from 125 to 250 in Q19
(Q19 got a higher estimate but its q-error improved because the actual
row count is 175).

Fix: every unary branch in `_build_physical` now copies `_stats` from
its child into its own output; `_physical_join` merges both sides'
column stats (taking the max ndv per column) into its own `_stats`.

Lesson: an estimator has state that must be threaded through the whole
plan tree, not just stored at the leaves. A single missing copy at one
unary node makes every join above it silently fall back to a default.
The default is not visible in the plan unless you print the ndv the
estimator actually used.

## 2026-11-21 - M4: group_by estimated its output as its input

Trying to: get Q12, Q13, Q15, Q16 (all aggregations) to estimate
sensibly.

Tried: assumed `_build_physical`'s group_by case set `est_rows` to the
number of groups.

Broke: q-errors of 115, 10000, 833, 125 on the four aggregation
queries. `est_rows` was being copied from the child, so a group_by
of 10,000 input rows was estimated to output 10,000 rows instead of
the number of distinct groups.

Found by: the per-query q-error printout from `measure_m4.py`.

Fix: `_build_physical`'s group_by branch now computes
`min(prod(ndv(grouping_key)), child_rows)` as its output estimate,
using the `_stats` field from the child. That gives 5 for Q13
(`GROUP BY region`, ndv 5) and 50 for Q14 (`GROUP BY did`, ndv 50).

Note: this fix does not make Q12 and Q15's q-errors go to 1 because
their *pre-aggregation* nodes are still off. Those are estimation
issues in the join path, not the group_by path. The Q14 execution
error (unknown column n) is a separate M3↔M2 schema-name mismatch,
documented in REPORT_M4 section 1 and DESIGN_M4 section 8.

Lesson: every blocking operator changes the row count. group_by and
distinct both output the number of groups / distinct tuples, not
their input count. The estimator must model that explicitly.