# tests/test_m2_grouping.py - distinct and group_by.
from exec.operator import Operator, END, run_to_completion
from exec.grouping import Distinct, GroupBy


class ListOperator(Operator):
    def __init__(self, tuples, schema):
        super().__init__("list")
        self.tuples = tuples
        self._schema = schema
        self.i = 0
        self.children = []
    def open(self): self.i = 0
    def next(self):
        if self.i >= len(self.tuples): return END
        t = self.tuples[self.i]; self.i += 1
        return self._emit(t)
    def close(self): pass
    def schema(self): return self._schema


SCHEMA = [("t.a", "INT"), ("t.b", "VARCHAR"), ("t.c", "INT")]


# ---- distinct ----

def test_distinct_removes_duplicates_sort_mode():
    src = ListOperator([(1, "x", 10), (2, "y", 20), (1, "x", 10)], SCHEMA)
    d = Distinct("sort", src)
    rows = run_to_completion(d)
    assert len(rows) == 2
    assert set(rows) == {(1, "x", 10), (2, "y", 20)}


def test_distinct_removes_duplicates_hash_mode():
    src = ListOperator([(1, "x", 10), (2, "y", 20), (1, "x", 10)], SCHEMA)
    d = Distinct("hash", src)
    rows = run_to_completion(d)
    assert len(rows) == 2


def test_distinct_empty_input():
    src = ListOperator([], SCHEMA)
    d = Distinct("sort", src)
    rows = run_to_completion(d)
    assert rows == []


def test_distinct_schema_passes_through():
    src = ListOperator([(1, "x", 10)], SCHEMA)
    d = Distinct("sort", src)
    d.open()
    assert d.schema() == SCHEMA
    d.close()


# ---- group_by: count_star ----

def test_group_by_count_star():
    data = [
        (1, "a", 10), (1, "a", 20), (1, "a", 30),  # group 1, 3 rows
        (2, "b", 40), (2, "b", 50),                # group 2, 2 rows
    ]
    src = ListOperator(data, SCHEMA)
    g = GroupBy("hash", ["t.a"], [("count", "*")], src)
    rows = run_to_completion(g)
    assert g.schema() == [("t.a", "INT"), ("count_star", "INT")]
    rows_sorted = sorted(rows)
    assert rows_sorted == [(1, 3), (2, 2)]


# ---- group_by: count(col) ----

def test_group_by_count_column():
    data = [
        (1, "a", 10), (1, "a", 20),
        (2, "b", 30),
    ]
    src = ListOperator(data, SCHEMA)
    g = GroupBy("hash", ["t.a"], [("count", "t.c")], src)
    rows = run_to_completion(g)
    rows_sorted = sorted(rows)
    assert rows_sorted == [(1, 2), (2, 1)]


# ---- group_by: sum, avg, min, max ----

def test_group_by_sum():
    data = [(1, "a", 10), (1, "a", 20), (1, "a", 30)]
    src = ListOperator(data, SCHEMA)
    g = GroupBy("hash", ["t.a"], [("sum", "t.c")], src)
    rows = run_to_completion(g)
    assert rows == [(1, 60)]


def test_group_by_avg():
    data = [(1, "a", 10), (1, "a", 20), (1, "a", 30)]
    src = ListOperator(data, SCHEMA)
    g = GroupBy("hash", ["t.a"], [("avg", "t.c")], src)
    rows = run_to_completion(g)
    # 60 / 3 = 20.0
    assert rows == [(1, 20.0)]


def test_group_by_min_max():
    data = [(1, "a", 10), (1, "a", 40), (1, "a", 25)]
    src = ListOperator(data, SCHEMA)
    g = GroupBy("hash", ["t.a"], [("min", "t.c"), ("max", "t.c")], src)
    rows = run_to_completion(g)
    # output schema: t.a, min_t_c, max_t_c
    assert rows == [(1, 10, 40)]


# ---- group_by: no grouping keys ----

def test_group_by_no_keys_single_row():
    """M2 section 2.6: no grouping keys => one output row."""
    data = [(1, "a", 10), (2, "b", 20), (3, "c", 30)]
    src = ListOperator(data, SCHEMA)
    g = GroupBy("hash", [], [("count", "*"), ("sum", "t.c")], src)
    rows = run_to_completion(g)
    assert rows == [(3, 60)]


# ---- group_by: empty input ----

def test_group_by_empty_input_no_keys():
    """M2 section 2.6: empty + no keys => 1 row with COUNT 0."""
    src = ListOperator([], SCHEMA)
    g = GroupBy("hash", [], [("count", "*")], src)
    rows = run_to_completion(g)
    assert rows == [(0,)]


def test_group_by_empty_input_with_keys():
    """M2 section 2.6: empty + keys => 0 rows."""
    src = ListOperator([], SCHEMA)
    g = GroupBy("hash", ["t.a"], [("count", "*")], src)
    rows = run_to_completion(g)
    assert rows == []


def test_group_by_empty_input_no_keys_non_count_errors():
    """M2 section 2.6: non-count aggregates over empty input with no keys error."""
    from engine.errors import EngineError
    src = ListOperator([], SCHEMA)
    g = GroupBy("hash", [], [("sum", "t.c")], src)
    try:
        run_to_completion(g)
        assert False, "should have raised"
    except EngineError as e:
        assert "empty input" in str(e)


# ---- group_by: type rules ----

def test_group_by_sum_requires_int():
    from engine.errors import EngineError
    src = ListOperator([(1, "a", 10)], SCHEMA)
    g = GroupBy("hash", ["t.a"], [("sum", "t.b")], src)
    try:
        g.open()
        assert False
    except EngineError as e:
        assert "requires INT" in str(e)
