# tests/test_m2_setops.py - union, intersect, except.
from exec.operator import Operator, END, run_to_completion
from exec.setops import Union, Intersect, Except


class ListOperator(Operator):
    def __init__(self, tuples, schema):
        super().__init__("list")
        self.tuples = tuples
        self._schema = schema
        self.i = 0
        self.children = []
    def open(self): self.i = 0
    def next(self):
        if self.i >= len(self.tuples):
            return END
        t = self.tuples[self.i]
        self.i += 1
        return self._emit(t)
    def close(self): pass
    def schema(self): return self._schema


SCHEMA = [("t.a", "INT"), ("t.b", "VARCHAR")]


def test_union_deduplicates():
    l = ListOperator([(1, "x"), (2, "y")], SCHEMA)
    r = ListOperator([(2, "y"), (3, "z")], SCHEMA)
    u = Union(l, r)
    rows = run_to_completion(u)
    assert sorted(rows) == [(1, "x"), (2, "y"), (3, "z")]


def test_union_empty_left():
    l = ListOperator([], SCHEMA)
    r = ListOperator([(1, "x")], SCHEMA)
    u = Union(l, r)
    rows = run_to_completion(u)
    assert rows == [(1, "x")]


def test_union_empty_both():
    l = ListOperator([], SCHEMA)
    r = ListOperator([], SCHEMA)
    u = Union(l, r)
    rows = run_to_completion(u)
    assert rows == []


def test_intersect_returns_common():
    l = ListOperator([(1, "x"), (2, "y"), (3, "z")], SCHEMA)
    r = ListOperator([(2, "y"), (3, "z"), (4, "w")], SCHEMA)
    i = Intersect(l, r)
    rows = run_to_completion(i)
    assert sorted(rows) == [(2, "y"), (3, "z")]


def test_intersect_empty():
    l = ListOperator([(1, "x")], SCHEMA)
    r = ListOperator([], SCHEMA)
    i = Intersect(l, r)
    rows = run_to_completion(i)
    assert rows == []


def test_except_returns_left_minus_right():
    l = ListOperator([(1, "x"), (2, "y"), (3, "z")], SCHEMA)
    r = ListOperator([(2, "y")], SCHEMA)
    e = Except(l, r)
    rows = run_to_completion(e)
    assert sorted(rows) == [(1, "x"), (3, "z")]


def test_except_empty_right():
    l = ListOperator([(1, "x")], SCHEMA)
    r = ListOperator([], SCHEMA)
    e = Except(l, r)
    rows = run_to_completion(e)
    assert rows == [(1, "x")]


def test_schema_from_left():
    lschema = [("L.a", "INT"), ("L.b", "VARCHAR")]
    rschema = [("R.x", "INT"), ("R.y", "VARCHAR")]
    l = ListOperator([(1, "x")], lschema)
    r = ListOperator([(1, "x")], rschema)
    u = Union(l, r)
    u.open()
    assert u.schema() == lschema
    u.close()


def test_column_count_mismatch_errors():
    from engine.errors import EngineError
    l = ListOperator([(1,)], [("a", "INT")])
    r = ListOperator([(1, 2)], [("x", "INT"), ("y", "INT")])
    u = Union(l, r)
    try:
        u.open()
        assert False
    except EngineError as e:
        assert "left has 1 columns" in str(e)
        assert "right has 2" in str(e)


def test_type_mismatch_errors():
    from engine.errors import EngineError
    l = ListOperator([(1,)], [("a", "INT")])
    r = ListOperator([("hello",)], [("b", "VARCHAR")])
    u = Union(l, r)
    try:
        u.open()
        assert False
    except EngineError as e:
        assert "type mismatch" in str(e)


def test_union_handles_duplicates_within_each_side():
    l = ListOperator([(1, "x"), (1, "x")], SCHEMA)
    r = ListOperator([(1, "x"), (2, "y")], SCHEMA)
    u = Union(l, r)
    rows = run_to_completion(u)
    assert sorted(rows) == [(1, "x"), (2, "y")]