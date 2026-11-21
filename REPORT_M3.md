# REPORT_M3 - SQL Front End

Machine: 2019 MacBook (macOS, Darwin 24.x)
Language: Python 3.15.2
Date: 2026-11-21

M3 §9 does not require a REPORT document. It requires:
- `GRAMMAR_SQL.md` (delivered; EBNF, precedence/associativity table,
  and the five documented deviations from the M3 §2 sketch).
- `DESIGN_M3.md` (delivered; scope and resolution algorithm, bag
  semantics decisions, aggregate naming rule).
- Tests for all fifteen cases from M3 §7 (delivered in
  `tests/test_m3_required.py`; all fifteen pass).
- `DESIGN_LOG.md` entries (delivered separately).

This document is provided as an addition, covering what was built and
where it differs from the M3 §2 sketch.

## 1. What M3 delivers

Six modules under `sql/`:

- `sql/lexer.py` - hand-written scanner (no regex, no parser generators).
  Handles case-insensitive keywords, case-sensitive identifiers,
  single-quoted strings with `''` escape, `--` comments, maximal-munch
  operators including `<>` and `!=`, and records line/column on every
  token.
- `sql/ast.py` - AST nodes. Statements (`CreateTable`, `Insert`,
  `Query`), blocks (`SelectBlock`), select items (`Star`, `SelectItem`,
  `Aggregate`, `ColumnRef`), FROM items (`TableRef`, `SubqueryRef`,
  `Join`), and SQL-level condition nodes (`CondCmp`, `CondAnd`,
  `CondOr`, `CondNot`, `Literal`, `AggRef`).
- `sql/parser.py` - recursive-descent parser. Own condition parser for
  WHERE and HAVING (separate from `engine.condition`, so it can handle
  `COUNT(*)` in HAVING and `<>`). Public entry point `parse_sql(text)`
  returns a list of statement nodes.
- `sql/resolver.py` - name resolution in place: builds a scope from the
  FROM clause, resolves every `ColumnRef` to a fully qualified name,
  expands `SELECT *`, enforces aggregate placement, checks types,
  checks set-op column compatibility. Populates `ColumnRef.resolved_name`,
  `ColumnRef.resolved_type`, and `SelectBlock.out_schema`.
- `sql/translator.py` - emits the logical plan JSON in the canonical
  order from M3 §5. No optimization. Every column reference is fully
  qualified.
- `sql/cli.py` - `parse` (indented AST), `compile` (JSON plan), `check`
  (errors only, exit 0/1).

## 2. Required cases (M3 §7)

All fifteen cases pass. `python -m pytest tests/test_m3_required.py -v`
prints 15 passed. The tests assert plan shape (node names and child
structure) and error categories. They do not assert exact JSON field
ordering, because the spec says "comparing your logical plans
structurally."

## 3. Deviations from M3 §2 sketch

Documented in detail in `GRAMMAR_SQL.md` §4. Summarized here:

1. **Bare table aliases.** `FROM Emp e` is accepted as well as
   `FROM Emp AS e`. M3 §2 writes `ident [ AS ident ]`, but M3 §5.2 uses
   bare aliases in examples, so both are supported.

2. **`!=` as synonym for `<>`.** M3 §1 lists only `<>`. `sql/lexer.py`
   emits both, and the parser normalizes `!=` to `<>`. Symmetric with
   the P1 condition language.

3. **Parenthesized query in `select_block`.** M3 §2 shows
   `select_block ::= ... | "(" query ")"`. Our parser returns the inner
   `Query` directly, not a wrapper; `Query.blocks` can contain either
   `SelectBlock` or nested `Query`. Resolver and translator handle both.

4. **FROM list: alternatives, not composable.** M3 §2's two alternatives
   (comma-separated table refs, or JOIN chain) are mutually exclusive.
   `FROM A, B JOIN C ON ...` is a syntax error, matching the sketch.

5. **No arithmetic expressions.** M3 §2 has no `+ - * /` in the grammar.
   `SELECT a + 1 FROM R` is a syntax error.

## 4. Known limitations

- **Aggregate aliases do not reach the executor.** A query like
  `SELECT did, COUNT(*) AS n FROM Emp GROUP BY did` compiles to a plan
  whose project node references `n`, but M2's group_by output schema
  names the aggregate `count_star`. The executor's `Project.open()`
  fails with `unknown column n`. Q14 in the M4 workload demonstrates
  this. Fixing it requires either (a) the M3 translator rewriting the
  project's `n` to the group_by's output name, or (b) M2's group_by
  accepting the alias, or (c) the resolver rewriting the alias on the
  AST before translation. Option (a) is the cleanest but was out of
  time.

- **Parenthesized conditions do not reach the executor.** M3's condition
  parser correctly builds an AST for `WHERE (a = 1 OR b = 2)`, but the
  translator emits `((a = 1 or b = 2))` with parentheses, and M2's
  condition parser (`engine/condition.py`, P1-era) does not handle
  parentheses. Q20 in the M4 workload demonstrates this. The fix is
  either to strip redundant parentheses in
  `sql/translator.py::_condition_to_string` (only outer ones are
  needed to preserve precedence, and M2's parser handles AND/OR at
  the same precedence as SQL) or to teach `engine/condition.py` about
  parentheses. Neither was done.

## 5. Not implemented

- **INSERT and CREATE TABLE execution.** `sql compile` emits ASTs for
  both; running them against the catalog is M1's job (`storage cli`),
  and the spec does not ask M3 to wire them up.
- **Index access paths, FROM-subquery correlation, EXISTS/IN
  subqueries.** Not in M3 §2's grammar; not implemented.
- **Multi-error recovery.** M3 §6 says panic mode is enough; we report
  the first error and stop.