# tests/test_m2_joins.py - the three join algorithms.
from exec.operator import Operator, END, run_to_completion
from exec.joins import NestedLoopJoin, SortMergeJoin, HashJoin


class ListOperator(Operator):
    def __init__(self, tuples, schema, name="list"):
        super().__init__(name)
        self.tuples = tuples
        self._schema = schema
        self.i = 0
        self.children = []
    def open(self):
        self.i = 0
    def next(self):
        if self.i >= len(self.tuples):
            return END
        t = self.tuples[self.i]
        self.i += 1
        return self._emit(t)
    def close(self):
        pass
    def schema(self):
        return self._schema


LSCHEMA = [("R.b", "INT"), ("R.c", "VARCHAR")]
RSCHEMA = [("S.b", "INT"), ("S.d", "INT")]


def make_join_inputs(lrows, rrows):
    left = ListOperator(lrows, LSCHEMA)
    right = ListOperator(rrows, RSCHEMA)
    return left, right


def test_all_three_joins_same_result():
    lrows = [(1, "x"), (2, "y"), (3, "z"), (2, "w")]
    rrows = [(1, 100), (2, 200), (2, 201), (4, 400)]
    condition = "R.b = S.b"
    results = {}
    for name, cls in [("bnl", NestedLoopJoin), ("smj", SortMergeJoin), ("hj", HashJoin)]:
        left, right = make_join_inputs(lrows, rrows)
        j = cls(condition, left, right, frames=4)
        results[name] = sorted(run_to_completion(j))
    assert results["bnl"] == results["smj"] == results["hj"]


def test_sort_merge_duplicate_keys_section_2_8():
    """M2 section 2.8 worked example: R.b=[1,2,2,3], S.b=[2,2,4] => 4 output rows."""
    lrows = [(1, ""), (2, ""), (2, ""), (3, "")]
    rrows = [(2, 0), (2, 0), (4, 0)]
    left, right = make_join_inputs(lrows, rrows)
    j = SortMergeJoin("R.b = S.b", left, right, frames=4)
    rows = run_to_completion(j)
    assert len(rows) == 4


def test_hash_join_duplicate_keys():
    lrows = [(2, "a"), (2, "b"), (3, "c")]
    rrows = [(2, 10), (2, 20)]
    left, right = make_join_inputs(lrows, rrows)
    j = HashJoin("R.b = S.b", left, right, frames=4)
    rows = run_to_completion(j)
    assert len(rows) == 4


def test_bnl_duplicate_keys():
    lrows = [(2, "a"), (2, "b")]
    rrows = [(2, 10), (2, 20)]
    left, right = make_join_inputs(lrows, rrows)
    j = NestedLoopJoin("R.b = S.b", left, right, frames=4)
    rows = run_to_completion(j)
    assert len(rows) == 4


def test_bnl_exact_comparisons():
    """M2 section 2.7: comparisons = |R| * |S| exactly."""
    lrows = [(i, "x") for i in range(10)]
    rrows = [(i, 0) for i in range(7)]
    left, right = make_join_inputs(lrows, rrows)
    j = NestedLoopJoin("R.b = S.b", left, right, frames=4)
    run_to_completion(j)
    assert j.counters()["comparisons"] == 10 * 7


def test_bnl_accepts_inequality():
    lrows = [(1, "x"), (5, "y"), (10, "z")]
    rrows = [(3, 0)]
    left, right = make_join_inputs(lrows, rrows)
    j = NestedLoopJoin("R.b > S.b", left, right, frames=4)
    rows = run_to_completion(j)
    assert len(rows) == 2


def test_smj_rejects_inequality():
    from engine.errors import EngineError
    left, right = make_join_inputs([(1, "x")], [(1, 0)])
    j = SortMergeJoin("R.b > S.b", left, right, frames=4)
    try:
        j.open()
        assert False
    except EngineError as e:
        assert "equality" in str(e)


def test_hj_rejects_inequality():
    from engine.errors import EngineError
    left, right = make_join_inputs([(1, "x")], [(1, 0)])
    j = HashJoin("R.b > S.b", left, right, frames=4)
    try:
        j.open()
        assert False
    except EngineError as e:
        assert "equality" in str(e)


def test_join_schema_concat():
    left, right = make_join_inputs([(1, "x")], [(1, 0)])
    j = NestedLoopJoin("R.b = S.b", left, right, frames=4)
    j.open()
    assert j.schema() == [("R.b", "INT"), ("R.c", "VARCHAR"),
                          ("S.b", "INT"), ("S.d", "INT")]
    j.close()


def test_join_schema_conflict_errors():
    from engine.errors import EngineError
    lschema = [("R.b", "INT")]
    rschema = [("R.b", "INT")]
    left = ListOperator([(1,)], lschema)
    right = ListOperator([(1,)], rschema)
    j = NestedLoopJoin("R.b = R.b", left, right, frames=4)
    try:
        j.open()
        assert False
    except EngineError as e:
        assert "duplicate" in str(e)


def test_all_joins_empty_left():
    for cls in (NestedLoopJoin, SortMergeJoin, HashJoin):
        left = ListOperator([], LSCHEMA)
        right = ListOperator([(1, 100)], RSCHEMA)
        j = cls("R.b = S.b", left, right, frames=4)
        rows = run_to_completion(j)
        assert rows == []


def test_all_joins_empty_right():
    for cls in (NestedLoopJoin, SortMergeJoin, HashJoin):
        left = ListOperator([(1, "x")], LSCHEMA)
        right = ListOperator([], RSCHEMA)
        j = cls("R.b = S.b", left, right, frames=4)
        rows = run_to_completion(j)
        assert rows == []
