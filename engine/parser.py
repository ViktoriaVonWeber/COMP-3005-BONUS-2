from engine.lexer import (IDENT, NUMBER, STRING, OP, LPAREN, RPAREN,
                          LBRACK, RBRACK, COMMA, DOT, EOF)
from engine.tree import (Relation, Select, Project, Rename, SetOp, Join,
                         Times, Cmp, And, Or, Not)
from engine.errors import EngineError

class Parser:
    def __init__(self, tokens):
        self.toks = tokens; self.i = 0
    def peek(self):    return self.toks[self.i]
    def advance(self): t = self.toks[self.i]; self.i += 1; return t
    def match(self, kind, text=None):
        t = self.peek()
        if t.kind == kind and (text is None or t.text == text):
            return self.advance()
        return None
    def expect(self, kind, text=None):
        t = self.match(kind, text)
        if t is None:
            raise EngineError("syntax", f"expected {text or kind}", self.peek().pos)
        return t

    def parse_query(self):
        q = self.parse_join()
        if self.peek().kind != EOF:
            raise EngineError("syntax", "trailing input", self.peek().pos)
        return q

    def parse_join(self):
        left = self.parse_set()
        while True:
            t = self.peek()
            if t.kind == IDENT and t.text == "join":
                self.advance(); self.expect(LBRACK)
                cond = self.parse_condition(); self.expect(RBRACK)
                left = Join(cond, left, self.parse_set())
            elif t.kind == IDENT and t.text == "times":
                self.advance(); left = Times(left, self.parse_set())
            else:
                return left

    def parse_set(self):
        left = self.parse_primary()
        while True:
            t = self.peek()
            if t.kind == IDENT and t.text in ("union","intersect","minus"):
                kind = t.text; self.advance()
                left = SetOp(kind, left, self.parse_primary())
            else:
                return left

    def parse_primary(self):
        t = self.peek()
        if t.kind == IDENT:
            kw = t.text
            if kw == "select":
                self.advance(); self.expect(LBRACK)
                cond = self.parse_condition(); self.expect(RBRACK)
                self.expect(LPAREN); q = self.parse_join(); self.expect(RPAREN)
                return Select(cond, q)
            if kw == "project":
                self.advance(); self.expect(LBRACK)
                attrs = [self.parse_operand()]
                while self.match(COMMA):
                    attrs.append(self.parse_operand())
                self.expect(RBRACK)
                self.expect(LPAREN); q = self.parse_join(); self.expect(RPAREN)
                return Project(attrs, q)
            if kw == "rename":
                self.advance(); self.expect(LBRACK)
                name = self.expect(IDENT).text
                self.expect(RBRACK)
                self.expect(LPAREN); q = self.parse_join(); self.expect(RPAREN)
                return Rename(name, q)
            self.advance(); return Relation(kw)
        if t.kind == LPAREN:
            self.advance(); q = self.parse_join(); self.expect(RPAREN); return q
        raise EngineError("syntax", "expected relational expression", t.pos)

    def parse_condition(self): return self.parse_or()
    def parse_or(self):
        left = self.parse_and()
        while self.peek().kind == IDENT and self.peek().text.lower() == "or":
            self.advance(); left = Or(left, self.parse_and())
        return left
    def parse_and(self):
        left = self.parse_not()
        while self.peek().kind == IDENT and self.peek().text.lower() == "and":
            self.advance(); left = And(left, self.parse_not())
        return left
    def parse_not(self):
        if self.peek().kind == IDENT and self.peek().text.lower() == "not":
            self.advance(); return Not(self.parse_not())
        return self.parse_cmp()
    def parse_cmp(self):
        left = self.parse_operand()
        t = self.peek()
        if t.kind != OP:
            raise EngineError("syntax", "expected comparison operator", t.pos)
        op = self.advance().text
        return Cmp(op, left, self.parse_operand())
    def parse_operand(self):
        t = self.peek()
        if t.kind == NUMBER:
            self.advance(); return {"kind":"num","value":int(t.text)}
        if t.kind == STRING:
            self.advance(); return {"kind":"str","value":t.text}
        if t.kind == IDENT:
            qual = t.text; self.advance()
            if self.match(DOT):
                name = self.expect(IDENT).text
                return {"kind":"attr","qual":qual,"name":name}
            return {"kind":"attr","qual":None,"name":qual}
        raise EngineError("syntax", "expected operand", t.pos)