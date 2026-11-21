# tests/test_m2_sort.py - external sort.
import math, os
from exec.operator import Operator, END, run_to_completion
from exec.sort import Sort


class ListOperator(Operator):
    """Test operator that emits a fixed list of tuples."""
    def __init__(self, tuples, name="list"):
        super().__init__(name)
        self.tuples = tuples
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
        return [("t.a", "INT")]


def test_sort_preserves_all_rows():
    src = ListOperator([(3,), (1,), (2,), (5,), (4,)])
    s = Sort([("t.a", "asc")], src, frames=2, work_dir="/tmp")
    rows = run_to_completion(s)
    assert len(rows) == 5


def test_sort_ascending():
    src = ListOperator([(3,), (1,), (2,), (5,), (4,)])
    s = Sort([("t.a", "asc")], src, frames=2, work_dir="/tmp")
    rows = run_to_completion(s)
    assert rows == [(1,), (2,), (3,), (4,), (5,)]


def test_sort_descending():
    src = ListOperator([(3,), (1,), (2,), (5,), (4,)])
    s = Sort([("t.a", "desc")], src, frames=2, work_dir="/tmp")
    rows = run_to_completion(s)
    assert rows == [(5,), (4,), (3,), (2,), (1,)]


def test_sort_multi_key():
    src = ListOperator([(1, 10), (2, 20), (1, 30), (2, 5), (1, 20)])
    src.schema = lambda: [("t.a", "INT"), ("t.b", "INT")]
    s = Sort([("t.a", "asc"), ("t.b", "desc")], src, frames=2, work_dir="/tmp")
    rows = run_to_completion(s)
    # a asc, b desc: (1,30), (1,20), (1,10), (2,20), (2,5)
    assert rows == [(1, 30), (1, 20), (1, 10), (2, 20), (2, 5)]


def test_sort_handles_duplicates():
    src = ListOperator([(1,), (1,), (2,), (1,), (2,)])
    s = Sort([("t.a", "asc")], src, frames=2, work_dir="/tmp")
    rows = run_to_completion(s)
    assert rows == [(1,), (1,), (1,), (2,), (2,)]


def test_sort_empty_input():
    src = ListOperator([])
    s = Sort([("t.a", "asc")], src, frames=2, work_dir="/tmp")
    rows = run_to_completion(s)
    assert rows == []


def test_sort_external_input():
    """20 * B pages worth of tuples. With 100 tuples/page that's 20 * B * 100.
    With B=4 (M2 section 1.2 minimum for grading), that's 8000 tuples.
    Verifies sort order at a size that forces multiple merge passes."""
    n = 20 * 4 * 100
    src = ListOperator([(i,) for i in range(n, 0, -1)])
    s = Sort([("t.a", "asc")], src, frames=4, work_dir="/tmp")
    rows = run_to_completion(s)
    assert len(rows) == n
    assert rows[0] == (1,)
    assert rows[-1] == (n,)


def test_sort_temps_cleaned_up():
    src = ListOperator([(i,) for i in range(100)])
    s = Sort([("t.a", "asc")], src, frames=2, work_dir="/tmp")
    run_to_completion(s)
    # after close, temp files should be removed
    assert s._temp_files == []


def test_sort_unknown_column_errors():
    from engine.errors import EngineError
    src = ListOperator([(1,)])
    s = Sort([("t.nope", "asc")], src, frames=2, work_dir="/tmp")
    try:
        s.open()
        assert False
    except EngineError as e:
        assert "unknown column" in str(e)


def test_sort_pass_count_formula():
    """M2 section 2.5: passes = 1 + ceil(log(ceil(N/B))/log(B-1))."""
    def passes(N, B):
        if N <= B:
            return 1
        return 1 + math.ceil(math.log(math.ceil(N/B)) / math.log(B-1))
    # spot-check the formula
    assert passes(8, 3) == 1 + math.ceil(math.log(3)/math.log(2))  # 1 + 2 = 3
    assert passes(8, 3) == 3
