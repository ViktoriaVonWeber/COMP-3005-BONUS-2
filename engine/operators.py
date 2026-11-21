from engine.tree import (Relation, Select, Project, Rename, SetOp, Join,
                         Times, Distinct, Cmp, And, Or, Not)
from engine.errors import EngineError

def evaluate(tree, catalog):
    if isinstance(tree, Relation):
        rel = catalog.get(tree.name)
        if rel is None:
            raise EngineError("name", f"unknown relation {tree.name}")
        return list(rel.rows)
    if isinstance(tree, Select):
        rows = evaluate(tree.child, catalog)
        schema = _schema_of(tree.child, catalog)
        return [r for r in rows if _eval_cond(tree.cond, r, schema)]
    if isinstance(tree, Project):
        rows = evaluate(tree.child, catalog)
        schema = _schema_of(tree.child, catalog)
        idx = [_resolve_attr(a, schema) for a in tree.attrs]
        return [tuple(r[i] for i in idx) for r in rows]
    if isinstance(tree, Distinct):
        rows = evaluate(tree.child, catalog)
        seen = set(); out = []
        for r in rows:
            if r not in seen:
                seen.add(r); out.append(r)
        return out
    if isinstance(tree, Rename):
        return evaluate(tree.child, catalog)
    if isinstance(tree, Times):
        L = evaluate(tree.left, catalog); R = evaluate(tree.right, catalog)
        return [l + r for l in L for r in R]
    if isinstance(tree, Join):
        L = evaluate(tree.left, catalog); R = evaluate(tree.right, catalog)
        ls = _schema_of(tree.left, catalog); rs = _schema_of(tree.right, catalog)
        out = []
        for l in L:
            for r in R:
                row = l + r
                if _eval_cond(tree.cond, row, ls + rs):
                    out.append(row)
        return out
    if isinstance(tree, SetOp):
        L = evaluate(tree.left, catalog); R = evaluate(tree.right, catalog)
        if tree.kind == "union":
            return list({tuple(r) for r in L} | {tuple(r) for r in R})
        if tree.kind == "intersect":
            return list({tuple(r) for r in L} & {tuple(r) for r in R})
        if tree.kind == "minus":
            return list({tuple(r) for r in L} - {tuple(r) for r in R})
    raise EngineError("schema", f"cannot evaluate {type(tree).__name__}")

def _schema_of(tree, catalog):
    if isinstance(tree, Relation):
        return catalog[tree.name].schema()
    if isinstance(tree, Select):
        return _schema_of(tree.child, catalog)
    if isinstance(tree, Project):
        return [(a["name"], "INT") for a in tree.attrs]
    if isinstance(tree, (Rename, Distinct)):
        return _schema_of(tree.child, catalog)
    if isinstance(tree, (Times, Join)):
        return _schema_of(tree.left, catalog) + _schema_of(tree.right, catalog)
    if isinstance(tree, SetOp):
        return _schema_of(tree.left, catalog)
    raise EngineError("schema", f"no schema for {type(tree).__name__}")

def _resolve_attr(attr, schema):
    for i, (n, _t) in enumerate(schema):
        if n == attr["name"]:
            return i
    raise EngineError("name", f"unknown attribute {attr['name']}")

def _eval_cond(cond, row, schema):
    if isinstance(cond, And):
        return _eval_cond(cond.left, row, schema) and _eval_cond(cond.right, row, schema)
    if isinstance(cond, Or):
        return _eval_cond(cond.left, row, schema) or _eval_cond(cond.right, row, schema)
    if isinstance(cond, Not):
        return not _eval_cond(cond.child, row, schema)
    if isinstance(cond, Cmp):
        l = _eval_operand(cond.left, row, schema)
        r = _eval_operand(cond.right, row, schema)
        return _cmp(cond.op, l, r)
    raise EngineError("schema", f"bad condition {type(cond).__name__}")

def _eval_operand(op, row, schema):
    if op["kind"] == "num":  return op["value"]
    if op["kind"] == "str":  return op["value"]
    if op["kind"] == "attr":
        for i, (n, _t) in enumerate(schema):
            if n == op["name"]:
                return row[i]
        raise EngineError("name", f"unknown attribute {op['name']}")
    raise EngineError("schema", "bad operand")

def _cmp(op, a, b):
    if type(a) is not type(b) and not (isinstance(a,(int,float)) and isinstance(b,(int,float))):
        raise EngineError("type", f"cannot compare {type(a).__name__} to {type(b).__name__}")
    return {"=": a==b, "!=": a!=b, "<>": a!=b, "<": a<b, "<=": a<=b, ">": a>b, ">=": a>=b}[op]