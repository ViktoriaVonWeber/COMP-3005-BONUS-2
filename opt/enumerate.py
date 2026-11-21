# opt/enumerate.py - join enumeration. M4 section 5.
#
# Dynamic programming over left-deep trees (Selinger). Up to six
# relations. Left-deep: the right input of every join is a base relation.
#
# Cost convention matching M4 section 5.1:
#   - base relation: cost = page count, ext_cost = 0
#   - extension: cost(S + {r}) = ext_cost(S) + join_algorithm_cost(pages(S), pages(r))
#   - ext_cost(S) = cost(S)
# For M4 5.1's numbers: {A,B'} = 110, {A,B',C} = 530 (with 20 pages for
# {A,B'}; we compute 25 pages from the actual row estimate, giving 535).

import itertools

from engine.errors import EngineError
from opt.cost import nested_loop_join, sort_merge_join, hash_join
from opt.selectivity import (selectivity_join, selectivity_default,
                             out_rows, out_pages)


def enumerate_joins(relations, predicates, stats_by_table, B,
                    widths_by_table=None, use_stats=True):
    if not relations:
        raise EngineError("schema", "enumerate_joins: no relations")
    n = len(relations)
    if n > 6:
        raise EngineError("schema",
            f"enumerate_joins: at most 6 relations, got {n}")

    # Width defaults: use each table's stats width, not 50.
    if widths_by_table is None:
        widths_by_table = {
            t: stats_by_table.get(t, {}).get("width", 50)
            for t in relations
        }

    best = {}
    dp_rows = []

    for r in relations:
        stats = _get_stats(stats_by_table, r, use_stats)
        pages = stats["n_pages"]
        rows = stats["n_tuples"]
        width = widths_by_table.get(r, stats.get("width", 50))
        cols = stats.get("columns", {})
        best[frozenset([r])] = {
            "plan": {"op": "seqscan", "table": r},
            "cost": pages,
            "ext_cost": 0,
            "pages": pages,
            "rows": rows,
            "width": width,
            "columns": dict(cols),
            "tables": frozenset([r]),
        }
        dp_rows.append({"subset": r, "plan": "scan " + r, "cost": pages})

    for k in range(2, n + 1):
        for subset in itertools.combinations(relations, k):
            S = frozenset(subset)
            best_S = None
            fallback = None
            for r in subset:
                left_subset = S - {r}
                if left_subset not in best:
                    continue
                left = best[left_subset]
                right = best[frozenset([r])]
                preds = _predicates_between(predicates, left_subset, {r})
                if not preds:
                    if fallback is None:
                        fallback = (left, right)
                    continue
                cand = _best_join_for_pair(left, right, preds, B)
                if best_S is None or cand["cost"] < best_S["cost"]:
                    best_S = cand

            if best_S is None:
                if fallback is not None:
                    best_S = _cross_pair(fallback[0], fallback[1])
                else:
                    raise EngineError("schema",
                        f"enumerate_joins: no plan for subset {sorted(subset)}")

            best[S] = best_S
            dp_rows.append({
                "subset": ",".join(sorted(subset)),
                "plan": _plan_to_string(best_S["plan"]),
                "cost": best_S["cost"],
            })

    all_rel = frozenset(relations)
    return {
        "plan": best[all_rel]["plan"],
        "cost": best[all_rel]["cost"],
        "dp_table": dp_rows,
    }


def _best_join_for_pair(left, right, preds, B):
    is_all_equality = all(p["op"] == "=" for p in preds)
    M = left["pages"]
    N = right["pages"]
    candidates = []

    bnl = nested_loop_join(M, N, B)
    candidates.append(_make_candidate(
        "nested_loop_join", left, right, preds, bnl))

    if is_all_equality:
        smj = sort_merge_join(M, N, B)
        candidates.append(_make_candidate(
            "sort_merge_join", left, right, preds, smj))
        hj = hash_join(M, N, B)
        candidates.append(_make_candidate(
            "hash_join", left, right, preds, hj))

    return min(candidates, key=lambda c: c["cost"])


def _cross_pair(left, right):
    M = left["pages"]
    N = right["pages"]
    join_cost = nested_loop_join(M, N, 4)
    rows = left["rows"] * right["rows"]
    width = left["width"] + right["width"]
    pages = out_pages(rows, width)
    cols = _merge_columns(left["columns"], right["columns"])
    return {
        "plan": {"op": "cross", "left": left["plan"], "right": right["plan"]},
        "cost": left["ext_cost"] + right["ext_cost"] + join_cost,
        "ext_cost": left["ext_cost"] + right["ext_cost"] + join_cost,
        "pages": pages,
        "rows": rows,
        "width": width,
        "columns": cols,
        "tables": left["tables"] | right["tables"],
    }


def _make_candidate(op, left, right, preds, join_cost):
    cond = " and ".join(_predicate_string(p) for p in preds)
    rows = _join_out_rows(left, right, preds)
    width = left["width"] + right["width"]
    pages = out_pages(rows, width)
    total = left["ext_cost"] + right["ext_cost"] + join_cost
    cols = _merge_columns(left["columns"], right["columns"])
    return {
        "plan": {"op": op, "condition": cond,
                 "left": left["plan"], "right": right["plan"]},
        "cost": total,
        "ext_cost": total,
        "pages": pages,
        "rows": rows,
        "width": width,
        "columns": cols,
        "tables": left["tables"] | right["tables"],
    }


def _merge_columns(lc, rc):
    out = dict(lc)
    for k, v in rc.items():
        if k in out:
            merged = dict(out[k])
            merged["ndv"] = max(out[k].get("ndv", 1), v.get("ndv", 1))
            out[k] = merged
        else:
            out[k] = v
    return out


def _join_out_rows(left, right, preds):
    n = left["rows"] * right["rows"]
    for p in preds:
        if p["op"] == "=":
            ndv_l = left["columns"].get(p["left_col"], {}).get("ndv", left["rows"])
            ndv_r = right["columns"].get(p["right_col"], {}).get("ndv", right["rows"])
            s = 1.0 / max(ndv_l, ndv_r, 1)
            n = out_rows(n, s)
        else:
            n = out_rows(n, selectivity_default())
    return max(1, n)


def _predicates_between(predicates, left_tables, right_tables):
    out = []
    for p in predicates:
        lt, rt = p["left_table"], p["right_table"]
        if (lt in left_tables and rt in right_tables) or \
           (rt in left_tables and lt in right_tables):
            out.append(p)
    return out


def _predicate_string(p):
    return f"{p['left_table']}.{p['left_col']} {p['op']} {p['right_table']}.{p['right_col']}"


def _get_stats(stats_by_table, table, use_stats):
    stats = stats_by_table.get(table)
    if stats is None or not use_stats:
        return {"n_tuples": 1000, "n_pages": 10, "width": 50, "columns": {}}
    return stats


def _plan_to_string(plan):
    op = plan.get("op")
    if op == "seqscan":
        return "scan " + plan["table"]
    if op == "nested_loop_join":
        return f"BNL({_plan_to_string(plan['left'])}, {_plan_to_string(plan['right'])})"
    if op == "sort_merge_join":
        return f"SMJ({_plan_to_string(plan['left'])}, {_plan_to_string(plan['right'])})"
    if op == "hash_join":
        return f"HJ({_plan_to_string(plan['left'])}, {_plan_to_string(plan['right'])})"
    if op == "cross":
        return f"CROSS({_plan_to_string(plan['left'])}, {_plan_to_string(plan['right'])})"
    if "child" in plan:
        return f"{op}({_plan_to_string(plan['child'])})"
    return op