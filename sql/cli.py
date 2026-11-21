# sql/cli.py - the three SQL subcommands. M3 section 8.
#
# sql parse   --query "SELECT ..." --explain
# sql compile --db DIR --file q.sql --json
# sql check   --db DIR --file q.sql
#
# parse:   lex + parse, print an indented AST tree (no catalog needed).
# compile: parse + resolve + translate, print logical plan JSON.
# check:   same as compile but only prints errors, exits 0 or 1.

import argparse
import json
import sys

from sql.lexer import SqlLexer
from sql.parser import SqlParser, parse_sql
from sql.resolver import resolve
from sql.translator import translate
from storage.catalog import Catalog
from engine.errors import EngineError
from sql import ast


# ------------------------------------------------------------
# AST pretty-printer for `parse --explain`
# ------------------------------------------------------------

def _ast_to_lines(node, indent=0):
    pad = "  " * indent
    if isinstance(node, ast.Query):
        out = [f"{pad}Query(setops={node.setops})"]
        for b in node.blocks:
            out.extend(_ast_to_lines(b, indent + 1))
        return out
    if isinstance(node, ast.SelectBlock):
        out = [f"{pad}SelectBlock(distinct={node.distinct})"]
        out.append(f"{pad}  select_list:")
        for item in node.select_list:
            out.extend(_ast_to_lines(item, indent + 2))
        out.append(f"{pad}  from:")
        for ref in node.from_list:
            out.extend(_ast_to_lines(ref, indent + 2))
        if node.where is not None:
            out.append(f"{pad}  where:")
            out.extend(_ast_to_lines(node.where, indent + 2))
        if node.group_by:
            out.append(f"{pad}  group_by:")
            for c in node.group_by:
                out.extend(_ast_to_lines(c, indent + 2))
        if node.having is not None:
            out.append(f"{pad}  having:")
            out.extend(_ast_to_lines(node.having, indent + 2))
        if node.order_by:
            out.append(f"{pad}  order_by:")
            for col, d in node.order_by:
                out.append(f"{pad}    {d}")
                out.extend(_ast_to_lines(col, indent + 3))
        if node.limit is not None:
            out.append(f"{pad}  limit {node.limit}")
        return out
    if isinstance(node, ast.Star):
        return [f"{pad}*"]
    if isinstance(node, ast.SelectItem):
        out = [f"{pad}SelectItem(alias={node.alias})"]
        out.extend(_ast_to_lines(node.expr, indent + 1))
        return out
    if isinstance(node, ast.Aggregate):
        return [f"{pad}Aggregate({node.kind}, {_short(node.arg)})"]
    if isinstance(node, ast.AggRef):
        return [f"{pad}AggRef({node.kind}, {_short(node.arg)})"]
    if isinstance(node, ast.ColumnRef):
        q = f"{node.qualifier}." if node.qualifier else ""
        return [f"{pad}ColumnRef({q}{node.name})"]
    if isinstance(node, ast.Literal):
        return [f"{pad}Literal({node.value!r})"]
    if isinstance(node, ast.TableRef):
        return [f"{pad}TableRef({node.name} AS {node.alias})"]
    if isinstance(node, ast.SubqueryRef):
        out = [f"{pad}SubqueryRef(AS {node.alias})"]
        out.extend(_ast_to_lines(node.query, indent + 1))
        return out
    if isinstance(node, ast.Join):
        out = [f"{pad}Join"]
        out.append(f"{pad}  left:")
        out.extend(_ast_to_lines(node.left, indent + 2))
        out.append(f"{pad}  right:")
        out.extend(_ast_to_lines(node.right, indent + 2))
        out.append(f"{pad}  on:")
        out.extend(_ast_to_lines(node.condition, indent + 2))
        return out
    if isinstance(node, ast.CondCmp):
        out = [f"{pad}CondCmp({node.op})"]
        out.extend(_ast_to_lines(node.left, indent + 1))
        out.extend(_ast_to_lines(node.right, indent + 1))
        return out
    if isinstance(node, ast.CondAnd):
        out = [f"{pad}CondAnd"]
        out.extend(_ast_to_lines(node.left, indent + 1))
        out.extend(_ast_to_lines(node.right, indent + 1))
        return out
    if isinstance(node, ast.CondOr):
        out = [f"{pad}CondOr"]
        out.extend(_ast_to_lines(node.left, indent + 1))
        out.extend(_ast_to_lines(node.right, indent + 1))
        return out
    if isinstance(node, ast.CondNot):
        out = [f"{pad}CondNot"]
        out.extend(_ast_to_lines(node.child, indent + 1))
        return out
    if isinstance(node, ast.CreateTable):
        return [f"{pad}CreateTable({node.name}, {node.columns})"]
    if isinstance(node, ast.Insert):
        return [f"{pad}Insert({node.table}, {node.values})"]
    return [f"{pad}{type(node).__name__}"]


def _short(x):
    if x == "*":
        return "*"
    if isinstance(x, ast.ColumnRef):
        q = f"{x.qualifier}." if x.qualifier else ""
        return f"{q}{x.name}"
    return str(x)


# ------------------------------------------------------------
# Commands
# ------------------------------------------------------------

def cmd_parse(args):
    try:
        tokens = SqlLexer(args.query).tokens()
        stmts = SqlParser(tokens).parse_statements()
    except EngineError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    if args.explain:
        for s in stmts:
            print("\n".join(_ast_to_lines(s)))
    else:
        print(json.dumps([{"type": type(s).__name__} for s in stmts]))


def _compile_file(db, path):
    with open(path) as f:
        sql = f.read()
    cat = Catalog(db)
    stmts = parse_sql(sql)
    plans = []
    for s in stmts:
        resolve(s, cat)
        plans.append(translate(s))
    return plans


def cmd_compile(args):
    try:
        plans = _compile_file(args.db, args.file)
    except EngineError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    if args.json:
        print(json.dumps(plans, indent=2))
    else:
        for p in plans:
            print(json.dumps(p))


def cmd_check(args):
    try:
        _compile_file(args.db, args.file)
    except EngineError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


# ------------------------------------------------------------
# Entry point
# ------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="sql")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("parse")
    p.add_argument("--query", required=True)
    p.add_argument("--explain", action="store_true")
    p.set_defaults(fn=cmd_parse)

    p = sub.add_parser("compile")
    p.add_argument("--db", required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_compile)

    p = sub.add_parser("check")
    p.add_argument("--db", required=True)
    p.add_argument("--file", required=True)
    p.set_defaults(fn=cmd_check)

    args = ap.parse_args(argv)
    try:
        args.fn(args)
    except EngineError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    except SystemExit:
        raise
    except Exception as e:
        print(f"error: internal: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()