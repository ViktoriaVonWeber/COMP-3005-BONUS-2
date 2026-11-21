from engine.errors import EngineError

IDENT, NUMBER, STRING, OP, LPAREN, RPAREN, LBRACK, RBRACK, LBRACE, RBRACE, COMMA, DOT, EOF = (
    "IDENT","NUMBER","STRING","OP","LPAREN","RPAREN","LBRACK","RBRACK",
    "LBRACE","RBRACE","COMMA","DOT","EOF",
)
MULTI_OPS = ["!=", "<>", "<=", ">="]
SINGLE_OPS = set("=<>")

class Token:
    __slots__ = ("kind","text","pos")
    def __init__(self, kind, text, pos):
        self.kind, self.text, self.pos = kind, text, pos
    def __repr__(self):
        return f"Token({self.kind!r},{self.text!r},{self.pos})"

class Lexer:
    def __init__(self, src):
        self.src = src
        self.pos = 0
    def tokens(self):
        out = []; s = self.src
        while self.pos < len(s):
            c = s[self.pos]
            if c in " \t\r\n":
                self.pos += 1; continue
            if c == "/" and self.pos + 1 < len(s) and s[self.pos+1] == "/":
                while self.pos < len(s) and s[self.pos] != "\n":
                    self.pos += 1
                continue
            if c.isalpha() or c == "_":
                start = self.pos
                while self.pos < len(s) and (s[self.pos].isalnum() or s[self.pos] == "_"):
                    self.pos += 1
                out.append(Token(IDENT, s[start:self.pos], start)); continue
            if c.isdigit() or (c == "-" and self.pos+1 < len(s) and s[self.pos+1].isdigit()):
                start = self.pos
                if c == "-": self.pos += 1
                while self.pos < len(s) and s[self.pos].isdigit():
                    self.pos += 1
                out.append(Token(NUMBER, s[start:self.pos], start)); continue
            if c == "'":
                start = self.pos; self.pos += 1; buf = []
                while True:
                    if self.pos >= len(s):
                        raise EngineError("lexical", "unterminated string", start)
                    ch = s[self.pos]
                    if ch == "'":
                        if self.pos+1 < len(s) and s[self.pos+1] == "'":
                            buf.append("'"); self.pos += 2; continue
                        self.pos += 1; break
                    buf.append(ch); self.pos += 1
                out.append(Token(STRING, "".join(buf), start)); continue
            matched = False
            for op in MULTI_OPS:
                if s.startswith(op, self.pos):
                    out.append(Token(OP, op, self.pos)); self.pos += len(op)
                    matched = True; break
            if matched: continue
            if c in SINGLE_OPS:
                out.append(Token(OP, c, self.pos)); self.pos += 1; continue
            simple = {"(":LPAREN,")":RPAREN,"[":LBRACK,"]":RBRACK,
                      "{":LBRACE,"}":RBRACE,",":COMMA,".":DOT}
            if c in simple:
                out.append(Token(simple[c], c, self.pos)); self.pos += 1; continue
            raise EngineError("lexical", f"unexpected character {c!r}", self.pos)
        out.append(Token(EOF, "", self.pos))
        return out