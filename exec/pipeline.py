# exec/pipeline.py - project, limit, union_all. M2 sections 2.3 and 2.10.
#
# project:  keep listed columns in listed order. Does NOT dedupe.
# limit:    return first n tuples, then stop pulling from child.
# union_all: concatenate left then right. No deduplication.
from exec.operator import Operator, END
from engine.errors import EngineError


class Project(Operator):
    """Keep listed columns. Bag semantics: duplicates are not removed."""

    def __init__(self, columns, child):
        super().__init__("project")
        self.columns = columns
        self.child = child
        self.children = [child]
        self._idx = None
        self._out_schema = None

    def schema(self):
        return self._out_schema

    def open(self):
        self.child.open()
        child_schema = self.child.schema()
        self._idx = []
        for c in self.columns:
            try:
                i = [j for j, (qn, _t) in enumerate(child_schema) if qn == c][0]
            except IndexError:
                raise EngineError("name", f"project: unknown column {c}")
            self._idx.append(i)
        self._out_schema = [(c, child_schema[i][1])
                            for c, i in zip(self.columns, self._idx)]

    def next(self):
        t = self.child.next()
        if t is END:
            return END
        return self._emit(tuple(t[i] for i in self._idx))

    def close(self):
        self.child.close()


class Limit(Operator):
    """Return first n tuples, then stop pulling from child."""

    def __init__(self, n, child):
        super().__init__("limit")
        self.n = n
        self.child = child
        self.children = [child]
        self._count = 0
        self._done = False

    def schema(self):
        return self.child.schema()

    def open(self):
        self._count = 0
        self._done = False
        self.child.open()

    def next(self):
        if self._done or self._count >= self.n:
            self._done = True
            return END
        t = self.child.next()
        if t is END:
            self._done = True
            return END
        self._count += 1
        return self._emit(t)

    def close(self):
        self.child.close()


class UnionAll(Operator):
    """Concatenate left then right. No deduplication."""

    def __init__(self, left, right):
        super().__init__("union_all")
        self.left = left
        self.right = right
        self.children = [left, right]
        self._stage = 0
        self._out_schema = None

    def schema(self):
        return self._out_schema

    def open(self):
        self.left.open()
        self.right.open()
        l = self.left.schema()
        r = self.right.schema()
        # M2 section 2.10: both sides must have the same number of columns
        # with the same types. Column names may differ; output takes the
        # left input's names.
        if len(l) != len(r):
            raise EngineError("schema",
                f"union_all: left has {len(l)} columns, right has {len(r)}")
        for (_, lt), (_, rt) in zip(l, r):
            if lt != rt:
                raise EngineError("schema",
                    f"union_all: type mismatch {lt} vs {rt}")
        self._out_schema = l
        self._stage = 0

    def next(self):
        if self._stage == 0:
            t = self.left.next()
            if t is not END:
                return self._emit(t)
            self._stage = 1
        t = self.right.next()
        if t is END:
            return END
        return self._emit(t)

    def close(self):
        self.left.close()
        self.right.close()
