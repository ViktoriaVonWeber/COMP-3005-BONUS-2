# tests/test_m3_lexer.py - SQL lexer.
from sql.lexer import (
    SqlLexer, KEYWORD, IDENT, NUMBER, STRING, OP,
    LPAREN, RPAREN, COMMA, DOT, SEMI, STAR, EOF,
)
from engine.errors import EngineError


def kinds(tokens):
    return [t.kind for t in tokens if t.kind != EOF]


def texts(tokens):
    return [t.text for t in tokens if t.kind != EOF]


def test_keywords_case_insensitive():
    toks = SqlLexer("SELECT select SeLeCt").tokens()
    assert texts(toks) == ["SELECT", "SELECT", "SELECT"]
    assert all(t.kind == KEYWORD for t in toks if t.kind != EOF)


def test_identifiers_case_sensitive():
    toks = SqlLexer("Emp emp EMP").tokens()
    assert texts(toks) == ["Emp", "emp", "EMP"]
    assert all(t.kind == IDENT for t in toks if t.kind != EOF)


def test_string_with_escaped_quote():
    toks = SqlLexer("'O''Brien'").tokens()
    assert toks[0].kind == STRING
    assert toks[0].text == "O'Brien"


def test_string_with_comma_and_paren():
    toks = SqlLexer("'a,b)'").tokens()
    assert toks[0].kind == STRING
    assert toks[0].text == "a,b)"


def test_integer_positive_and_negative():
    toks = SqlLexer("42 -7").tokens()
    assert texts(toks) == ["42", "-7"]
    assert all(t.kind == NUMBER for t in toks if t.kind != EOF)


def test_multi_char_operators_maximal_munch():
    toks = SqlLexer("<= >= <> < > =").tokens()
    ops = [t.text for t in toks if t.kind == OP]
    assert ops == ["<=", ">=", "<>", "<", ">", "="]


def test_comment_stripped():
    toks = SqlLexer("SELECT 1 -- this is a comment\nFROM t").tokens()
    assert "SELECT" in texts(toks)
    assert "FROM" in texts(toks)
    # no comment token leaked
    assert "this" not in texts(toks)


def test_punctuation():
    toks = SqlLexer("( ) , . ; *").tokens()
    ks = kinds(toks)
    assert LPAREN in ks and RPAREN in ks and COMMA in ks
    assert DOT in ks and SEMI in ks and STAR in ks


def test_positions_recorded():
    toks = SqlLexer("SELECT\n  x\nFROM t").tokens()
    # SELECT at line 1 col 1
    sel = toks[0]
    assert sel.line == 1 and sel.col == 1
    # x at line 2 col 3
    x = [t for t in toks if t.text == "x"][0]
    assert x.line == 2 and x.col == 3
    # FROM at line 3 col 1
    frm = [t for t in toks if t.text == "FROM"][0]
    assert frm.line == 3 and frm.col == 1


def test_unterminated_string_error():
    try:
        SqlLexer("SELECT 'abc").tokens()
        assert False, "should have raised"
    except EngineError as e:
        assert e.category == "lexical"
        assert "unterminated" in str(e)


def test_unexpected_char_error():
    try:
        SqlLexer("SELECT @").tokens()
        assert False, "should have raised"
    except EngineError as e:
        assert e.category == "lexical"
        assert "@" in str(e)


def test_multiple_statements():
    toks = SqlLexer("SELECT 1; SELECT 2;").tokens()
    semis = [t for t in toks if t.kind == SEMI]
    assert len(semis) == 2


def test_qualified_name():
    toks = SqlLexer("Emp.did").tokens()
    assert kinds(toks) == [IDENT, DOT, IDENT]
    assert texts(toks) == ["Emp", ".", "did"]


def test_select_star():
    toks = SqlLexer("SELECT * FROM t").tokens()
    assert STAR in kinds(toks)
