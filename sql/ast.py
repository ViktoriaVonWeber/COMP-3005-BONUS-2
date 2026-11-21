# sql/ast.py - SQL AST nodes.

class Node:
    pass


# ---------- statements ----------
class CreateTable(Node):
    def __init__(self, name, columns):
        # columns: list of (name, type_str, varchar_len_or_None)
        self.name = name
        self.columns = columns


class Insert(Node):
    def __init__(self, table, values):
        self.table = table
        self.values = values  # list of literal values (int or str)


class Query(Node):
    def __init__(self, blocks, setops):
        # blocks: list of SelectBlock or nested Query (parenthesized)
        # setops: list of "union" / "union_all" / "intersect" / "except"
        # len(setops) == len(blocks) - 1
        self.blocks = blocks
        self.setops = setops


class SelectBlock(Node):
    def __init__(self):
        self.distinct = False
        self.select_list = []   # list of SelectItem or Star
        self.from_list = []     # list of TableRef / SubqueryRef / Join (chained)
        self.where = None       # condition node or None
        self.group_by = []      # list of ColumnRef
        self.having = None      # condition node or None
        self.order_by = []      # list of (ColumnRef-or-alias-str, "asc"|"desc")
        self.limit = None


class Star(Node):
    pass


class SelectItem(Node):
    def __init__(self, expr, alias):
        self.expr = expr      # ColumnRef or Aggregate
        self.alias = alias    # str or None


class Aggregate(Node):
    def __init__(self, kind, arg):
        self.kind = kind      # "count" | "sum" | "avg" | "min" | "max"
        self.arg = arg        # ColumnRef or "*" (for count(*))


class ColumnRef(Node):
    def __init__(self, qualifier, name):
        self.qualifier = qualifier   # str or None
        self.name = name


class TableRef(Node):
    def __init__(self, name, alias):
        self.name = name
        self.alias = alias or name


class SubqueryRef(Node):
    def __init__(self, query, alias):
        self.query = query
        self.alias = alias


class Join(Node):
    def __init__(self, left, right, condition):
        self.left = left
        self.right = right
        self.condition = condition  # condition node


# ---------- condition nodes (SQL-level) ----------
# These are produced by the SQL parser for both WHERE and HAVING.
# The translator in sql/translator.py is responsible for turning them
# into whatever the execution layer's condition representation is.

class CondCmp(Node):
    def __init__(self, op, left, right):
        # op: "=" | "<>" | "<" | "<=" | ">" | ">="
        # left, right: operand nodes (ColumnRef, Literal, AggRef)
        self.op = op
        self.left = left
        self.right = right


class CondAnd(Node):
    def __init__(self, left, right):
        self.left = left
        self.right = right


class CondOr(Node):
    def __init__(self, left, right):
        self.left = left
        self.right = right


class CondNot(Node):
    def __init__(self, child):
        self.child = child


class Literal(Node):
    def __init__(self, value):
        # value: int or str
        self.value = value


class AggRef(Node):
    """An aggregate used as an operand inside HAVING (e.g. COUNT(*) > 1)."""
    def __init__(self, kind, arg):
        # kind: "count" | "sum" | "avg" | "min" | "max"
        # arg: ColumnRef or "*"
        self.kind = kind
        self.arg = arg