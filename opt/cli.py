# opt/cli.py - the four optimizer subcommands. M4 section 6.
#
#   opt analyze  --db DIR --table R [--buckets 20]
#   opt plan     --db DIR --logical q.json --frames 32 --json
#   opt explain  --db DIR --logical q.json --frames 32
#   opt dp-table --db DIR --logical q.json --frames 32

import argparse
import json
import sys

from storage.catalog import Catalog
from engine.errors import EngineError

from opt.catalog_ext import get_stats, default_stats, has_stats
from opt.stats import analyze, analyze_all
from opt.rewrite import rewrite
from opt.enumerate import enumerate_joins
from opt import selectivity as sel


def cmd_analyze(args):
    cat = Catalog(args.db)
    if args.table:
        stats = analyze(cat, args.table, buckets=args.buckets, frames=args.frames)
        out = {
            "table": args.table,
            "n_tuples": stats["n_tuples"],
            "n_pages": stats["n_pages"],
            "width": stats["width"],
            "columns": {
                c: {"ndv": v["ndv"], "min": v["min"], "max": v["max"],
                    "buckets": len(v["hist"]["buckets"])}
                for c, v in stats["columns"].items()
            },
        }
        print(json.dumps(out))
    else:
        results = analyze_all(cat, buckets=args.buckets, frames=args.frames)
        print(json.dumps({
            t: {"n_tuples": s["n_tuples"], "n_pages": s["n_pages"]}
            for t, s in results.items()
        }))


def _load_logical_plan(path):
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, list):
        if len(data) == 0:
            raise EngineError("schema", f"{path}: empty plan list")
        return data[0]
    return data


def _physical_plan(logical, cat, B, enabled_rules=None, use_stats=True):
    rewritten = rewrite(logical, enabled_rules=enabled_rules)
    return _build_physical(rewritten, cat, B, use_stats=use_stats)


def _build_physical(node, cat, B, use_stats=True):
    op = node.get("op")
    if op == "scan":
        return _physical_scan(node, cat)
    if op == "rename":
        inner = _build_physical(node["child"], cat, B, use_stats)
        out = {"op": "rename",
               "to": node["to"],
               "child": inner,
               "est_rows": inner.get("est_rows", 0),
               "est_cost": inner.get("est_cost", 0)}
        if "_stats" in inner:
            out["_stats"] = inner["_stats"]
        return out
    if op == "select":
        child = _build_physical(node["child"], cat, B, use_stats)
        rows = _estimate_select_rows(node["condition"], child, cat, use_stats)
        out = {"op": "filter",
               "condition": node["condition"],
               "child": child,
               "est_rows": rows,
               "est_cost": child.get("est_cost", 0)}
        if "_stats" in child:
            out["_stats"] = child["_stats"]
        return out
    if op == "project":
        child = _build_physical(node["child"], cat, B, use_stats)
        out = {"op": "project",
               "columns": node["columns"],
               "child": child,
               "est_rows": child.get("est_rows", 0),
               "est_cost": child.get("est_cost", 0)}
        if "_stats" in child:
            out["_stats"] = child["_stats"]
        return out
    if op == "sort":
        child = _build_physical(node["child"], cat, B, use_stats)
        out = {"op": "sort",
               "keys": node["keys"],
               "child": child,
               "est_rows": child.get("est_rows", 0),
               "est_cost": child.get("est_cost", 0)}
        if "_stats" in child:
            out["_stats"] = child["_stats"]
        return out
    if op == "limit":
        child = _build_physical(node["child"], cat, B, use_stats)
        rows = min(child.get("est_rows", 0), node["n"])
        out = {"op": "limit",
               "n": node["n"],
               "child": child,
               "est_rows": rows,
               "est_cost": child.get("est_cost", 0)}
        if "_stats" in child:
            out["_stats"] = child["_stats"]
        return out
    if op == "distinct":
        child = _build_physical(node["child"], cat, B, use_stats)
        out = {"op": "distinct",
               "mode": node.get("mode", "hash"),
               "child": child,
               "est_rows": child.get("est_rows", 0),
               "est_cost": child.get("est_cost", 0)}
        if "_stats" in child:
            out["_stats"] = child["_stats"]
        return out
    if op == "group_by":
        child = _build_physical(node["child"], cat, B, use_stats)
        out = {"op": "group_by",
               "mode": node.get("mode", "hash"),
               "keys": node["keys"],
               "aggregates": node["aggregates"],
               "child": child,
               "est_rows": child.get("est_rows", 0),
               "est_cost": child.get("est_cost", 0)}
        if "_stats" in child:
            out["_stats"] = child["_stats"]
        return out
    if op == "join":
        return _physical_join(node, cat, B, use_stats)
    if op == "cross":
        return _physical_cross(node, cat, B, use_stats)
    if op in ("union", "union_all", "intersect", "except"):
        left = _build_physical(node["left"], cat, B, use_stats)
        right = _build_physical(node["right"], cat, B, use_stats)
        rows = left.get("est_rows", 0) + right.get("est_rows", 0)
        out = {"op": op,
               "left": left,
               "right": right,
               "est_rows": rows,
               "est_cost": left.get("est_cost", 0) + right.get("est_cost", 0)}
        return out
    raise EngineError("schema", f"opt: unknown logical operator {op!r}")


def _physical_scan(node, cat):
    table = node["table"]
    if has_stats(cat, table):
        stats = get_stats(cat, table)
    else:
        stats = default_stats(cat, table)
    pages = stats["n_pages"]
    rows = stats["n_tuples"]
    return {"op": "seqscan",
            "table": table,
            "est_rows": rows,
            "est_cost": pages,
            "_stats": stats}


def _physical_join(node, cat, B, use_stats):
    left = _build_physical(node["left"], cat, B, use_stats)
    right = _build_physical(node["right"], cat, B, use_stats)
    cond = node["condition"]

    rows = _estimate_join_rows(cond, left, right, use_stats)

    M = max(1, left.get("est_cost", 1))
    N = max(1, right.get("est_cost", 1))
    is_equality = _condition_is_equality(cond)

    from opt.cost import nested_loop_join, sort_merge_join, hash_join
    candidates = [
        ("nested_loop_join", nested_loop_join(M, N, B)),
    ]
    if is_equality:
        candidates.append(("sort_merge_join", sort_merge_join(M, N, B)))
        candidates.append(("hash_join", hash_join(M, N, B)))
    algo, join_cost = min(candidates, key=lambda c: c[1])
    total = left.get("est_cost", 0) + right.get("est_cost", 0) + join_cost

    out = {"op": algo,
           "condition": cond,
           "left": left,
           "right": right,
           "est_rows": rows,
           "est_cost": total}
    # Carry column stats forward for the NEXT join to use.
    stats_l = left.get("_stats") or {}
    stats_r = right.get("_stats") or {}
    cols = dict(stats_l.get("columns", {}))
    for k, v in stats_r.get("columns", {}).items():
        if k in cols:
            merged = dict(cols[k])
            merged["ndv"] = max(cols[k].get("ndv", 1), v.get("ndv", 1))
            cols[k] = merged
        else:
            cols[k] = v
    out["_stats"] = {
        "n_tuples": rows,
        "n_pages": sel.out_pages(rows, stats_l.get("width", 50) + stats_r.get("width", 50)),
        "width": stats_l.get("width", 50) + stats_r.get("width", 50),
        "columns": cols,
    }
    return out


def _physical_cross(node, cat, B, use_stats):
    left = _build_physical(node["left"], cat, B, use_stats)
    right = _build_physical(node["right"], cat, B, use_stats)
    rows = max(1, left.get("est_rows", 1) * right.get("est_rows", 1))
    from opt.cost import nested_loop_join
    join_cost = nested_loop_join(max(1, left.get("est_cost", 1)),
                                 max(1, right.get("est_cost", 1)), B)
    total = left.get("est_cost", 0) + right.get("est_cost", 0) + join_cost
    out = {"op": "nested_loop_join",
           "condition": "1 = 1",
           "left": left,
           "right": right,
           "est_rows": rows,
           "est_cost": total}
    stats_l = left.get("_stats") or {}
    stats_r = right.get("_stats") or {}
    out["_stats"] = {
        "n_tuples": rows,
        "n_pages": 1,
        "width": stats_l.get("width", 50) + stats_r.get("width", 50),
        "columns": {**stats_l.get("columns", {}), **stats_r.get("columns", {})},
    }
    return out


def _estimate_select_rows(cond, child, cat, use_stats):
    rows = child.get("est_rows", 1)
    s = _condition_selectivity(cond, child, cat, use_stats)
    return sel.out_rows(rows, s)


def _condition_selectivity(cond, child, cat, use_stats):
    parts = _split_and(cond)
    s = 1.0
    for p in parts:
        s *= _conjunct_selectivity(p.strip(), child, cat, use_stats)
    return s


def _split_and(cond):
    parts = []
    depth = 0
    buf = []
    s = cond.strip()
    i = 0
    while i < len(s):
        c = s[i]
        if c == "(":
            depth += 1; buf.append(c); i += 1
        elif c == ")":
            depth -= 1; buf.append(c); i += 1
        elif depth == 0 and s.startswith(" and ", i):
            parts.append("".join(buf).strip()); buf = []; i += 5
        else:
            buf.append(c); i += 1
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts or [cond]


def _conjunct_selectivity(cond, child, cat, use_stats):
    import re
    m = re.match(
        r"^\s*([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\s*"
        r"(=|<>|<|<=|>|>=)\s*(.+)$", cond)
    if not m:
        return 0.1
    colq = m.group(1)
    op = m.group(2)
    table, col = colq.split(".", 1)
    rhs = m.group(3).strip()
    if rhs.startswith("'") and rhs.endswith("'"):
        value = rhs[1:-1]
    else:
        try:
            value = int(rhs)
        except ValueError:
            return 0.1
    stats = child.get("_stats") or {}
    if not stats.get("columns", {}).get(col):
        try:
            if has_stats(cat, table):
                stats = get_stats(cat, table)
            else:
                stats = default_stats(cat, table)
        except EngineError:
            return 0.1
    try:
        return sel.selectivity(stats, col, (op, value))
    except EngineError:
        return 0.1


def _estimate_join_rows(cond, left, right, use_stats):
    n = max(1, left.get("est_rows", 1) * right.get("est_rows", 1))
    import re
    m = re.match(
        r"^\s*([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
        r"([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\s*$", cond)
    if m:
        a, b = m.group(1), m.group(2)
        ndv_a = (left.get("_stats") or {}).get("columns", {}).get(a.split(".", 1)[1], {}).get("ndv")
        ndv_b = (right.get("_stats") or {}).get("columns", {}).get(b.split(".", 1)[1], {}).get("ndv")
        if ndv_a and ndv_b:
            return sel.out_rows(n, 1.0 / max(ndv_a, ndv_b))
    return sel.out_rows(n, sel.selectivity_default())


def _condition_is_equality(cond):
    import re
    return bool(re.match(r"^[^<>=]*=[^=<>]*$", cond))


def _explain_lines(node, depth=0):
    pad = "  " * depth
    op = node.get("op", "?")
    condition = ""
    if "condition" in node:
        condition = f" ({node['condition']})"
    est_rows = node.get("est_rows", 0)
    est_cost = node.get("est_cost", 0)
    line = f"{pad}{op}{condition}"
    line = f"{line:<44} est_rows={est_rows:<10} est_cost={est_cost}"
    out = [line]
    for k in ("child", "left", "right"):
        if k in node:
            out.extend(_explain_lines(node[k], depth + 1))
    return out


def cmd_plan(args):
    cat = Catalog(args.db)
    logical = _load_logical_plan(args.logical)
    physical = _physical_plan(logical, cat, args.frames)
    if args.json:
        print(json.dumps(physical, indent=2, default=str))
    else:
        print("\n".join(_explain_lines(physical)))


def cmd_explain(args):
    cat = Catalog(args.db)
    logical = _load_logical_plan(args.logical)
    physical = _physical_plan(logical, cat, args.frames)
    print("\n".join(_explain_lines(physical)))


def cmd_dp_table(args):
    cat = Catalog(args.db)
    logical = _load_logical_plan(args.logical)
    p = rewrite(logical, enabled_rules={1, 2, 3, 4, 5, 7})
    relations, predicates = _extract_join_graph(p)
    if len(relations) < 2:
        print(f"dp-table: only {len(relations)} base relation(s); no enumeration needed")
        return
    stats_by_table = {}
    for t in relations:
        if has_stats(cat, t):
            stats_by_table[t] = get_stats(cat, t)
        else:
            stats_by_table[t] = default_stats(cat, t)
    result = enumerate_joins(relations, predicates, stats_by_table, args.frames)
    print(f"{'subset':<24} {'best plan':<44} {'cost':>8}")
    print("-" * 80)
    for row in result["dp_table"]:
        print(f"{row['subset']:<24} {row['plan']:<44} {row['cost']:>8}")
    print()
    print(f"answer cost: {result['cost']}")


def _extract_join_graph(plan):
    relations = []
    predicates = []

    def walk(n):
        if not isinstance(n, dict):
            return
        op = n.get("op")
        if op == "scan" and "table" in n:
            if n["table"] not in relations:
                relations.append(n["table"])
        elif op == "join":
            import re
            m = re.match(
                r"^\s*([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\s*"
                r"(=|<>|<|<=|>|>=)\s*"
                r"([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\s*$",
                n["condition"])
            if m:
                a, op2, b = m.group(1), m.group(2), m.group(3)
                ta, ca = a.split(".", 1)
                tb, cb = b.split(".", 1)
                predicates.append({
                    "left_table": ta, "left_col": ca,
                    "right_table": tb, "right_col": cb, "op": op2,
                })
            walk(n["left"])
            walk(n["right"])
            return
        for k in ("child", "left", "right"):
            if k in n:
                walk(n[k])

    walk(plan)
    return relations, predicates


def main(argv=None):
    ap = argparse.ArgumentParser(prog="opt")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("analyze")
    p.add_argument("--db", required=True)
    p.add_argument("--table", default=None)
    p.add_argument("--buckets", type=int, default=20)
    p.add_argument("--frames", type=int, default=32)
    p.set_defaults(fn=cmd_analyze)

    p = sub.add_parser("plan")
    p.add_argument("--db", required=True)
    p.add_argument("--logical", required=True)
    p.add_argument("--frames", type=int, default=32)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_plan)

    p = sub.add_parser("explain")
    p.add_argument("--db", required=True)
    p.add_argument("--logical", required=True)
    p.add_argument("--frames", type=int, default=32)
    p.set_defaults(fn=cmd_explain)

    p = sub.add_parser("dp-table")
    p.add_argument("--db", required=True)
    p.add_argument("--logical", required=True)
    p.add_argument("--frames", type=int, default=32)
    p.set_defaults(fn=cmd_dp_table)

    args = ap.parse_args(argv)
    try:
        args.fn(args)
    except EngineError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"error: internal: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
