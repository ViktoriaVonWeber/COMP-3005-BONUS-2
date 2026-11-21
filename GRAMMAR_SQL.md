# DESIGN_M3 — SQL Front End

## 1. Scope and resolution algorithm

The resolver in `sql/resolver.py` walks the AST in place after parsing
and before translation. Responsibilities:

- Building a scope from the FROM clause.
- Expanding `SELECT *`.
- Resolving every column reference to a fully qualified name.
- Enforcing aggregate placement rules (M3 §3.1).
- Enforcing type rules (M3 §3.2).
- Checking set-op column compatibility (M3 §3.2).

### Scope construction

Each `TableRef` contributes `(alias, schema)` to a scope list, where
`alias` is the AS-name if given, otherwise the table name. Each
`SubqueryRef` contributes `(alias, schema)`, where `schema` is the
subquery's `out_schema` with the alias prepended to each column name.

A `JOIN` node is descended into: the left and right refs are resolved
first, their scopes are combined, and the ON-condition is resolved
against the combined scope.

Duplicate aliases are caught at scope-construction time:
`FROM Emp, Emp` raises `duplicate alias Emp`. `FROM Emp e, Emp m` is fine.

### Column resolution

For a `ColumnRef` with a qualifier `t.c`:
- Find the scope entry whose alias is `t`. If none, `unknown table alias t`.
- Find the column `c` in that entry. If none, `table t has no column c`.

For an unqualified `ColumnRef` `c`:
- Collect every `(qualified_name, type)` in the scope whose last
  component is `c`.
- Exactly one: use it.
- Zero: `unknown column c`.
- More than one: `ambiguous column c: T1.c, T2.c`.

After resolution, `ColumnRef.resolved_name` holds the fully qualified
name and `ColumnRef.resolved_type` holds the column's type.

### SELECT * expansion

Every `Star` in the SELECT list is replaced by one `SelectItem` per
column of every scope entry, in FROM order then schema order. Each
expanded item's `ColumnRef` has `resolved_name` and `resolved_type`
filled in immediately.

## 2. Bag semantics decisions

M3 §4 specifies bags throughout. Where duplicates are eliminated:

- **`project`** does not eliminate duplicates. It never emits a
  `distinct` node on its own.
- **`DISTINCT`** in the SELECT list emits a `distinct` node above the
  `project` node.
- **`UNION ALL`** emits a `union_all` node; no deduplication.
- **`UNION`** emits a `union` node, which deduplicates at execution.
- **`INTERSECT`** and **`EXCEPT`** emit `intersect` and `except` nodes;
  both are set-semantic per M3 §4 and M2 §2.10.
- **`GROUP BY`** produces one row per group; no `distinct` node is
  added above it.
- **A plain `SELECT`** with no DISTINCT never emits `distinct`.

The translator's `_translate_block` makes these decisions in that order:
FROM → WHERE → GROUP BY → HAVING → SELECT (project, then distinct if
written) → ORDER BY → LIMIT.

## 3. Aggregate naming rule

An aggregate in the SELECT list gets an output name as follows:

1. If the item has an explicit `AS alias`, use the alias.
2. Otherwise, if the aggregate is `COUNT(*)`, use `count_star`.
3. Otherwise, use `<kind>_<qualified_column_with_dots_replaced_by_underscores>`.

| Written                              | Output name        |
|--------------------------------------|--------------------|
| `COUNT(*)`                           | `count_star`       |
| `COUNT(*) AS n`                      | `n`                |
| `COUNT(Emp.eid)`                     | `count_Emp_eid`    |
| `SUM(Emp.salary)`                    | `sum_Emp_salary`   |
| `AVG(salary)` (with Emp in FROM)     | `avg_Emp_salary`   |
| `MAX(Dept.budget) AS maxb`           | `maxb`             |

Enforced in `sql/resolver.py::_aggregate_out_name` and
`sql/translator.py::_aggregate_out_name`. The two implementations agree
by construction.

## 4. Deviations from the M3 §2 sketch

1. **Bare table aliases.** M3 §2 writes `table_ref ::= ident [ AS ident ]`,
   but examples in M3 §5.2 use bare aliases (`FROM Emp e JOIN Dept d`).
   This grammar permits both `ident alias` and `ident AS alias`.
   Enforced in `sql/parser.py::_parse_table_ref`.

2. **`!=` accepted as a synonym for `<>`.** M3 §1 lists only `<>`;
   `sql/lexer.py` also emits `!=`, and `_CondParser._cmp` normalizes
   `!=` to `<>` so downstream code sees one form.

3. **Parenthesized query in `select_block`.** M3 §2 shows
   `select_block ::= ... | "(" query ")"`. Our parser returns the inner
   `Query` directly rather than wrapping it in a `SelectBlock`, so
   `Query.blocks` may contain either `SelectBlock` or nested `Query`.
   Resolver and translator both handle this.

4. **FROM list: alternatives, not composable.** The two FROM
   alternatives in M3 §2 are mutually exclusive in this grammar.
   `FROM A, B JOIN C ON ...` is a syntax error, matching the sketch.

5. **No arithmetic expressions.** M3 §2 has no `+ - * /` in the
   grammar; the only operators are comparisons.

## 5. What the parser emits

Every condition node is `sql.ast.CondCmp`, `CondAnd`, `CondOr`, or
`CondNot`, with operands being `ColumnRef`, `Literal`, or `AggRef`.
The parser never produces an `engine.tree.Cmp`.

After parsing, no name resolution has occurred. `sql/resolver.py`
makes every column fully qualified before translation.