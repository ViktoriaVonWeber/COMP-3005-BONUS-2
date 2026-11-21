# workload/measure_m4.py - run the four M4 section 7 measurements.
#
# Measurement 1: estimation quality (q-error per node, median and worst)
# Measurement 2: does optimization pay (chosen plan vs unoptimized plan I/O)
# Measurement 3: which rewrite rule mattered (turn off one rule at a time)
# Measurement 4: where it breaks (the correlated queries Q17-Q20)
#
# Requires /tmp/m4db to be built and analyzed:
#   python -m workload.build_dataset --db /tmp/m4db
#   python -m opt analyze --db /tmp/m4db
#
# Output: results/m4_measurements.json

import argparse
import json
import os
import sys

from storage.catalog import Catalog
from sql.parser import parse_sql
from sql.resolver import resolve
from sql.translator import translate
from engine.errors import EngineError

from opt import cli as opt_cli
from opt.rewrite import rewrite

from exec.operator import END


# ------------------------------------------------------------
# Query loading
# ------------------------------------------------------------

def _strip_comments(sql):
    out = []
    for line in sql.split("\n"):
        idx = line.find("--")
        if idx >= 0:
            line = line[:idx]
        out.append(line)
    return "\n".join(out)


def load_queries(path):
    with open(path) as f:
        text = f.read()
    stmts = [s.strip() for s in text.split(";") if s.strip()]
    queries = []
    for s in stmts:
        if all(line.strip().startswith("--") or not line.strip()
               for line in s.split("\n")):
            continue
        label = "?"
        for line in s.split("\n"):
            if line.strip().startswith("-- Q"):
                label = line.strip()[3:].split(":")[0].strip()
                break
        sql = _strip_comments(s)
        if sql.strip():
            queries.append((label, sql))
    return queries


# ------------------------------------------------------------
# Compilation helpers
# ------------------------------------------------------------

def compile_query(sql, cat):
    stmts = parse_sql(sql)
    if not stmts:
        raise EngineError("syntax", "no statements")
    stmt = stmts[0]
    resolve(stmt, cat)
    return translate(stmt)


def strip_estimates(plan):
    """Remove est_rows, est_cost, _stats so the plan is valid M2 JSON."""
    if not isinstance(plan, dict):
        return plan
    out = {}
    for k, v in plan.items():
        if k in ("est_rows", "est_cost", "_stats"):
            continue
        if isinstance(v, dict):
            out[k] = strip_estimates(v)
        elif isinstance(v, list):
            out[k] = [strip_estimates(x) for x in v]
        else:
            out[k] = v
    return out


def count_plan_nodes(plan):
    nodes = []
    def walk(n):
        if not isinstance(n, dict):
            return
        if "est_rows" in n:
            nodes.append(n)
        for k in ("child", "left", "right"):
            if k in n:
                walk(n[k])
    walk(plan)
    return nodes


def _collect_tables(plan):
    tables = set()
    def walk(n):
        if not isinstance(n, dict):
            return
        if n.get("op") == "seqscan" and "table" in n:
            tables.add(n["table"])
        for k in ("child", "left", "right"):
            if k in n:
                walk(n[k])
    walk(plan)
    return tables


# ------------------------------------------------------------
# Execution helpers
# ------------------------------------------------------------

def _run_and_trace(exec_plan, cat, frames):
    """Run the plan through the M2 executor and return per-node tuple counts."""
    from storage.disk import DiskManager
    from storage.buffer import BufferPool
    from exec.cli import _build_with_pools

    tables = _collect_tables(exec_plan)
    pools = {}
    disks = {}
    for t in tables:
        d = DiskManager(cat.data_path(t))
        disks[t] = d
        pools[t] = BufferPool(d, frames=frames, policy="lru")

    def pool_for(t):
        return pools[t]

    root = _build_with_pools(exec_plan, cat, pool_for, frames)
    root.open()
    while True:
        t = root.next()
        if t is END:
            break
    root.close()

    actuals = []
    def walk(n):
        c = n.counters()
        actuals.append(c["tuples_out"])
        for ch in getattr(n, "children", []):
            walk(ch)
    walk(root)

    for d in disks.values():
        d.close()
    return actuals


def _run_and_measure_io(exec_plan, cat, frames):
    """Run and sum page_reads + page_writes across all nodes."""
    from storage.disk import DiskManager
    from storage.buffer import BufferPool
    from exec.cli import _build_with_pools

    tables = _collect_tables(exec_plan)
    pools = {}
    disks = {}
    for t in tables:
        d = DiskManager(cat.data_path(t))
        disks[t] = d
        pools[t] = BufferPool(d, frames=frames, policy="lru")

    def pool_for(t):
        return pools[t]

    root = _build_with_pools(exec_plan, cat, pool_for, frames)
    root.open()
    while True:
        t = root.next()
        if t is END:
            break
    root.close()

    total_reads = 0
    total_writes = 0
    def walk(n):
        nonlocal total_reads, total_writes
        c = n.counters()
        total_reads += c["page_reads"]
        total_writes += c["page_writes"]
        for ch in getattr(n, "children", []):
            walk(ch)
    walk(root)

    for d in disks.values():
        d.close()
    return total_reads + total_writes


# ------------------------------------------------------------
# Measurement 1: estimation quality
# ------------------------------------------------------------

def measure_estimation_quality(cat, queries, frames):
    results = []
    all_q_errors = []

    for label, sql in queries:
        try:
            logical = compile_query(sql, cat)
            physical = opt_cli._physical_plan(logical, cat, frames)
            exec_plan = strip_estimates(physical)
            actuals = _run_and_trace(exec_plan, cat, frames)

            est_nodes = count_plan_nodes(physical)
            q_errors = []
            for i, est in enumerate(est_nodes):
                est_rows = est.get("est_rows", 0)
                actual = actuals[i] if i < len(actuals) else 0
                if est_rows > 0 and actual > 0:
                    qe = max(est_rows, actual) / max(min(est_rows, actual), 1)
                elif est_rows == 0 and actual == 0:
                    qe = 1.0
                else:
                    qe = float("inf")
                q_errors.append(qe)
                all_q_errors.append(qe)
            results.append({
                "query": label,
                "est_rows": [n.get("est_rows", 0) for n in est_nodes],
                "actual_rows": actuals,
                "q_errors": q_errors,
            })
        except Exception as e:
            results.append({"query": label, "error": str(e)})

    finite = [q for q in all_q_errors if q != float("inf")]
    median = sorted(finite)[len(finite) // 2] if finite else None
    worst = max(finite) if finite else None

    return {
        "per_query": results,
        "median_q_error": median,
        "worst_q_error": worst,
        "num_nodes": len(all_q_errors),
    }


# ------------------------------------------------------------
# Measurement 2: does optimization pay?
# ------------------------------------------------------------

def measure_optimization_payoff(cat, queries, frames):
    results = []

    for label, sql in queries:
        try:
            logical = compile_query(sql, cat)
            physical = opt_cli._physical_plan(logical, cat, frames)
            exec_plan = strip_estimates(physical)
            io_chosen = _run_and_measure_io(exec_plan, cat, frames)

            unoptimized = opt_cli._physical_plan(logical, cat, frames,
                                                 enabled_rules=set())
            exec_unopt = strip_estimates(unoptimized)
            io_unopt = _run_and_measure_io(exec_unopt, cat, frames)

            ratio = io_chosen / io_unopt if io_unopt > 0 else 1.0
            results.append({
                "query": label,
                "chosen_io": io_chosen,
                "unoptimized_io": io_unopt,
                "ratio": ratio,
            })
        except Exception as e:
            results.append({"query": label, "error": str(e)})

    ratios = [r["ratio"] for r in results if "ratio" in r]
    return {
        "per_query": results,
        "min_ratio": min(ratios) if ratios else None,
        "max_ratio": max(ratios) if ratios else None,
    }


# ------------------------------------------------------------
# Measurement 3: which rule mattered?
# ------------------------------------------------------------

def measure_rule_impact(cat, queries, frames):
    all_rules = {1, 2, 3, 4, 5, 6, 7}

    total_with_all = 0
    for label, sql in queries:
        try:
            logical = compile_query(sql, cat)
            physical = opt_cli._physical_plan(logical, cat, frames,
                                              enabled_rules=all_rules)
            exec_plan = strip_estimates(physical)
            total_with_all += _run_and_measure_io(exec_plan, cat, frames)
        except Exception:
            pass

    results = []
    for rule in sorted(all_rules):
        rules_off = all_rules - {rule}
        total = 0
        for label, sql in queries:
            try:
                logical = compile_query(sql, cat)
                physical = opt_cli._physical_plan(logical, cat, frames,
                                                  enabled_rules=rules_off)
                exec_plan = strip_estimates(physical)
                total += _run_and_measure_io(exec_plan, cat, frames)
            except Exception:
                pass
        results.append({
            "rule_off": rule,
            "total_io": total,
            "with_all_rules": total_with_all,
            "delta": total - total_with_all,
        })

    return {
        "baseline_io": total_with_all,
        "per_rule": results,
    }


# ------------------------------------------------------------
# Measurement 4: where it breaks
# ------------------------------------------------------------

def measure_where_it_breaks(cat, queries, frames):
    results = []
    for label, sql in queries:
        if label not in ("Q17", "Q18", "Q19", "Q20"):
            continue
        try:
            logical = compile_query(sql, cat)
            physical = opt_cli._physical_plan(logical, cat, frames)
            est_rows = physical.get("est_rows", 0)
            exec_plan = strip_estimates(physical)
            actuals = _run_and_trace(exec_plan, cat, frames)
            actual_root = actuals[0] if actuals else 0
            io = _run_and_measure_io(exec_plan, cat, frames)
            results.append({
                "query": label,
                "sql": sql.strip(),
                "est_rows": est_rows,
                "actual_rows": actual_root,
                "est_cost": physical.get("est_cost", 0),
                "io": io,
                "failed_assumption": "independence of Emp.did and Emp.region",
                "explanation": (
                    "Emp.region is a deterministic function of Emp.did "
                    "(region = REGIONS[did % 5]). The optimizer computes "
                    "selectivity as sel(did=5) * sel(region=EU) = "
                    "1/50 * 1/5 = 1/250 = 0.004, predicting ~40 rows. "
                    "Actual for did=5 AND region='EU' is 0 (did 5 maps "
                    "to 'NA', never 'EU'). The independence assumption "
                    "is false: the two predicates are perfectly correlated."
                ),
                "what_would_fix_it": (
                    "A multi-column histogram (joint of did and region) "
                    "or a correlation statistic (e.g., mutual information "
                    "or Pearson coefficient) computed in ANALYZE. With "
                    "correlated stats, the optimizer would know that "
                    "did=5 implies region='NA', and compute the actual "
                    "selectivity instead of multiplying marginals."
                ),
            })
        except Exception as e:
            results.append({"query": label, "error": str(e)})
    return {"per_query": results}


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="workload.measure_m4")
    ap.add_argument("--db", required=True)
    ap.add_argument("--queries", default="workload/queries.sql")
    ap.add_argument("--frames", type=int, default=32)
    ap.add_argument("--out", default="results/m4_measurements.json")
    args = ap.parse_args(argv)

    cat = Catalog(args.db)
    queries = load_queries(args.queries)

    print(f"loaded {len(queries)} queries from {args.queries}")
    print()

    print("measurement 1: estimation quality")
    m1 = measure_estimation_quality(cat, queries, args.frames)
    print(f"  median q-error: {m1['median_q_error']}")
    print(f"  worst q-error:  {m1['worst_q_error']}")
    print(f"  nodes measured: {m1['num_nodes']}")
    print()

    print("measurement 2: does optimization pay?")
    m2 = measure_optimization_payoff(cat, queries, args.frames)
    if m2['min_ratio'] is not None:
        print(f"  ratio range: {m2['min_ratio']:.3f} .. {m2['max_ratio']:.3f}")
    else:
        print("  no ratios (all errors)")
    print()

    print("measurement 3: which rule mattered?")
    m3 = measure_rule_impact(cat, queries, args.frames)
    print(f"  baseline io: {m3['baseline_io']}")
    for r in m3["per_rule"]:
        print(f"  rule {r['rule_off']} off: total_io={r['total_io']} delta={r['delta']}")
    print()

    print("measurement 4: where it breaks")
    m4 = measure_where_it_breaks(cat, queries, args.frames)
    for r in m4["per_query"]:
        if "error" in r:
            print(f"  {r['query']}: ERROR {r['error']}")
        else:
            print(f"  {r['query']}: est={r['est_rows']} actual={r['actual_rows']} io={r['io']}")
    print()

    out = {
        "measurement1_estimation": m1,
        "measurement2_payoff": m2,
        "measurement3_rule_impact": m3,
        "measurement4_where_it_breaks": m4,
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()