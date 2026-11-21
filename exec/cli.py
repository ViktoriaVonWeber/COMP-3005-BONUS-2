# exec/cli.py - exec run and exec trace. M2 section 4.
import argparse
import json
import sys
from storage.catalog import Catalog
from storage.disk import DiskManager
from storage.buffer import BufferPool
from exec.operator import END, collect_counters
from engine.errors import EngineError


def _collect_tables(plan):
    tables = set()
    def walk(node):
        if not isinstance(node, dict):
            return
        if node.get("op") == "seqscan" and "table" in node:
            tables.add(node["table"])
        for k in ("child", "left", "right"):
            if k in node:
                walk(node[k])
    walk(plan)
    return tables


def _build_with_pools(plan, cat, pool_for_table, frames):
    from exec.scan import SeqScan, Filter
    from exec.pipeline import Project, Limit, UnionAll
    from exec.sort import Sort
    from exec.grouping import Distinct, GroupBy
    from exec.joins import NestedLoopJoin, SortMergeJoin, HashJoin
    from exec.setops import Union, Intersect, Except

    op = plan.get("op")
    if not isinstance(op, str):
        raise EngineError("schema", "plan node missing 'op' field")

    def child(field):
        if field not in plan:
            raise EngineError("schema", f"Node '{op}': missing required field '{field}'")
        return _build_with_pools(plan[field], cat, pool_for_table, frames)

    if op == "seqscan":
        table = plan.get("table")
        if not isinstance(table, str):
            raise EngineError("schema", "Node 'seqscan': missing or bad 'table'")
        return SeqScan(table, cat, pool_for_table(table))
    if op == "filter":
        return Filter(_req_str(plan, "condition", op), child("child"))
    if op == "project":
        cols = plan.get("columns")
        if not isinstance(cols, list) or not all(isinstance(c, str) for c in cols):
            raise EngineError("schema", f"Node '{op}': field 'columns' must be list of strings")
        return Project(cols, child("child"))
    if op == "limit":
        n = plan.get("n")
        if not isinstance(n, int) or n < 0:
            raise EngineError("schema", f"Node '{op}': field 'n' must be a non-negative int")
        return Limit(n, child("child"))
    if op == "distinct":
        return Distinct(_req_choice(plan, "mode", ["sort", "hash"], op), child("child"))
    if op == "sort":
        keys = plan.get("keys")
        if not isinstance(keys, list) or not all(
            isinstance(k, list) and len(k) == 2 and isinstance(k[0], str) and k[1] in ("asc", "desc")
            for k in keys
        ):
            raise EngineError("schema",
                f"Node '{op}': field 'keys' must be list of [col, asc|desc]")
        return Sort([(k[0], k[1]) for k in keys], child("child"), frames=frames)
    if op == "group_by":
        mode = _req_choice(plan, "mode", ["sort", "hash"], op)
        keys = plan.get("keys")
        if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
            raise EngineError("schema", f"Node '{op}': field 'keys' must be list of strings")
        aggs = plan.get("aggregates")
        if not isinstance(aggs, list) or not all(
            isinstance(a, list) and len(a) == 2 and isinstance(a[0], str) and isinstance(a[1], str)
            for a in aggs
        ):
            raise EngineError("schema",
                f"Node '{op}': field 'aggregates' must be list of [kind, col]")
        return GroupBy(mode, keys, [tuple(a) for a in aggs], child("child"))
    if op == "nested_loop_join":
        return NestedLoopJoin(_req_str(plan, "condition", op),
                              child("left"), child("right"), frames=frames)
    if op == "sort_merge_join":
        return SortMergeJoin(_req_str(plan, "condition", op),
                             child("left"), child("right"), frames=frames)
    if op == "hash_join":
        return HashJoin(_req_str(plan, "condition", op),
                        child("left"), child("right"), frames=frames)
    if op == "union_all":
        return UnionAll(child("left"), child("right"))
    if op == "union":
        return Union(child("left"), child("right"))
    if op == "intersect":
        return Intersect(child("left"), child("right"))
    if op == "except":
        return Except(child("left"), child("right"))
    raise EngineError("schema", f"Node '{op}': unknown operator")


def _req_str(spec, field, op):
    if field not in spec:
        raise EngineError("schema", f"Node '{op}': missing required field '{field}'")
    v = spec[field]
    if not isinstance(v, str):
        raise EngineError("schema", f"Node '{op}': field '{field}' must be a string")
    return v


def _req_choice(spec, field, choices, op):
    v = _req_str(spec, field, op)
    if v not in choices:
        raise EngineError("schema",
            f"Node '{op}': field '{field}' must be one of {choices}")
    return v


def _load_plan(path):
    with open(path) as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            raise EngineError("syntax", f"malformed plan JSON: {e}")


def _open_pools(cat, plan, frames):
    tables = _collect_tables(plan)
    pools = {}
    disks = {}
    for t in tables:
        d = DiskManager(cat.data_path(t))
        disks[t] = d
        pools[t] = BufferPool(d, frames=frames, policy="lru")

    def pool_for(table):
        if table not in pools:
            raise EngineError("name", f"unknown table {table}")
        return pools[table]

    return pools, disks, pool_for


def cmd_run(args):
    plan = _load_plan(args.plan)
    cat = Catalog(args.db)
    pools, disks, pool_for = _open_pools(cat, plan, args.frames)
    root = _build_with_pools(plan, cat, pool_for, args.frames)

    root.open()
    rows = []
    while True:
        t = root.next()
        if t is END:
            break
        rows.append(list(t))
    schema = [list(c) for c in root.schema()]
    root.close()

    counters = collect_counters(root)
    totals = {"tuples_out": 0, "comparisons": 0, "page_reads": 0, "page_writes": 0}
    for c in counters:
        for k in totals:
            totals[k] += c[k]

    print(json.dumps({"schema": schema, "rows": rows, "totals": totals}))
    for d in disks.values():
        d.close()


def cmd_trace(args):
    plan = _load_plan(args.plan)
    cat = Catalog(args.db)
    pools, disks, pool_for = _open_pools(cat, plan, args.frames)
    root = _build_with_pools(plan, cat, pool_for, args.frames)

    root.open()
    while True:
        t = root.next()
        if t is END:
            break
    root.close()

    def walk(node, depth):
        c = node.counters()
        print("  " * depth + f"{c['name']:<18} tuples_out={c['tuples_out']:<6} "
              f"comparisons={c['comparisons']:<6} "
              f"reads={c['page_reads']:<6} writes={c['page_writes']}")
        for ch in getattr(node, "children", []):
            walk(ch, depth + 1)

    walk(root, 0)
    for d in disks.values():
        d.close()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="exec")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run")
    p.add_argument("--db", required=True)
    p.add_argument("--plan", required=True)
    p.add_argument("--frames", type=int, required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("trace")
    p.add_argument("--db", required=True)
    p.add_argument("--plan", required=True)
    p.add_argument("--frames", type=int, required=True)
    p.set_defaults(fn=cmd_trace)

    args = ap.parse_args(argv)
    try:
        args.fn(args)
    except EngineError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"error: internal: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
