# tree.py — AST nodes. Every node round-trips through JSON.
from dataclasses import dataclass, asdict

@dataclass
class Relation:
    name: str
    def to_json(self): return {"op": "relation", "name": self.name}

@dataclass
class Select:
    cond: dict
    child: object
    def to_json(self): return {"op": "select", "cond": self.cond, "child": self.child.to_json()}

@dataclass
class Project:
    attrs: list
    child: object
    def to_json(self): return {"op": "project", "attrs": self.attrs, "child": self.child.to_json()}

@dataclass
class Rename:
    name: str
    child: object
    def to_json(self): return {"op": "rename", "name": self.name, "child": self.child.to_json()}

@dataclass
class SetOp:
    kind: str          # union / intersect / minus
    left: object
    right: object
    def to_json(self): return {"op": self.kind, "left": self.left.to_json(), "right": self.right.to_json()}

@dataclass
class Join:
    cond: dict
    left: object
    right: object
    def to_json(self): return {"op": "join", "cond": self.cond, "left": self.left.to_json(), "right": self.right.to_json()}

@dataclass
class Times:
    left: object
    right: object
    def to_json(self): return {"op": "times", "left": self.left.to_json(), "right": self.right.to_json()}

@dataclass
class Distinct:
    child: object
    def to_json(self): return {"op": "distinct", "child": self.child.to_json()}

# --- Conditions ---
@dataclass
class Cmp:
    op: str; left: dict; right: dict
    def to_json(self): return {"op": "cmp", "cmp": self.op, "left": self.left, "right": self.right}

@dataclass
class And:
    left: object; right: object
    def to_json(self): return {"op": "and", "left": self.left.to_json(), "right": self.right.to_json()}

@dataclass
class Or:
    left: object; right: object
    def to_json(self): return {"op": "or", "left": self.left.to_json(), "right": self.right.to_json()}

@dataclass
class Not:
    child: object
    def to_json(self): return {"op": "not", "child": self.child.to_json()}

def json_to_tree(obj):
    """Inverse of node.to_json(). Raises EngineError on unknown op."""
    from errors import EngineError
    op = obj.get("op")
    if op == "relation":  return Relation(obj["name"])
    if op == "select":    return Select(obj["cond"], json_to_tree(obj["child"]))
    if op == "project":   return Project(obj["attrs"], json_to_tree(obj["child"]))
    if op == "rename":    return Rename(obj["name"], json_to_tree(obj["child"]))
    if op in ("union","intersect","minus"):
        return SetOp(op, json_to_tree(obj["left"]), json_to_tree(obj["right"]))
    if op == "join":      return Join(obj["cond"], json_to_tree(obj["left"]), json_to_tree(obj["right"]))
    if op == "times":     return Times(json_to_tree(obj["left"]), json_to_tree(obj["right"]))
    if op == "distinct":  return Distinct(json_to_tree(obj["child"]))
    if op == "cmp":       return Cmp(obj["cmp"], obj["left"], obj["right"])
    if op == "and":       return And(json_to_tree(obj["left"]), json_to_tree(obj["right"]))
    if op == "or":        return Or(json_to_tree(obj["left"]), json_to_tree(obj["right"]))
    if op == "not":       return Not(json_to_tree(obj["child"]))
    raise EngineError("schema", f"unknown tree node '{op}'")