# tests/test_m2_operator.py - iterator contract.
from exec.operator import Operator, END, run_to_completion, collect_counters


class CountOperator(Operator):
    """Toy operator: emits N tuples (0,), (1,), ..., (N-1,)."""
    def __init__(self, n):
        super().__init__("count")
        self.n = n
        self.i = 0
        self.children = []

    def open(self):
        self.i = 0

    def next(self):
        if self.i >= self.n:
            return END
        t = (self.i,)
        self.i += 1
        return self._emit(t)

    def close(self):
        pass


def test_end_is_singleton():
    assert END is END
    assert repr(END) == "END"


def test_run_to_completion():
    op = CountOperator(5)
    rows = run_to_completion(op)
    assert rows == [(0,), (1,), (2,), (3,), (4,)]
    assert op.counters()["tuples_out"] == 5


def test_collect_counters_flat():
    root = CountOperator(3)
    run_to_completion(root)
    cs = collect_counters(root)
    assert len(cs) == 1
    assert cs[0]["name"] == "count"
    assert cs[0]["tuples_out"] == 3
    assert cs[0]["depth"] == 0


def test_collect_counters_nested():
    class Passthrough(Operator):
        def __init__(self, child):
            super().__init__("pass")
            self.children = [child]
        def open(self):
            self.children[0].open()
        def next(self):
            t = self.children[0].next()
            if t is END:
                return END
            return self._emit(t)
        def close(self):
            self.children[0].close()

    root = Passthrough(CountOperator(4))
    rows = run_to_completion(root)
    assert rows == [(0,), (1,), (2,), (3,)]
    cs = collect_counters(root)
    assert len(cs) == 2
    assert cs[0]["name"] == "pass" and cs[0]["depth"] == 0
    assert cs[1]["name"] == "count" and cs[1]["depth"] == 1
    assert cs[0]["tuples_out"] == 4
    assert cs[1]["tuples_out"] == 4


def test_base_operator_raises_on_open():
    op = Operator("abstract")
    try:
        op.open()
        assert False
    except NotImplementedError:
        pass

