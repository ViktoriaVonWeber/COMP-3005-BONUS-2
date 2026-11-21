# sql/lexer.py - hand-written SQL lexer. M3 section 1.
# No regex, no parser generators.
from engine.errors import EngineError


# Token kinds
IDENT = "IDENT"
KEYWORD = "KEYWORD"
NUMBER = "NUMBER"
STRING = "STRING"
OP = "OP"
LPAREN = "LPAREN"
RPAREN = "RPAREN"
COMMA = "COMMA"
DOT = "DOT"
SEMI = "SEMI"
STAR = "STAR"
EOF = "EOF"

# Keywords (case-insensitive, we store the uppercase form on the token)
KEYWORDS = {
    "SELECT", "DISTINCT", "FROM", "WHERE", "GROUP", "BY", "HAVING",
    "ORDER", "ASC", "DESC", "LIMIT", "AS",
    "JOIN", "ON", "INNER", "LEFT", "RIGHT", "OUTER", "CROSS",
    "UNION", "ALL", "INTERSECT", "EXCEPT",
    "CREATE", "TABLE", "INSERT", "INTO", "VALUES",
    "AND", "OR", "NOT",
    "COUNT", "SUM", "AVG", "MIN", "MAX",
    "INT", "VARCHAR",
}

# Multi-char operators. Order matters: longest first for maximal munch.
MULTI_OPS = ["<=", ">=", "<>"]
SINGLE_OPS = set("=<>")


class Token:
    __slots__ = ("kind", "text", "pos", "line", "col")

    def __init__(self, kind, text, pos, line, col):
        self.kind = kind
        self.text = text
        self.pos = pos
        self.line = line
        self.col = col

    def __repr__(self):
        return f"Token({self.kind!r}, {self.text!r}, {self.line}:{self.col})"


class SqlLexer:
    def __init__(self, src):
        self.src = src
        self.pos = 0
        self.line = 1
        self.col = 1

    def _advance(self):
        c = self.src[self.pos]
        self.pos += 1
        if c == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return c

    def _peek(self, offset=0):
        i = self.pos + offset
        return self.src[i] if i < len(self.src) else ""

    def _err(self, msg):
        raise EngineError("lexical", msg, f"line {self.line}, column {self.col}")

    def tokens(self):
        out = []
        while self.pos < len(self.src):
            c = self._peek()
            # whitespace
            if c in " \t\r\n":
                self._advance()
                continue
            # -- line comment
            if c == "-" and self._peek(1) == "-":
                while self.pos < len(self.src) and self._peek() != "\n":
                    self._advance()
                continue
            # identifiers and keywords
            if c.isalpha() or c == "_":
                start_pos = self.pos
                start_line = self.line
                start_col = self.col
                buf = []
                while self.pos < len(self.src) and (self._peek().isalnum() or self._peek() == "_"):
                    buf.append(self._advance())
                text = "".join(buf)
                upper = text.upper()
                if upper in KEYWORDS:
                    out.append(Token(KEYWORD, upper, start_pos, start_line, start_col))
                else:
                    out.append(Token(IDENT, text, start_pos, start_line, start_col))
                continue
            # numbers: optionally signed integer
            if c.isdigit() or (c == "-" and self._peek(1).isdigit()):
                start_pos = self.pos
                start_line = self.line
                start_col = self.col
                buf = []
                if c == "-":
                    buf.append(self._advance())
                while self.pos < len(self.src) and self._peek().isdigit():
                    buf.append(self._advance())
                out.append(Token(NUMBER, "".join(buf), start_pos, start_line, start_col))
                continue
            # string literal
            if c == "'":
                start_pos = self.pos
                start_line = self.line
                start_col = self.col
                self._advance()  # opening quote
                buf = []
                while True:
                    if self.pos >= len(self.src):
                        raise EngineError("lexical", "unterminated string",
                                          f"line {start_line}, column {start_col}")
                    ch = self._advance()
                    if ch == "'":
                        if self._peek() == "'":
                            self._advance()
                            buf.append("'")
                            continue
                        break
                    buf.append(ch)
                out.append(Token(STRING, "".join(buf), start_pos, start_line, start_col))
                continue
            # multi-char operators
            matched = False
            for op in MULTI_OPS:
                if self.src.startswith(op, self.pos):
                    start_pos = self.pos
                    start_line = self.line
                    start_col = self.col
                    self._advance()
                    self._advance()
                    out.append(Token(OP, op, start_pos, start_line, start_col))
                    matched = True
                    break
            if matched:
                continue
            # single-char operators
            if c in SINGLE_OPS:
                start_pos = self.pos
                start_line = self.line
                start_col = self.col
                self._advance()
                out.append(Token(OP, c, start_pos, start_line, start_col))
                continue
            # punctuation
            if c == "(":
                out.append(Token(LPAREN, c, self.pos, self.line, self.col))
                self._advance(); continue
            if c == ")":
                out.append(Token(RPAREN, c, self.pos, self.line, self.col))
                self._advance(); continue
            if c == ",":
                out.append(Token(COMMA, c, self.pos, self.line, self.col))
                self._advance(); continue
            if c == ".":
                out.append(Token(DOT, c, self.pos, self.line, self.col))
                self._advance(); continue
            if c == ";":
                out.append(Token(SEMI, c, self.pos, self.line, self.col))
                self._advance(); continue
            if c == "*":
                out.append(Token(STAR, c, self.pos, self.line, self.col))
                self._advance(); continue
            self._err(f"unexpected character {c!r}")
        out.append(Token(EOF, "", self.pos, self.line, self.col))
        return out
