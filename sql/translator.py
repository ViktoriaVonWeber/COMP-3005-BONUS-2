# sql/translator.py - AST to logical plan JSON. M3 section 5.
#
# Canonical order (M3 5):
#   1 FROM      scans, renames, joins, cross
#   2 WHERE     one select node
#   3 GROUP BY  group_by node
#   4 HAVING    select node above group_by
#   5 SELECT    project, then distinct if written
#   6 ORDER BY  sort
#   7 LIMIT     limit
#   8 set ops   left-assoc at the top
#
# The translator makes no optimization decisions. Module 4 does that.
# Every column reference is fully qualified (the resolver guarantees it).

from sql import ast
from engine.errors import EngineError


def translate(stmt):
    if isinstance(stmt, ast.CreateTable):
        return _translate_create(stmt)
    if isinstance(stmt, ast.Insert):
        return _translate_insert(stmt)
    if isinstance(stmt, ast.Query):
        return _translate_query(stmt)
    raise EngineError("schema", f"cannot translate {type(stmt).__name__}")


# ------------------------------------------------------------
# DDL / DML
# ------------------------------------------------------------

def _translate_create(stmt):
    return {
        "op": "create_table",
        "name": stmt.name,
        "schema": [[c, t, n] for (c, t, n) in stmt.columns],
    }


def _translate_insert(stmt):
    return {
        "op": "insert",
        "table": stmt.table,
        "values": stmt.values,
    }


# ------------------------------------------------------------
# Query
# ------------------------------------------------------------

def _translate_query(q):
    # Translate first block
    plan = _translate_block(q.blocks[0])
    # Left-assoc set ops
    for op, right_block in zip(q.setops, q.blocks[1:]):
        right_plan = _translate_block(right_block)
        plan = {
            "op": op,
            "left": plan,
            "right": right_plan,
        }
    return plan


def _translate_block(b):
    if isinstance(b, ast.Query):
        return _translate_query(b)
    if not isinstance(b, ast.SelectBlock):
        raise EngineError("schema", f"bad block {type(b).__name__}")

    # 1. FROM
    plan = _translate_from_list(b.from_list)

    # 2. WHERE
    if b.where is not None:
        plan = {
            "op": "select",
            "condition": _condition_to_string(b.where),
            "child": plan,
        }

    # 3. GROUP BY
    grouping_cols = [c.resolved_name for c in b.group_by]
    if grouping_cols or _block_has_aggregate(b):
        aggs = []
        for item in b.select_list:
            if isinstance(item.expr, ast.Aggregate):
                aggs.append(_aggregate_spec(item.expr, item.alias))
        # Aggregates in HAVING that aren't in SELECT also need to be computed.
        # For simplicity we require them to also be computable from existing
        # aggregates; if not, we add them.
        if b.having is not None:
            for agg in _collect_having_aggregates(b.having):
                spec = _aggregate_spec(agg, None)
                if spec not in aggs:
                    aggs.append(spec)
        plan = {
            "op": "group_by",
            "mode": "hash",
            "keys": grouping_cols,
            "aggregates": aggs,
            "child": plan,
        }

        # 4. HAVING
        if b.having is not None:
            plan = {
                "op": "select",
                "condition": _condition_to_string(b.having),
                "child": plan,
            }

    # 5. SELECT: project, then distinct
    columns = []
    for item in b.select_list:
        if isinstance(item.expr, ast.ColumnRef):
            columns.append(item.alias if item.alias else item.expr.resolved_name)
        elif isinstance(item.expr, ast.Aggregate):
            # The group_by output uses the aggregate's output name.
            if item.alias:
                columns.append(item.alias)
            else:
                columns.append(_aggregate_out_name(item.expr))
        else:
            raise EngineError("schema", f"bad select expr {type(item.expr).__name__}")
    plan = {
        "op": "project",
        "columns": columns,
        "child": plan,
    }

    if b.distinct:
        plan = {"op": "distinct", "mode": "hash", "child": plan}

    # 6. ORDER BY
    if b.order_by:
        keys = []
        for col, direction in b.order_by:
            name = getattr(col, "resolved_name", col.name)
            # If it matches a select alias, use the alias name.
            for item in b.select_list:
                if item.alias == col.name and col.qualifier is None:
                    name = item.alias
                    break
            keys.append([name, direction])
        plan = {"op": "sort", "keys": keys, "child": plan}

    # 7. LIMIT
    if b.limit is not None:
        plan = {"op": "limit", "n": b.limit, "child": plan}

    return plan


# ------------------------------------------------------------
# FROM
# ------------------------------------------------------------

def _translate_from_list(from_list):
    if not from_list:
        raise EngineError("schema", "empty FROM")
    # First item
    first = from_list[0]
    plan = _translate_from_item(first)
    # Additional items become cross products (M3 5.3: comma = cross).
    for ref in from_list[1:]:
        right = _translate_from_item(ref)
        plan = {"op": "cross", "left": plan, "right": right}
    return plan


def _translate_from_item(ref):
    if isinstance(ref, ast.TableRef):
        scan = {"op": "scan", "table": ref.name}
        if ref.alias and ref.alias != ref.name:
            return {"op": "rename", "to": ref.alias, "child": scan}
        return scan
    if isinstance(ref, ast.SubqueryRef):
        inner = _translate_query(ref.query)
        return {"op": "rename", "to": ref.alias, "child": inner}
    if isinstance(ref, ast.Join):
        left = _translate_from_item(ref.left)
        right = _translate_from_item(ref.right)
        return {
            "op": "join",
            "condition": _condition_to_string(ref.condition),
            "left": left,
            "right": right,
        }
    raise EngineError("schema", f"bad FROM item {type(ref).__name__}")


# ------------------------------------------------------------
# Conditions
# ------------------------------------------------------------

def _condition_to_string(node):
    if isinstance(node, ast.CondAnd):
        return f"({_condition_to_string(node.left)} and {_condition_to_string(node.right)})"
    if isinstance(node, ast.CondOr):
        return f"({_condition_to_string(node.left)} or {_condition_to_string(node.right)})"
    if isinstance(node, ast.CondNot):
        return f"not ({_condition_to_string(node.child)})"
    if isinstance(node, ast.CondCmp):
        return f"{_operand_to_string(node.left)} {node.op} {_operand_to_string(node.right)}"
    raise EngineError("schema", f"bad condition {type(node).__name__}")


def _operand_to_string(op):
    if isinstance(op, ast.Literal):
        if isinstance(op.value, str):
            escaped = op.value.replace("'", "''")
            return f"'{escaped}'"
        return str(op.value)
    if isinstance(op, ast.ColumnRef):
        return op.resolved_name
    if isinstance(op, ast.AggRef):
        if op.arg == "*":
            return f"{op.kind}_star"
        if op.kind == "avg":
            return f"avg_{op.arg.resolved_name.replace('.', '_')}"
        return f"{op.kind}_{op.arg.resolved_name.replace('.', '_')}"
    raise EngineError("schema", f"bad operand {type(op).__name__}")


# ------------------------------------------------------------
# Aggregates
# ------------------------------------------------------------

def _aggregate_spec(agg, alias):
    """Return the [kind, arg] pair for a group_by aggregates list."""
    if agg.arg == "*":
        return ["count", "*"]
    return [agg.kind, agg.arg.resolved_name]


def _aggregate_out_name(agg):
    if agg.arg == "*":
        return f"{agg.kind}_star"
    return f"{agg.kind}_{agg.arg.resolved_name.replace('.', '_')}"


def _collect_having_aggregates(node):
    out = []
    def walk(n):
        if isinstance(n, ast.AggRef):
            out.append(n)
        elif isinstance(n, (ast.CondAnd, ast.CondOr)):
            walk(n.left); walk(n.right)
        elif isinstance(n, ast.CondNot):
            walk(n.child)
        elif isinstance(n, ast.CondCmp):
            walk(n.left); walk(n.right)
    walk(node)
    return out


def _block_has_aggregate(b):
    for item in b.select_list:
        if isinstance(item.expr, ast.Aggregate):
            return True
    return False