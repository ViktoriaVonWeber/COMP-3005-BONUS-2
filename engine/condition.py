# engine/condition.py - standalone condition parser and evaluator.
# Project 2 section 1.3 point 2: this is the single most reused piece
# of the P1 engine. Modules 2, 3 and 4 all need to parse a condition
# string and evaluate it against a tuple.
from engine.lexer import Lexer, IDENT, NUMBER, STRING, OP, LPAREN, RPAREN, DOT, EOF
from engine.tree import Cmp, And, Or, Not
from engine.errors import EngineError


def parse_condition(text):
    """Parse a condition string like "R.a > 500 and R.b = 'cs'" into a tree."""
    return _CondParser(Lexer(text).tokens()).parse()


def eval_condition(node, tup, schema):
    """Evaluate a condition tree against a tuple given a schema.
    schema = [(qualified_name, type), ...] in column order."""
    if isinstance(node, And):
        return eval_condition(node.left, tup, schema) and eval_condition(node.right, tup, schema)
    if isinstance(node, Or):
        return eval_condition(node.left, tup, schema) or eval_condition(node.right, tup, schema)
    if isinstance(node, Not):
        return not eval_condition(node.child, tup, schema)
    if isinstance(node, Cmp):
        left = _operand(node.left, tup, schema)
        right = _operand(node.right, tup, schema)
        return _cmp(node.op, left, right)
    raise EngineError("schema", f"bad condition node {type(node).__name__}")


def _operand(op, tup, schema):
    if op["kind"] == "num":
        return op["value"]
    if op["kind"] == "str":
        return op["value"]
    if op["kind"] == "attr":
        name = op["name"]
        qual = op.get("qual")
        if qual is not None:
            full = f"{qual}.{name}"
            for i, (qn, _t) in enumerate(schema):
                if qn == full:
                    return tup[i]
            raise EngineError("name", f"unknown attribute {full}")
        matches = [i for i, (qn, _t) in enumerate(schema) if qn.split(".")[-1] == name]
        if len(matches) == 0:
            raise EngineError("name", f"unknown attribute {name}")
        if len(matches) > 1:
            raise EngineError("ambiguity", f"ambiguous attribute {name}")
        return tup[matches[0]]
    raise EngineError("schema", "bad operand")


def _cmp(op, a, b):
    if type(a) is not type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        raise EngineError("type", f"cannot compare {type(a).__name__} to {type(b).__name__}")
    return {"=": a == b, "!=": a != b, "<>": a != b, "<": a < b, "<=": a <= b,
            ">": a > b, ">=": a >= b}[op]


class _CondParser:
    def __init__(self, tokens):
        self.toks = tokens
        self.i = 0

    def parse(self):
        node = self._or()
        if self._peek().kind != EOF:
            raise EngineError("syntax", "trailing input in condition", self._peek().pos)
        return node

    def _peek(self):
        return self.toks[self.i]

    def _advance(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def _match(self, kind, text=None):
        t = self._peek()
        if t.kind == kind and (text is None or t.text == text):
            return self._advance()
        return None

    def _expect(self, kind, text=None):
        t = self._match(kind, text)
        if t is None:
            raise EngineError("syntax", f"expected {text or kind}", self._peek().pos)
        return t

    def _or(self):
        left = self._and()
        while self._peek().kind == IDENT and self._peek().text.lower() == "or":
            self._advance()
            left = Or(left, self._and())
        return left

    def _and(self):
        left = self._not()
        while self._peek().kind == IDENT and self._peek().text.lower() == "and":
            self._advance()
            left = And(left, self._not())
        return left

    def _not(self):
        if self._peek().kind == IDENT and self._peek().text.lower() == "not":
            self._advance()
            return Not(self._not())
        return self._cmp()

    def _cmp(self):
        left = self._operand()
        t = self._peek()
        if t.kind != OP:
            raise EngineError("syntax", "expected comparison operator", t.pos)
        op = self._advance().text
        right = self._operand()
        return Cmp(op, left, right)

    def _operand(self):
        t = self._peek()
        if t.kind == NUMBER:
            self._advance()
            return {"kind": "num", "value": int(t.text)}
        if t.kind == STRING:
            self._advance()
            return {"kind": "str", "value": t.text}
        if t.kind == IDENT:
            qual = t.text
            self._advance()
            if self._match(DOT):
                name = self._expect(IDENT).text
                return {"kind": "attr", "qual": qual, "name": name}
            return {"kind": "attr", "qual": None, "name": qual}
        raise EngineError("syntax", "expected operand", t.pos)