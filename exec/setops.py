# exec/setops.py - union, intersect, except. M2 section 2.10.
from exec.operator import Operator, END
from engine.errors import EngineError


class _SetOpBase(Operator):
    def __init__(self, name, left, right):
        super().__init__(name)
        self.left = left
        self.right = right
        self.children = [left, right]
        self._out_schema = None

    def schema(self):
        return self._out_schema

    def _build_schema(self):
        l = self.left.schema()
        r = self.right.schema()
        if len(l) != len(r):
            raise EngineError("schema",
                f"{self.name}: left has {len(l)} columns, right has {len(r)}")
        for (_, lt), (_, rt) in zip(l, r):
            if lt != rt:
                raise EngineError("schema",
                    f"{self.name}: type mismatch {lt} vs {rt}")
        self._out_schema = l

    def open(self):
        self.left.open()
        self.right.open()
        self._build_schema()

    def close(self):
        self.left.close()
        self.right.close()

    def _drain(self, op):
        rows = []
        while True:
            t = op.next()
            if t is END:
                return rows
            rows.append(t)


class Union(_SetOpBase):
    def __init__(self, left, right):
        super().__init__("union", left, right)
        self._iter = None

    def open(self):
        super().open()
        self._iter = self._run()

    def next(self):
        try:
            t = next(self._iter)
        except StopIteration:
            return END
        return self._emit(t)

    def _run(self):
        seen = set()
        for t in self._drain(self.left):
            if t not in seen:
                seen.add(t)
                yield t
        for t in self._drain(self.right):
            if t not in seen:
                seen.add(t)
                yield t


class Intersect(_SetOpBase):
    def __init__(self, left, right):
        super().__init__("intersect", left, right)
        self._iter = None

    def open(self):
        super().open()
        self._iter = self._run()

    def next(self):
        try:
            t = next(self._iter)
        except StopIteration:
            return END
        return self._emit(t)

    def _run(self):
        left_set = set(self._drain(self.left))
        right_set = set(self._drain(self.right))
        for t in left_set & right_set:
            yield t


class Except(_SetOpBase):
    def __init__(self, left, right):
        super().__init__("except", left, right)
        self._iter = None

    def open(self):
        super().open()
        self._iter = self._run()

    def next(self):
        try:
            t = next(self._iter)
        except StopIteration:
            return END
        return self._emit(t)

    def _run(self):
        left_set = set(self._drain(self.left))
        right_set = set(self._drain(self.right))
        for t in left_set - right_set:
            yield t
