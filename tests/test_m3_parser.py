# tests/test_m3_parser.py - SQL parser.
from sql.parser import parse_sql
from sql import ast
from engine.errors import EngineError


def test_create_table():
    stmts = parse_sql("CREATE TABLE R (a INT, b VARCHAR(20))")
    assert len(stmts) == 1
    c = stmts[0]
    assert isinstance(c, ast.CreateTable)
    assert c.name == "R"
    assert c.columns == [("a", "INT", None), ("b", "VARCHAR", 20)]


def test_insert():
    stmts = parse_sql("INSERT INTO R VALUES (1, 'x')")
    i = stmts[0]
    assert isinstance(i, ast.Insert)
    assert i.table == "R"
    assert i.values == [1, "x"]


def test_select_star():
    stmts = parse_sql("SELECT * FROM R")
    q = stmts[0]
    assert isinstance(q, ast.Query)
    b = q.blocks[0]
    assert len(b.select_list) == 1
    assert isinstance(b.select_list[0], ast.Star)


def test_select_columns():
    stmts = parse_sql("SELECT a, b FROM R")
    b = stmts[0].blocks[0]
    assert len(b.select_list) == 2
    assert b.select_list[0].expr.name == "a"
    assert b.select_list[1].expr.name == "b"


def test_select_with_alias():
    stmts = parse_sql("SELECT a AS x FROM R")
    b = stmts[0].blocks[0]
    assert b.select_list[0].alias == "x"


def test_select_qualified():
    stmts = parse_sql("SELECT R.a FROM R")
    b = stmts[0].blocks[0]
    assert b.select_list[0].expr.qualifier == "R"
    assert b.select_list[0].expr.name == "a"


def test_aggregate_count_star():
    stmts = parse_sql("SELECT COUNT(*) FROM R")
    b = stmts[0].blocks[0]
    agg = b.select_list[0].expr
    assert isinstance(agg, ast.Aggregate)
    assert agg.kind == "count"
    assert agg.arg == "*"


def test_aggregate_sum():
    stmts = parse_sql("SELECT SUM(x) FROM R")
    agg = stmts[0].blocks[0].select_list[0].expr
    assert agg.kind == "sum"
    assert agg.arg.name == "x"


def test_where():
    stmts = parse_sql("SELECT a FROM R WHERE a > 10")
    b = stmts[0].blocks[0]
    assert b.where is not None


def test_group_by_having():
    stmts = parse_sql("SELECT a, COUNT(*) FROM R GROUP BY a HAVING COUNT(*) > 1")
    b = stmts[0].blocks[0]
    assert len(b.group_by) == 1
    assert b.having is not None


def test_order_limit():
    stmts = parse_sql("SELECT a FROM R ORDER BY a DESC LIMIT 10")
    b = stmts[0].blocks[0]
    assert len(b.order_by) == 1
    assert b.order_by[0][1] == "desc"
    assert b.limit == 10


def test_distinct():
    stmts = parse_sql("SELECT DISTINCT a FROM R")
    assert stmts[0].blocks[0].distinct is True


def test_join():
    stmts = parse_sql("SELECT e.a FROM Emp e JOIN Dept d ON e.did = d.did")
    b = stmts[0].blocks[0]
    assert len(b.from_list) == 1
    assert isinstance(b.from_list[0], ast.Join)


def test_union_all_vs_union():
    stmts = parse_sql("SELECT a FROM R UNION ALL SELECT a FROM S")
    q = stmts[0]
    assert q.setops == ["union_all"]
    stmts2 = parse_sql("SELECT a FROM R UNION SELECT a FROM S")
    assert stmts2[0].setops == ["union"]


def test_set_ops_left_assoc():
    stmts = parse_sql("SELECT a FROM R UNION SELECT a FROM S EXCEPT SELECT a FROM T")
    q = stmts[0]
    assert q.setops == ["union", "except"]
    assert len(q.blocks) == 3


def test_subquery_in_from():
    stmts = parse_sql("SELECT t.n FROM (SELECT COUNT(*) AS n FROM R) AS t")
    b = stmts[0].blocks[0]
    assert isinstance(b.from_list[0], ast.SubqueryRef)


def test_multiple_statements():
    stmts = parse_sql("CREATE TABLE R (a INT); INSERT INTO R VALUES (1); SELECT * FROM R")
    assert len(stmts) == 3


def test_syntax_error():
    try:
        parse_sql("SELECT")
        assert False
    except EngineError as e:
        assert e.category == "syntax"
