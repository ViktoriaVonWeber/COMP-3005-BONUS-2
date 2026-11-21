# workload/run_workload.py - compile each query and print its logical plan.
# Used by the M4 measurements.

import argparse
import json
import os
import sys

from storage.catalog import Catalog
from sql.parser import parse_sql
from sql.resolver import resolve
from sql.translator import translate
from opt.rewrite import rewrite
from opt import cli as opt_cli


def _strip_comments(sql):
    """Remove -- comments from a SQL string, preserving newlines."""
    out = []
    for line in sql.split("\n"):
        idx = line.find("--")
        if idx >= 0:
            line = line[:idx]
        out.append(line)
    return "\n".join(out)


def load_queries(path):
    """Return list of (label, sql) for each statement in the file."""
    with open(path) as f:
        text = f.read()
    stmts = [s.strip() for s in text.split(";") if s.strip()]
    queries = []
    for s in stmts:
        # Skip chunks that are entirely comments
        if all(line.strip().startswith("--") or not line.strip()
               for line in s.split("\n")):
            continue
        label = "?"
        for line in s.split("\n"):
            line = line.strip()
            if line.startswith("-- Q"):
                label = line[3:].split(":")[0].strip()
                break
        queries.append((label, _strip_comments(s)))
    return queries


def main(argv=None):
    ap = argparse.ArgumentParser(prog="workload.run_workload")
    ap.add_argument("--db", required=True)
    ap.add_argument("--queries", default="workload/queries.sql")
    ap.add_argument("--frames", type=int, default=32)
    args = ap.parse_args(argv)

    cat = Catalog(args.db)
    queries = load_queries(args.queries)
    print(f"{'q':<6} {'est_rows':>10} {'est_cost':>10}  plan summary")
    print("-" * 80)
    for label, sql in queries:
        try:
            stmts = parse_sql(sql)
            if not stmts:
                continue
            plan = translate_after_resolve(stmts[0], cat)
            physical = opt_cli._physical_plan(plan, cat, args.frames)
            rows = physical.get("est_rows", 0)
            cost = physical.get("est_cost", 0)
            summary = physical.get("op", "?")
            print(f"{label:<6} {rows:>10} {cost:>10}  {summary}")
        except Exception as e:
            print(f"{label:<6} {'ERROR':>10} {'-':>10}  {e}")


def translate_after_resolve(stmt, cat):
    resolve(stmt, cat)
    return translate(stmt)


if __name__ == "__main__":
    main()