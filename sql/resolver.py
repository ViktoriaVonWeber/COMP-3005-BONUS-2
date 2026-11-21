# sql/resolver.py - name resolution, SELECT * expansion, aggregate rules,
# type rules, set-op compatibility. M3 sections 3, 3.1, 3.2, 4.
#
# The resolver walks the AST in place. After resolve(stmt, catalog):
#   - Every ColumnRef has .resolved_name = "Table.col" (fully qualified).
#   - Every SelectBlock has .out_schema = [(qualified_name, type), ...].
#   - Every TableRef/SubqueryRef has .resolved_schema.
#   - SELECT * lists have been expanded into SelectItems.
#   - Aggregate placement and type rules have been checked.
#
# Errors are EngineError with category in {name, ambiguity, type,
# aggregate, schema} and a "line L, column C" position when available.

from sql import ast
from engine.errors import EngineError


# ------------------------------------------------------------
# Public entry point
# ------------------------------------------------------------

def resolve(stmt, catalog):
    """Resolve a single parsed statement in place. Returns stmt."""
    if isinstance(stmt, ast.CreateTable):
        return _resolve_create(stmt, catalog)
    if isinstance(stmt, ast.Insert):
        return _resolve_insert(stmt, catalog)
    if isinstance(stmt, ast.Query):
        return _resolve_query(stmt, catalog)
    raise EngineError("schema", f"cannot resolve {type(stmt).__name__}")


# ------------------------------------------------------------
# DDL / DML
# ------------------------------------------------------------

def _resolve_create(stmt, catalog):
    # Nothing to resolve; CREATE just defines names. The CLI's
    # execute path is responsible for actually creating the table.
    return stmt


def _resolve_insert(stmt, catalog):
    entry = catalog.get_table(stmt.table)
    schema = entry["schema"]
    if len(stmt.values) != len(schema):
        raise EngineError("schema",
            f"INSERT into {stmt.table}: expected {len(schema)} values, got {len(stmt.values)}")
    out = []
    for (colname, ctype, clen), v in zip(schema, stmt.values):
        if ctype == "INT":
            if not isinstance(v, int):
                raise EngineError("type",
                    f"INSERT into {stmt.table}.{colname}: expected INT, got {type(v).__name__}")
        elif ctype == "VARCHAR":
            if not isinstance(v, str):
                raise EngineError("type",
                    f"INSERT into {stmt.table}.{colname}: expected VARCHAR, got {type(v).__name__}")
            if len(v.encode("utf-8")) > clen:
                raise EngineError("schema",
                    f"INSERT into {stmt.table}.{colname}: value too long for VARCHAR({clen})")
        out.append(v)
    stmt.values = out
    return stmt


# ------------------------------------------------------------
# Query (set-op trees)
# ------------------------------------------------------------

def _resolve_query(q, catalog):
    for b in q.blocks:
        if isinstance(b, ast.Query):
            _resolve_query(b, catalog)
        elif isinstance(b, ast.SelectBlock):
            _resolve_select_block(b, catalog)
        else:
            raise EngineError("schema", f"bad query block {type(b).__name__}")
    # Set-op schema compatibility (M3 3.2 last sentence).
    for i, op in enumerate(q.setops):
        left = _block_schema(q.blocks[i])
        right = _block_schema(q.blocks[i + 1])
        if len(left) != len(right):
            raise EngineError("schema",
                f"set operation {op}: left has {len(left)} columns, right has {len(right)}")
        for (ln, lt), (rn, rt) in zip(left, right):
            if lt != rt:
                raise EngineError("schema",
                    f"set operation {op}: type mismatch {lt} vs {rt}")
    q.out_schema = _block_schema(q.blocks[0])
    return q


def _block_schema(block):
    if isinstance(block, ast.SelectBlock):
        return block.out_schema
    if isinstance(block, ast.Query):
        return block.out_schema
    raise EngineError("schema", f"no schema for {type(block).__name__}")


# ------------------------------------------------------------
# SELECT block
# ------------------------------------------------------------

def _resolve_select_block(b, catalog):
    # 1. Build scope from FROM
    scope_entries = []  # list of (alias, schema)
    b.from_list = _resolve_from_list(b.from_list, catalog, scope_entries)

    scope = _Scope(scope_entries)

    # 2. Expand SELECT *
    expanded = []
    for item in b.select_list:
        if isinstance(item, ast.Star):
            for alias, schema in scope_entries:
                for colname, ctype in schema:
                    cr = ast.ColumnRef(alias, colname.split(".")[-1])
                    cr.resolved_name = colname
                    expanded.append(ast.SelectItem(cr, None))
        else:
            expanded.append(item)
    b.select_list = expanded

    # 3. Resolve WHERE
    if b.where is not None:
        _resolve_condition(b.where, scope, allow_aggregates=False)

    # 4. Resolve GROUP BY
    for i, col in enumerate(b.group_by):
        _resolve_column(col, scope)
        b.group_by[i] = col

    # 5. Aggregate placement rules (M3 3.1)
    is_aggregating = bool(b.group_by) or _has_aggregate(b)
    if is_aggregating and not b.group_by:
        pass  # allowed: whole-result aggregate (COUNT(*) FROM T)

    # 6. Resolve HAVING
    if b.having is not None:
        if not is_aggregating:
            raise EngineError("aggregate",
                "HAVING requires GROUP BY or an aggregate in the select list")
        _resolve_condition(b.having, scope, allow_aggregates=True)

    # 7. Resolve SELECT list items
    grouping_cols = set()
    for col in b.group_by:
        grouping_cols.add(col.resolved_name)
    for item in b.select_list:
        expr = item.expr
        if isinstance(expr, ast.Aggregate):
            _resolve_aggregate(expr, scope)
        elif isinstance(expr, ast.ColumnRef):
            _resolve_column(expr, scope)
            if is_aggregating and expr.resolved_name not in grouping_cols:
                raise EngineError("aggregate",
                    f"column {expr.resolved_name} must appear in GROUP BY "
                    f"or be inside an aggregate")

    # 8. Resolve ORDER BY (aliases first, then scope)
    select_aliases = {}
    for item in b.select_list:
        if item.alias:
            select_aliases[item.alias] = item
    for i, (col, direction) in enumerate(b.order_by):
        if col.qualifier is None and col.name in select_aliases:
            item = select_aliases[col.name]
            if isinstance(item.expr, ast.ColumnRef):
                col.resolved_name = item.expr.resolved_name
            elif isinstance(item.expr, ast.Aggregate):
                col.resolved_name = _aggregate_out_name(item.expr, item.alias)
            else:
                col.resolved_name = col.name
        else:
            _resolve_column(col, scope)
        b.order_by[i] = (col, direction)

    # 9. Compute output schema
    out = []
    for item in b.select_list:
        expr = item.expr
        if isinstance(expr, ast.ColumnRef):
            ctype = scope.type_of(expr.resolved_name)
            name = item.alias or expr.resolved_name
            out.append((name, ctype))
        elif isinstance(expr, ast.Aggregate):
            ctype = _aggregate_type(expr, scope)
            name = _aggregate_out_name(expr, item.alias)
            out.append((name, ctype))
        else:
            raise EngineError("schema", f"bad select item {type(expr).__name__}")
    b.out_schema = out
    return b


def _resolve_from_list(from_list, catalog, scope_entries):
    """Mutates scope_entries with (alias, schema). Returns the from list
    with resolved_schema on each ref."""
    out = []
    for ref in from_list:
        if isinstance(ref, ast.TableRef):
            entry = catalog.get_table(ref.name)
            schema = [(f"{ref.alias}.{c}", t) for (c, t, _n) in entry["schema"]]
            ref.resolved_schema = schema
            scope_entries.append((ref.alias, schema))
            out.append(ref)
        elif isinstance(ref, ast.SubqueryRef):
            _resolve_query(ref.query, catalog)
            schema = [(f"{ref.alias}.{c.split('.')[-1]}", t)
                      for (c, t) in ref.query.out_schema]
            ref.resolved_schema = schema
            scope_entries.append((ref.alias, schema))
            out.append(ref)
        elif isinstance(ref, ast.Join):
            left_scope = []
            left = _resolve_from_list([ref.left], catalog, left_scope)
            right_scope = []
            right = _resolve_from_list([ref.right], catalog, right_scope)
            ref.left = left[0]
            ref.right = right[0]
            scope_entries.extend(left_scope)
            scope_entries.extend(right_scope)
            combined = _Scope(scope_entries)
            _resolve_condition(ref.condition, combined, allow_aggregates=False)
            ref.resolved_schema = ref.left.resolved_schema + ref.right.resolved_schema
            out.append(ref)
        else:
            raise EngineError("schema", f"bad FROM item {type(ref).__name__}")
    return out


# ------------------------------------------------------------
# Scope
# ------------------------------------------------------------

class _Scope:
    def __init__(self, entries):
        # entries: list of (alias, schema)
        # Detect duplicate aliases
        seen = set()
        for alias, _schema in entries:
            if alias in seen:
                raise EngineError("name", f"duplicate alias {alias}")
            seen.add(alias)
        self.entries = entries

    def resolve(self, colref):
        """Return the fully-qualified name and type for colref."""
        if colref.qualifier is not None:
            for alias, schema in self.entries:
                if alias == colref.qualifier:
                    for qn, t in schema:
                        if qn.split(".")[-1] == colref.name:
                            return qn, t
                    raise EngineError("name",
                        f"table {colref.qualifier} has no column {colref.name}")
            raise EngineError("name", f"unknown table alias {colref.qualifier}")
        # unqualified
        matches = []
        for alias, schema in self.entries:
            for qn, t in schema:
                if qn.split(".")[-1] == colref.name:
                    matches.append((qn, t))
        if len(matches) == 0:
            raise EngineError("name", f"unknown column {colref.name}")
        if len(matches) > 1:
            candidates = ", ".join(m[0] for m in matches)
            raise EngineError("ambiguity",
                f"column {colref.name} is ambiguous: {candidates}")
        return matches[0]

    def type_of(self, qualified_name):
        for _alias, schema in self.entries:
            for qn, t in schema:
                if qn == qualified_name:
                    return t
        raise EngineError("name", f"unknown column {qualified_name}")


# ------------------------------------------------------------
# Column / aggregate / condition resolution
# ------------------------------------------------------------

def _resolve_column(colref, scope):
    qn, t = scope.resolve(colref)
    colref.resolved_name = qn
    colref.resolved_type = t
    return colref


def _resolve_aggregate(agg, scope):
    if agg.arg == "*":
        if agg.kind != "count":
            raise EngineError("aggregate", f"{agg.kind.upper()}(*) is not valid")
        return agg
    if isinstance(agg.arg, ast.ColumnRef):
        _resolve_column(agg.arg, scope)
        if agg.kind in ("sum", "avg") and agg.arg.resolved_type != "INT":
            raise EngineError("type",
                f"{agg.kind.upper()} requires INT, got {agg.arg.resolved_type}")
        return agg
    raise EngineError("aggregate", f"bad aggregate argument {type(agg.arg).__name__}")


def _aggregate_type(agg, scope):
    if agg.kind in ("count",):
        return "INT"
    if agg.kind == "avg":
        return "FLOAT"
    if agg.arg == "*":
        return "INT"
    return agg.arg.resolved_type


def _aggregate_out_name(agg, alias):
    if alias is not None:
        return alias
    if agg.arg == "*":
        return f"{agg.kind}_star"
    return f"{agg.kind}_{agg.arg.resolved_name.replace('.', '_')}"


def _has_aggregate(b):
    for item in b.select_list:
        if isinstance(item.expr, ast.Aggregate):
            return True
    if b.having is not None:
        return _cond_has_aggregate(b.having)
    return False


def _cond_has_aggregate(node):
    if isinstance(node, ast.AggRef):
        return True
    if isinstance(node, (ast.CondAnd, ast.CondOr)):
        return _cond_has_aggregate(node.left) or _cond_has_aggregate(node.right)
    if isinstance(node, ast.CondNot):
        return _cond_has_aggregate(node.child)
    if isinstance(node, ast.CondCmp):
        return _cond_has_aggregate(node.left) or _cond_has_aggregate(node.right)
    return False


def _resolve_condition(node, scope, allow_aggregates):
    if isinstance(node, ast.CondAnd) or isinstance(node, ast.CondOr):
        _resolve_condition(node.left, scope, allow_aggregates)
        _resolve_condition(node.right, scope, allow_aggregates)
        return
    if isinstance(node, ast.CondNot):
        _resolve_condition(node.child, scope, allow_aggregates)
        return
    if isinstance(node, ast.CondCmp):
        _resolve_cond_operand(node.left, scope, allow_aggregates)
        _resolve_cond_operand(node.right, scope, allow_aggregates)
        _check_cmp_types(node)
        return
    raise EngineError("schema", f"bad condition {type(node).__name__}")


def _resolve_cond_operand(op, scope, allow_aggregates):
    if isinstance(op, ast.Literal):
        return
    if isinstance(op, ast.ColumnRef):
        _resolve_column(op, scope)
        return
    if isinstance(op, ast.AggRef):
        if not allow_aggregates:
            raise EngineError("aggregate",
                "aggregate not allowed here (only in SELECT or HAVING)")
        _resolve_aggregate(op, scope)
        return
    raise EngineError("schema", f"bad operand {type(op).__name__}")


def _check_cmp_types(cmp):
    lt = _operand_type(cmp.left)
    rt = _operand_type(cmp.right)
    if lt is None or rt is None:
        return
    if lt != rt and not (lt in ("INT", "FLOAT") and rt in ("INT", "FLOAT")):
        raise EngineError("type",
            f"cannot compare {lt} to {rt}")


def _operand_type(op):
    if isinstance(op, ast.Literal):
        return "INT" if isinstance(op.value, int) else "VARCHAR"
    if isinstance(op, ast.ColumnRef):
        return getattr(op, "resolved_type", None)
    if isinstance(op, ast.AggRef):
        if op.kind == "count":
            return "INT"
        if op.kind == "avg":
            return "FLOAT"
        if op.arg == "*":
            return "INT"
        return getattr(op.arg, "resolved_type", None)
    return None