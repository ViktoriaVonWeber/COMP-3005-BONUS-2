# tests/test_m3_required.py - the 15 required cases from M3 section 7.
#
# Each test builds a small catalog, parses SQL, resolves, translates,
# and checks the shape of the logical plan or the error category.
# No execution: M4 turns these plans physical later.

import os
import shutil
import tempfile
import pytest

from storage.catalog import Catalog
from sql.parser import parse_sql
from sql.resolver import resolve
from sql.translator import translate
from engine.errors import EngineError


@pytest.fixture
def db():
    d = tempfile.mkdtemp()
    cat = Catalog(d)
    cat.create_table("Emp", [
        ("eid", "INT", None),
        ("name", "VARCHAR", 20),
        ("did", "INT", None),
        ("mgr", "INT", None),
        ("age", "INT", None),
        ("salary", "INT", None),
    ])
    cat.create_table("Dept", [
        ("did", "INT", None),
        ("title", "VARCHAR", 20),
        ("budget", "INT", None),
        ("city", "VARCHAR", 20),
    ])
    yield d
    shutil.rmtree(d)


def _compile(db_path, sql):
    cat = Catalog(db_path)
    stmts = parse_sql(sql)
    out = []
    for s in stmts:
        resolve(s, cat)
        out.append(translate(s))
    return out


def _compile_one(db_path, sql):
    return _compile(db_path, sql)[0]


def test_01_self_join(db):
    plan = _compile_one(db,
        "SELECT e.name, m.name FROM Emp e JOIN Emp m ON e.mgr = m.eid")
    assert plan["op"] == "project"
    assert plan["child"]["op"] == "join"
    join = plan["child"]
    assert join["left"]["op"] == "rename"
    assert join["left"]["to"] == "e"
    assert join["left"]["child"]["op"] == "scan"
    assert join["left"]["child"]["table"] == "Emp"
    assert join["right"]["op"] == "rename"
    assert join["right"]["to"] == "m"


def test_02_star_over_three_tables_shared_column(db):
    cat = Catalog(db)
    cat.create_table("People", [("name", "VARCHAR", 20), ("role", "VARCHAR", 20)])
    plan = _compile_one(db, "SELECT * FROM Emp e, Dept d, People p")
    assert plan["op"] == "project"
    cols = plan["columns"]
    assert "e.name" in cols
    assert "d.did" in cols
    assert "p.name" in cols
    for c in cols:
        assert "." in c


def test_03_ambiguous_unqualified_column(db):
    with pytest.raises(EngineError) as ei:
        _compile_one(db, "SELECT did FROM Emp, Dept")
    assert ei.value.category == "ambiguity"
    msg = str(ei.value)
    assert "Emp.did" in msg and "Dept.did" in msg


def test_04_union_vs_union_all(db):
    p_union = _compile_one(db,
        "SELECT city FROM Dept UNION SELECT city FROM Dept")
    p_union_all = _compile_one(db,
        "SELECT city FROM Dept UNION ALL SELECT city FROM Dept")
    assert p_union["op"] == "union"
    assert p_union_all["op"] == "union_all"


def test_05_where_and_having_opposite_sides(db):
    plan = _compile_one(db,
        "SELECT did, COUNT(*) FROM Emp "
        "WHERE age > 25 GROUP BY did HAVING COUNT(*) > 5")
    assert plan["op"] == "project"
    having = plan["child"]
    assert having["op"] == "select"
    assert "count_star" in having["condition"]
    gb = having["child"]
    assert gb["op"] == "group_by"
    where = gb["child"]
    assert where["op"] == "select"
    assert "age" in where["condition"]
    assert where["child"]["op"] == "scan"


def test_06_non_grouped_column(db):
    with pytest.raises(EngineError) as ei:
        _compile_one(db, "SELECT did, name, COUNT(*) FROM Emp GROUP BY did")
    assert ei.value.category == "aggregate"


def test_07_aggregate_in_where(db):
    with pytest.raises(EngineError) as ei:
        _compile_one(db, "SELECT did FROM Emp WHERE COUNT(*) > 5")
    assert ei.value.category == "aggregate"


def test_08_subquery_in_from(db):
    plan = _compile_one(db,
        "SELECT t.n, Dept.title FROM "
        "(SELECT did, COUNT(*) AS n FROM Emp GROUP BY did) AS t "
        "JOIN Dept ON t.did = Dept.did")
    assert plan["op"] == "project"
    join = plan["child"]
    assert join["op"] == "join"
    assert join["left"]["op"] == "rename"
    assert join["left"]["to"] == "t"
    inner = join["left"]["child"]
    assert inner["op"] == "project"
    assert inner["child"]["op"] == "group_by"


def test_09_order_by_non_selected_column(db):
    plan = _compile_one(db, "SELECT name FROM Emp ORDER BY age DESC")
    assert plan["op"] == "sort"
    assert plan["keys"][0][0] == "Emp.age"
    assert plan["keys"][0][1] == "desc"
    assert plan["child"]["op"] == "project"


def test_10_type_error_int_vs_string(db):
    with pytest.raises(EngineError) as ei:
        _compile_one(db, "SELECT name FROM Emp WHERE age > '30'")
    assert ei.value.category == "type"


def test_11_unknown_table_vs_unknown_column(db):
    with pytest.raises(EngineError) as ei_t:
        _compile_one(db, "SELECT x FROM NoSuchTable")
    assert ei_t.value.category == "name"
    assert "NoSuchTable" in str(ei_t.value)

    with pytest.raises(EngineError) as ei_c:
        _compile_one(db, "SELECT no_such_col FROM Emp")
    assert ei_c.value.category == "name"
    assert "no_such_col" in str(ei_c.value)


def test_12_duplicate_alias(db):
    with pytest.raises(EngineError) as ei:
        _compile_one(db, "SELECT * FROM Emp, Emp")
    assert ei.value.category == "name"
    assert "duplicate" in str(ei.value).lower()


def test_13_count_star_no_group_by_plan_shape(db):
    plan = _compile_one(db, "SELECT COUNT(*) FROM Emp")
    assert plan["op"] == "project"
    gb = plan["child"]
    assert gb["op"] == "group_by"
    assert gb["keys"] == []
    assert gb["aggregates"] == [["count", "*"]]


def test_14_union_type_mismatch(db):
    with pytest.raises(EngineError) as ei:
        _compile_one(db, "SELECT did FROM Emp UNION SELECT title FROM Dept")
    assert ei.value.category == "schema"
    assert "type mismatch" in str(ei.value)


def test_15_four_table_comma_vs_join(db):
    cat = Catalog(db)
    cat.create_table("A", [("x", "INT", None), ("y", "INT", None)])
    cat.create_table("B", [("x", "INT", None), ("z", "INT", None)])

    p_comma = _compile_one(db,
        "SELECT * FROM Emp e, Dept d, A a, B b "
        "WHERE e.did = d.did AND d.did = a.x AND a.x = b.x")
    p_join = _compile_one(db,
        "SELECT e.name FROM Emp e "
        "JOIN Dept d ON e.did = d.did "
        "JOIN A a ON d.did = a.x "
        "JOIN B b ON a.x = b.x")
    assert p_comma["op"] == "project"
    assert p_join["op"] == "project"
    assert p_comma["child"]["op"] == "select"
    assert p_join["child"]["op"] == "join"