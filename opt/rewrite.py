# opt/rewrite.py - rewrite rules. M4 section 3.
#
# Rules:
#   1. Split conjunctions. select[p AND q] -> two stacked selects.
#   2. Push a select below a join or cross (single-sided predicate).
#   3. Turn cross plus select into join (two-sided equality predicate).
#   4. Push a select below a rename, union, or union_all.
#   5. Push a select below a group_by (grouping columns only).
#   6. Push projections down.
#   7. Remove a projection that drops nothing; collapse adjacent selects.
#
# Predicate side-classification uses *table names*, not column names:
# a predicate like "Emp.age > 50" belongs to the side whose scan covers
# the table Emp. This is what lets Rule 2 push predicates below a join.

from engine.errors import EngineError


def rewrite(plan, enabled_rules=None):
    if enabled_rules is None:
        enabled_rules = {1, 2, 3, 4, 5, 6, 7}
    p = _deep_copy(plan)

    if 7 in enabled_rules:
        p = _collapse_selects(p)

    if any(r in enabled_rules for r in (1, 2, 3, 4, 5, 7)):
        for _ in range(20):
            before = _to_json(p)
            if 1 in enabled_rules:
                p = _split_conjunctions(p)
            if 2 in enabled_rules:
                p = _push_selects(p)
            if 3 in enabled_rules:
                p = _cross_to_join(p)
            if 4 in enabled_rules:
                p = _push_select_past_setop(p)
            if 5 in enabled_rules:
                p = _push_select_past_group(p)
            if 7 in enabled_rules:
                p = _collapse_selects(p)
            if _to_json(p) == before:
                break

    if 6 in enabled_rules:
        p = _push_projections(p)

    if 7 in enabled_rules:
        p = _collapse_selects(p)
        p = _remove_noop_projects(p)

    return p


# ------------------------------------------------------------
# Rule 1: split conjunctions
# ------------------------------------------------------------

def _split_conjunctions(node):
    if not isinstance(node, dict):
        return node
    for k in ("child", "left", "right"):
        if k in node:
            node[k] = _split_conjunctions(node[k])
    if node.get("op") == "select" and " and " in node.get("condition", ""):
        parts = _split_and(node["condition"])
        if len(parts) > 1:
            child = node["child"]
            for part in reversed(parts):
                child = {"op": "select", "condition": part, "child": child}
            return child
    return node


def _split_and(cond):
    parts = []
    depth = 0
    buf = []
    i = 0
    s = _strip_outer_parens(cond.strip())
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
    return [_strip_outer_parens(p) for p in parts]


def _strip_outer_parens(s):
    s = s.strip()
    while s.startswith("(") and s.endswith(")"):
        depth = 0
        ok = True
        for i, c in enumerate(s):
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            if depth == 0 and i < len(s) - 1:
                ok = False
                break
        if ok:
            s = s[1:-1].strip()
        else:
            break
    return s


# ------------------------------------------------------------
# Rule 2: push a select below a join or cross
# ------------------------------------------------------------

def _push_selects(node):
    if not isinstance(node, dict):
        return node
    if node.get("op") == "select" and "child" in node:
        child = _push_selects(node["child"])
        node["child"] = child
        if child.get("op") in ("join", "cross"):
            left_tables = _collect_tables(child["left"])
            right_tables = _collect_tables(child["right"])
            preds = _split_and(node["condition"])
            left_preds, right_preds, keep = [], [], []
            for p in preds:
                side = _predicate_side(p, left_tables, right_tables)
                if side == "left":
                    left_preds.append(p)
                elif side == "right":
                    right_preds.append(p)
                else:
                    keep.append(p)
            if left_preds or right_preds:
                new_left = child["left"]
                new_right = child["right"]
                for p in left_preds:
                    new_left = {"op": "select", "condition": p, "child": new_left}
                for p in right_preds:
                    new_right = {"op": "select", "condition": p, "child": new_right}
                new_node = dict(child)
                new_node["left"] = new_left
                new_node["right"] = new_right
                if keep:
                    return {"op": "select", "condition": " and ".join(keep),
                            "child": new_node}
                return new_node
        return node
    for k in ("child", "left", "right"):
        if k in node:
            node[k] = _push_selects(node[k])
    return node


def _collect_tables(node):
    """Set of base table names appearing in a subtree."""
    tables = set()
    def walk(n):
        if not isinstance(n, dict):
            return
        if n.get("op") == "scan" and "table" in n:
            tables.add(n["table"])
        for k in ("child", "left", "right"):
            if k in n:
                walk(n[k])
    walk(node)
    return tables


def _tokens_in_condition(cond):
    import re
    return re.findall(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", cond)


def _predicate_side(pred, left_tables, right_tables):
    """Which side does a predicate reference exclusively?"""
    import re
    qualified = set(re.findall(
        r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", pred))
    if not qualified:
        return "both"
    pred_tables = set(q.split(".", 1)[0] for q in qualified)
    only_left = pred_tables <= left_tables and not (pred_tables & right_tables)
    only_right = pred_tables <= right_tables and not (pred_tables & left_tables)
    if only_left:
        return "left"
    if only_right:
        return "right"
    return "both"


# ------------------------------------------------------------
# Rule 3: turn cross plus select into join
# ------------------------------------------------------------

def _cross_to_join(node):
    if not isinstance(node, dict):
        return node
    for k in ("child", "left", "right"):
        if k in node:
            node[k] = _cross_to_join(node[k])
    if node.get("op") == "select" and "child" in node:
        child = node["child"]
        if child.get("op") == "cross":
            preds = _split_and(node["condition"])
            eqs = [p for p in preds if _is_equality(p)]
            rest = [p for p in preds if not _is_equality(p)]
            if eqs:
                new_node = {
                    "op": "join",
                    "condition": " and ".join(eqs),
                    "left": child["left"],
                    "right": child["right"],
                }
                if rest:
                    return {"op": "select", "condition": " and ".join(rest),
                            "child": new_node}
                return new_node
    return node


def _is_equality(pred):
    import re
    m = re.match(
        r"^\s*([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
        r"([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\s*$",
        pred,
    )
    return m is not None


# ------------------------------------------------------------
# Rule 4: push a select below a rename, union, or union_all
# ------------------------------------------------------------

def _push_select_past_setop(node):
    if not isinstance(node, dict):
        return node
    for k in ("child", "left", "right"):
        if k in node:
            node[k] = _push_select_past_setop(node[k])
    if node.get("op") == "select" and "child" in node:
        child = node["child"]
        if child.get("op") == "rename":
            return {
                "op": "rename",
                "to": child["to"],
                "child": {"op": "select",
                          "condition": node["condition"],
                          "child": child["child"]},
            }
        if child.get("op") in ("union", "union_all"):
            return {
                "op": child["op"],
                "left": {"op": "select", "condition": node["condition"],
                         "child": child["left"]},
                "right": {"op": "select", "condition": node["condition"],
                          "child": child["right"]},
            }
    return node


# ------------------------------------------------------------
# Rule 5: push a select below a group_by
# ------------------------------------------------------------

def _push_select_past_group(node):
    if not isinstance(node, dict):
        return node
    for k in ("child", "left", "right"):
        if k in node:
            node[k] = _push_select_past_group(node[k])
    if node.get("op") == "select" and "child" in node:
        child = node["child"]
        if child.get("op") == "group_by":
            grouping = set(child.get("keys", []))
            preds = _split_and(node["condition"])
            pushable = []
            keep = []
            for p in preds:
                if _has_aggregate_ref(p):
                    keep.append(p)
                    continue
                cols = set(_tokens_in_condition(p))
                if cols <= grouping:
                    pushable.append(p)
                else:
                    keep.append(p)
            if pushable:
                new_child = dict(child)
                new_child["child"] = {
                    "op": "select",
                    "condition": " and ".join(pushable),
                    "child": child["child"],
                }
                if keep:
                    return {
                        "op": "select",
                        "condition": " and ".join(keep),
                        "child": new_child,
                    }
                return new_child
    return node


def _has_aggregate_ref(pred):
    import re
    return bool(re.search(r"\b(count|sum|avg|min|max)(_[A-Za-z0-9_]+)?\b", pred))


# ------------------------------------------------------------
# Rule 6: push projections down
# ------------------------------------------------------------

def _push_projections(node, needed=None):
    if not isinstance(node, dict):
        return node
    if needed is None:
        needed = _columns_referenced_from_root(node)
    op = node.get("op")
    if op == "project":
        keep = set(node.get("columns", []))
        if "child" in node:
            node["child"] = _push_projections(node["child"], keep)
    elif op in ("select", "filter"):
        self_cols = set(_tokens_in_condition(node.get("condition", "")))
        keep = needed | self_cols
        if "child" in node:
            node["child"] = _push_projections(node["child"], keep)
    elif op == "sort":
        self_cols = {k[0] for k in node.get("keys", [])}
        keep = needed | self_cols
        if "child" in node:
            node["child"] = _push_projections(node["child"], keep)
    elif op == "group_by":
        self_cols = set(node.get("keys", []))
        keep = needed | self_cols
        if "child" in node:
            node["child"] = _push_projections(node["child"], keep)
    elif op in ("join", "cross"):
        self_cols = set(_tokens_in_condition(node.get("condition", "")))
        keep = needed | self_cols
        if "left" in node:
            node["left"] = _push_projections(node["left"], keep)
        if "right" in node:
            node["right"] = _push_projections(node["right"], keep)
    elif op in ("union", "union_all", "intersect", "except"):
        if "left" in node:
            node["left"] = _push_projections(node["left"], needed)
        if "right" in node:
            node["right"] = _push_projections(node["right"], needed)
    else:
        for k in ("child", "left", "right"):
            if k in node:
                node[k] = _push_projections(node[k], needed)
    return node


def _columns_referenced_from_root(node):
    cols = set()
    def walk(n):
        if not isinstance(n, dict):
            return
        if "condition" in n and isinstance(n["condition"], str):
            cols.update(_tokens_in_condition(n["condition"]))
        if "columns" in n and isinstance(n["columns"], list):
            cols.update(n["columns"])
        if "keys" in n and isinstance(n["keys"], list):
            for k in n["keys"]:
                if isinstance(k, (list, tuple)) and k:
                    cols.add(k[0])
                elif isinstance(k, str):
                    cols.add(k)
        if "aggregates" in n and isinstance(n["aggregates"], list):
            for ag in n["aggregates"]:
                if len(ag) >= 2 and isinstance(ag[1], str):
                    cols.add(ag[1])
        for k in ("child", "left", "right"):
            if k in n:
                walk(n[k])
    walk(node)
    return cols


# ------------------------------------------------------------
# Rule 7: collapse adjacent selects
# ------------------------------------------------------------

def _remove_noop_projects(node):
    if not isinstance(node, dict):
        return node
    for k in ("child", "left", "right"):
        if k in node:
            node[k] = _remove_noop_projects(node[k])
    return node


def _collapse_selects(node):
    if not isinstance(node, dict):
        return node
    for k in ("child", "left", "right"):
        if k in node:
            node[k] = _collapse_selects(node[k])
    if node.get("op") == "select" and "child" in node:
        child = node["child"]
        if child.get("op") == "select":
            merged = f"{node['condition']} and {child['condition']}"
            return {
                "op": "select",
                "condition": merged,
                "child": child["child"],
            }
    return node


# ------------------------------------------------------------
# Utilities
# ------------------------------------------------------------

def _deep_copy(node):
    import json
    return json.loads(json.dumps(node))


def _to_json(node):
    import json
    return json.dumps(node, sort_keys=True)