# exec/planner.py - build an operator tree from a physical plan JSON.
# Validates the plan before execution (M2 section 2 contract).
from engine.errors import EngineError
from exec.scan import SeqScan, Filter
from exec.pipeline import Project, Limit, UnionAll
from exec.sort import Sort
from exec.grouping import Distinct, GroupBy
from exec.joins import NestedLoopJoin, SortMergeJoin, HashJoin
from exec.setops import Union, Intersect, Except


def build_plan(spec, catalog, buffer_pool, frames):
    """Build the operator tree from a physical plan JSON. Validates on the way."""
    if not isinstance(spec, dict):
        raise EngineError("schema", f"plan node must be a dict, got {type(spec).__name__}")
    op = spec.get("op")
    if not isinstance(op, str):
        raise EngineError("schema", "plan node missing 'op' field")

    if op == "seqscan":
        table = _require(spec, "table", str, op)
        return SeqScan(table, catalog, buffer_pool)

    if op == "filter":
        cond = _require(spec, "condition", str, op)
        child = _require_child(spec, "child", op, catalog, buffer_pool, frames)
        return Filter(cond, child)

    if op == "project":
        cols = _require(spec, "columns", list, op)
        if not all(isinstance(c, str) for c in cols):
            raise EngineError("schema", f"Node '{op}': field 'columns' must be a list of strings")
        child = _require_child(spec, "child", op, catalog, buffer_pool, frames)
        return Project(cols, child)

    if op == "limit":
        n = spec.get("n")
        if not isinstance(n, int) or n < 0:
            raise EngineError("schema", f"Node '{op}': field 'n' must be a non-negative int")
        child = _require_child(spec, "child", op, catalog, buffer_pool, frames)
        return Limit(n, child)

    if op == "distinct":
        mode = _require_choice(spec, "mode", ["sort", "hash"], op)
        child = _require_child(spec, "child", op, catalog, buffer_pool, frames)
        return Distinct(mode, child)

    if op == "sort":
        keys = spec.get("keys")
        if not isinstance(keys, list) or not all(
            isinstance(k, list) and len(k) == 2 and isinstance(k[0], str) and k[1] in ("asc", "desc")
            for k in keys
        ):
            raise EngineError("schema",
                f"Node '{op}': field 'keys' must be a list of [column, 'asc'|'desc'] pairs")
        child = _require_child(spec, "child", op, catalog, buffer_pool, frames)
        return Sort([(k[0], k[1]) for k in keys], child, frames=frames)

    if op == "group_by":
        mode = _require_choice(spec, "mode", ["sort", "hash"], op)
        keys = spec.get("keys")
        if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
            raise EngineError("schema",
                f"Node '{op}': field 'keys' must be a list of strings")
        aggs = spec.get("aggregates")
        if not isinstance(aggs, list) or not all(
            isinstance(a, list) and len(a) == 2 and isinstance(a[0], str) and isinstance(a[1], str)
            for a in aggs
        ):
            raise EngineError("schema",
                f"Node '{op}': field 'aggregates' must be a list of [kind, column] pairs")
        child = _require_child(spec, "child", op, catalog, buffer_pool, frames)
        return GroupBy(mode, keys, [tuple(a) for a in aggs], child)

    if op == "nested_loop_join":
        cond = _require(spec, "condition", str, op)
        left = _require_child(spec, "left", op, catalog, buffer_pool, frames)
        right = _require_child(spec, "right", op, catalog, buffer_pool, frames)
        return NestedLoopJoin(cond, left, right, frames=frames)

    if op == "sort_merge_join":
        cond = _require(spec, "condition", str, op)
        left = _require_child(spec, "left", op, catalog, buffer_pool, frames)
        right = _require_child(spec, "right", op, catalog, buffer_pool, frames)
        return SortMergeJoin(cond, left, right, frames=frames)

    if op == "hash_join":
        cond = _require(spec, "condition", str, op)
        left = _require_child(spec, "left", op, catalog, buffer_pool, frames)
        right = _require_child(spec, "right", op, catalog, buffer_pool, frames)
        return HashJoin(cond, left, right, frames=frames)

    if op == "union_all":
        left = _require_child(spec, "left", op, catalog, buffer_pool, frames)
        right = _require_child(spec, "right", op, catalog, buffer_pool, frames)
        return UnionAll(left, right)

    if op == "union":
        left = _require_child(spec, "left", op, catalog, buffer_pool, frames)
        right = _require_child(spec, "right", op, catalog, buffer_pool, frames)
        return Union(left, right)

    if op == "intersect":
        left = _require_child(spec, "left", op, catalog, buffer_pool, frames)
        right = _require_child(spec, "right", op, catalog, buffer_pool, frames)
        return Intersect(left, right)

    if op == "except":
        left = _require_child(spec, "left", op, catalog, buffer_pool, frames)
        right = _require_child(spec, "right", op, catalog, buffer_pool, frames)
        return Except(left, right)

    raise EngineError("schema", f"Node '{op}': unknown operator")


def _require(spec, field, typ, node_op):
    if field not in spec:
        raise EngineError("schema", f"Node '{node_op}': missing required field '{field}'")
    v = spec[field]
    if not isinstance(v, typ):
        raise EngineError("schema",
            f"Node '{node_op}': field '{field}' must be {typ.__name__}")
    return v


def _require_choice(spec, field, choices, node_op):
    v = _require(spec, field, str, node_op)
    if v not in choices:
        raise EngineError("schema",
            f"Node '{node_op}': field '{field}' must be one of {choices}")
    return v


def _require_child(spec, field, node_op, catalog, buffer_pool, frames):
    if field not in spec:
        raise EngineError("schema", f"Node '{node_op}': missing required field '{field}'")
    return build_plan(spec[field], catalog, buffer_pool, frames)
