# sql/parser.py - recursive-descent SQL parser. M3 section 2.
#
# Design notes:
# - WHERE and HAVING conditions are parsed with this file's own
#   condition parser (see _parse_condition below), not with
#   engine.condition. The reason: HAVING can contain aggregates
#   like COUNT(*), which engine.condition cannot represent, and
#   SQL comparison operators include <> which engine.condition's
#   lexer does not tokenize. Doing it here keeps sql/ self-contained
#   and keeps engine.condition as the P1 relational-algebra parser
#   it was.
# - The lexer is sql.lexer.SqlLexer, which already handles <>,
#   case-insensitive keywords, -- comments, and STAR.
# - Conditions produce sql.ast.CondCmp / CondAnd / CondOr / CondNot,
#   with operands being ColumnRef, Literal, or AggRef.
# - Table aliases may be written with or without AS. The EBNF in
#   M3 section 2 writes "[ AS ident ]", but the examples in section
#   5.2 use bare aliases ("FROM Emp e JOIN Dept d"), so we accept
#   both. Deviation recorded in GRAMMAR_SQL.md.

from sql.lexer import (
    SqlLexer, KEYWORD, IDENT, NUMBER, STRING, OP,
    LPAREN, RPAREN, COMMA, DOT, SEMI, STAR, EOF,
)
from sql import ast
from engine.errors import EngineError


# ============================================================
# Condition parser (used for WHERE and HAVING)
# ============================================================

class _CondParser:
    """Parses the SQL condition grammar:
         condition   := disjunction
         disjunction := conjunction { OR conjunction }
         conjunction := negation { AND negation }
         negation    := NOT negation | comparison
         comparison  := operand COMPOP operand
         operand     := literal | column | aggregate | '(' condition ')'
    Precedence, tightest first: NOT, AND, OR.
    """
    def __init__(self, toks, pos):
        self.toks = toks
        self.i = pos

    def peek(self):
        return self.toks[self.i]

    def advance(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def match(self, kind, text=None):
        t = self.peek()
        if t.kind == kind and (text is None or t.text == text):
            return self.advance()
        return None

    def expect(self, kind, text=None):
        t = self.match(kind, text)
        if t is None:
            exp = text if text else kind
            raise EngineError("syntax",
                f"expected {exp}, got {self.peek().text!r}",
                f"line {self.peek().line}, column {self.peek().col}")
        return t

    def parse(self):
        return self._or()

    def _or(self):
        left = self._and()
        while self.peek().kind == KEYWORD and self.peek().text == "OR":
            self.advance()
            left = ast.CondOr(left, self._and())
        return left

    def _and(self):
        left = self._not()
        while self.peek().kind == KEYWORD and self.peek().text == "AND":
            self.advance()
            left = ast.CondAnd(left, self._not())
        return left

    def _not(self):
        if self.peek().kind == KEYWORD and self.peek().text == "NOT":
            self.advance()
            return ast.CondNot(self._not())
        return self._cmp()

    def _cmp(self):
        left = self._operand()
        t = self.peek()
        if t.kind != OP:
            raise EngineError("syntax",
                f"expected comparison operator, got {t.text!r}",
                f"line {t.line}, column {t.col}")
        op = self.advance().text
        if op == "!=":
            op = "<>"
        right = self._operand()
        return ast.CondCmp(op, left, right)

    def _operand(self):
        t = self.peek()
        if t.kind == NUMBER:
            self.advance()
            return ast.Literal(int(t.text))
        if t.kind == STRING:
            self.advance()
            return ast.Literal(t.text)
        if t.kind == KEYWORD and t.text in ("COUNT", "SUM", "AVG", "MIN", "MAX"):
            kind = t.text.lower()
            self.advance()
            self.expect(LPAREN)
            if self.match(STAR):
                self.expect(RPAREN)
                if kind != "count":
                    raise EngineError("aggregate",
                        f"{kind.upper()}(*) is not valid",
                        f"line {t.line}, column {t.col}")
                return ast.AggRef("count", "*")
            col = self._parse_column_ref()
            self.expect(RPAREN)
            return ast.AggRef(kind, col)
        if t.kind == LPAREN:
            self.advance()
            inner = self._or()
            self.expect(RPAREN)
            return inner
        if t.kind == IDENT:
            return self._parse_column_ref()
        raise EngineError("syntax",
            f"expected operand, got {t.text!r}",
            f"line {t.line}, column {t.col}")

    def _parse_column_ref(self):
        t = self.expect(IDENT)
        if self.match(DOT):
            name = self.expect(IDENT)
            return ast.ColumnRef(t.text, name.text)
        return ast.ColumnRef(None, t.text)


# ============================================================
# Main parser
# ============================================================

class SqlParser:
    def __init__(self, tokens):
        self.toks = tokens
        self.i = 0

    # --- primitives ---
    def _peek(self, off=0):
        return self.toks[min(self.i + off, len(self.toks) - 1)]

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
            exp = text if text else kind
            raise EngineError("syntax",
                f"expected {exp}, got {self._peek().text!r}",
                f"line {self._peek().line}, column {self._peek().col}")
        return t

    def _at_keyword(self, word):
        t = self._peek()
        return t.kind == KEYWORD and t.text == word

    # --- entry ---
    def parse_statements(self):
        stmts = []
        while self._peek().kind != EOF:
            if self._match(SEMI):
                continue
            stmts.append(self._parse_statement())
            self._match(SEMI)
        return stmts

    def _parse_statement(self):
        t = self._peek()
        if t.kind == KEYWORD and t.text == "CREATE":
            return self._parse_create()
        if t.kind == KEYWORD and t.text == "INSERT":
            return self._parse_insert()
        if t.kind == KEYWORD and t.text == "SELECT":
            return self._parse_query()
        if t.kind == LPAREN:
            return self._parse_query()
        raise EngineError("syntax",
            f"expected CREATE, INSERT, or SELECT, got {t.text!r}",
            f"line {t.line}, column {t.col}")

    # --- CREATE TABLE ---
    def _parse_create(self):
        self._expect(KEYWORD, "CREATE")
        self._expect(KEYWORD, "TABLE")
        name = self._expect(IDENT).text
        self._expect(LPAREN)
        cols = [self._parse_coldef()]
        while self._match(COMMA):
            cols.append(self._parse_coldef())
        self._expect(RPAREN)
        return ast.CreateTable(name, cols)

    def _parse_coldef(self):
        name = self._expect(IDENT).text
        t = self._peek()
        if t.kind == KEYWORD and t.text == "INT":
            self._advance()
            return (name, "INT", None)
        if t.kind == KEYWORD and t.text == "VARCHAR":
            self._advance()
            self._expect(LPAREN)
            n = self._expect(NUMBER)
            self._expect(RPAREN)
            return (name, "VARCHAR", int(n.text))
        raise EngineError("syntax",
            f"expected INT or VARCHAR, got {t.text!r}",
            f"line {t.line}, column {t.col}")

    # --- INSERT ---
    def _parse_insert(self):
        self._expect(KEYWORD, "INSERT")
        self._expect(KEYWORD, "INTO")
        table = self._expect(IDENT).text
        self._expect(KEYWORD, "VALUES")
        self._expect(LPAREN)
        vals = [self._parse_literal()]
        while self._match(COMMA):
            vals.append(self._parse_literal())
        self._expect(RPAREN)
        return ast.Insert(table, vals)

    def _parse_literal(self):
        t = self._peek()
        if t.kind == NUMBER:
            self._advance()
            return int(t.text)
        if t.kind == STRING:
            self._advance()
            return t.text
        raise EngineError("syntax",
            f"expected literal, got {t.text!r}",
            f"line {t.line}, column {t.col}")

    # --- query ---
    def _parse_query(self):
        blocks = [self._parse_select_block()]
        setops = []
        while True:
            t = self._peek()
            if t.kind == KEYWORD and t.text == "UNION":
                self._advance()
                if self._match(KEYWORD, "ALL"):
                    setops.append("union_all")
                else:
                    setops.append("union")
            elif t.kind == KEYWORD and t.text == "INTERSECT":
                self._advance()
                setops.append("intersect")
            elif t.kind == KEYWORD and t.text == "EXCEPT":
                self._advance()
                setops.append("except")
            else:
                break
            blocks.append(self._parse_select_block())
        return ast.Query(blocks, setops)

    def _parse_select_block(self):
        if self._peek().kind == LPAREN:
            self._advance()
            inner = self._parse_query()
            self._expect(RPAREN)
            return inner

        self._expect(KEYWORD, "SELECT")
        b = ast.SelectBlock()

        if self._match(KEYWORD, "DISTINCT"):
            b.distinct = True

        b.select_list = self._parse_select_list()

        self._expect(KEYWORD, "FROM")
        b.from_list = self._parse_from_list()

        if self._match(KEYWORD, "WHERE"):
            b.where = self._parse_condition()

        if self._at_keyword("GROUP"):
            self._advance()
            self._expect(KEYWORD, "BY")
            b.group_by = self._parse_column_list()
            if self._match(KEYWORD, "HAVING"):
                b.having = self._parse_condition()

        if self._match(KEYWORD, "ORDER"):
            self._expect(KEYWORD, "BY")
            b.order_by = self._parse_order_by()

        if self._match(KEYWORD, "LIMIT"):
            n = self._expect(NUMBER)
            b.limit = int(n.text)

        return b

    # --- select list ---
    def _parse_select_list(self):
        if self._match(STAR):
            return [ast.Star()]
        items = [self._parse_select_item()]
        while self._match(COMMA):
            items.append(self._parse_select_item())
        return items

    def _parse_select_item(self):
        expr = self._parse_select_expr()
        alias = None
        if self._match(KEYWORD, "AS"):
            alias = self._expect(IDENT).text
        return ast.SelectItem(expr, alias)

    def _parse_select_expr(self):
        t = self._peek()
        if t.kind == KEYWORD and t.text in ("COUNT", "SUM", "AVG", "MIN", "MAX"):
            kind = t.text.lower()
            self._advance()
            self._expect(LPAREN)
            if self._match(STAR):
                self._expect(RPAREN)
                if kind != "count":
                    raise EngineError("aggregate",
                        f"{kind.upper()}(*) is not valid",
                        f"line {t.line}, column {t.col}")
                return ast.Aggregate("count", "*")
            col = self._parse_column_ref()
            self._expect(RPAREN)
            return ast.Aggregate(kind, col)
        return self._parse_column_ref()

    def _parse_column_ref(self):
        t = self._expect(IDENT)
        if self._match(DOT):
            name = self._expect(IDENT)
            return ast.ColumnRef(t.text, name.text)
        return ast.ColumnRef(None, t.text)

    # --- from list ---
    def _parse_from_list(self):
        items = [self._parse_table_ref()]
        while self._match(COMMA):
            items.append(self._parse_table_ref())
        if len(items) == 1:
            left = items[0]
            while self._match(KEYWORD, "JOIN"):
                right = self._parse_table_ref()
                self._expect(KEYWORD, "ON")
                cond = self._parse_condition()
                left = ast.Join(left, right, cond)
            items = [left]
        return items

    def _parse_table_ref(self):
        t = self._peek()
        if t.kind == LPAREN:
            self._advance()
            q = self._parse_query()
            self._expect(RPAREN)
            self._expect(KEYWORD, "AS")
            alias = self._expect(IDENT).text
            return ast.SubqueryRef(q, alias)
        name = self._expect(IDENT).text
        alias = None
        if self._match(KEYWORD, "AS"):
            alias = self._expect(IDENT).text
        else:
            # Bare alias: FROM Emp e   (allowed; see file header).
            nt = self._peek()
            if nt.kind == IDENT:
                alias = self._advance().text
        return ast.TableRef(name, alias)

    # --- group by / order by ---
    def _parse_column_list(self):
        cols = [self._parse_column_ref()]
        while self._match(COMMA):
            cols.append(self._parse_column_ref())
        return cols

    def _parse_order_by(self):
        out = []
        while True:
            col = self._parse_column_ref()
            direction = "asc"
            if self._at_keyword("ASC"):
                self._advance()
                direction = "asc"
            elif self._at_keyword("DESC"):
                self._advance()
                direction = "desc"
            out.append((col, direction))
            if not self._match(COMMA):
                break
        return out

    # --- condition (delegates to _CondParser) ---
    def _parse_condition(self):
        p = _CondParser(self.toks, self.i)
        cond = p.parse()
        self.i = p.i
        return cond


# ============================================================
# Public entry point
# ============================================================

def parse_sql(text):
    """Lex and parse a SQL string into a list of statement AST nodes."""
    tokens = SqlLexer(text).tokens()
    return SqlParser(tokens).parse_statements()