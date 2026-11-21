# exec/joins.py - the three join algorithms. M2 sections 2.7-2.9.
from exec.operator import Operator, END
from engine.condition import parse_condition, eval_condition
from engine.errors import EngineError
import math


class _JoinBase(Operator):
    def __init__(self, name, condition, left, right):
        super().__init__(name)
        self.condition = condition
        self.left = left
        self.right = right
        self.children = [left, right]
        self._parsed = None
        self._out_schema = None

    def schema(self):
        return self._out_schema

    def _build_schema(self):
        l = self.left.schema()
        r = self.right.schema()
        lnames = [qn for qn, _t in l]
        rnames = [qn for qn, _t in r]
        for qn in lnames:
            if qn in rnames:
                raise EngineError("schema",
                    f"{self.name}: duplicate qualified column {qn} in join output")
        self._out_schema = l + r

    def open(self):
        self.left.open()
        self.right.open()
        self._parsed = parse_condition(self.condition)
        self._build_schema()

    def close(self):
        self.left.close()
        self.right.close()


class NestedLoopJoin(_JoinBase):
    """Block nested loop. Outer is read in blocks of B-2 pages; the inner
    is drained once and iterated per outer tuple for each outer block.
    Every outer tuple is compared against every inner tuple, so the
    comparison count matches |R| * |S| exactly."""

    def __init__(self, condition, left, right, frames):
        super().__init__("nested_loop_join", condition, left, right)
        self.frames = frames
        if frames < 3:
            raise EngineError("schema",
                f"nested_loop_join: frames={frames} too small; need B >= 3")
        self.block_size = 100 * (frames - 2)
        self._inner_rows = None
        self._inner_cursor = 0
        self._outer = None
        self._outer_idx = 0
        self._initialized = False

    def open(self):
        super().open()
        self._inner_rows = None
        self._inner_cursor = 0
        self._outer = None
        self._outer_idx = 0
        self._initialized = False

    def next(self):
        # First call: drain the inner once. It is reused for every outer
        # tuple across every outer block. Do not re-read the child: it
        # has already been consumed.
        if not self._initialized:
            self._inner_rows = self._drain(self.right)
            self._initialized = True

        while True:
            # If no current outer block, take one from the left child.
            if self._outer is None:
                self._outer = self._take_block(self.left, self.block_size)
                if not self._outer:
                    return END
                self._outer_idx = 0

            # Iterate current outer tuple against the inner.
            while self._outer_idx < len(self._outer):
                outer_t = self._outer[self._outer_idx]
                while self._inner_cursor < len(self._inner_rows):
                    inner_t = self._inner_rows[self._inner_cursor]
                    self._inner_cursor += 1
                    self.comparisons += 1
                    combined = outer_t + inner_t
                    if eval_condition(self._parsed, combined, self._out_schema):
                        return self._emit(combined)
                # Inner exhausted for this outer tuple: reset cursor.
                self._inner_cursor = 0
                self._outer_idx += 1
            # Outer block exhausted: get next block (keeping the inner).
            self._outer = None

    def _drain(self, op):
        rows = []
        while True:
            t = op.next()
            if t is END:
                return rows
            rows.append(t)

    def _take_block(self, op, n):
        rows = []
        for _ in range(n):
            t = op.next()
            if t is END:
                break
            rows.append(t)
        return rows


class SortMergeJoin(_JoinBase):
    def __init__(self, condition, left, right, frames):
        super().__init__("sort_merge_join", condition, left, right)
        self.frames = frames
        self._lrows = None
        self._rrows = None
        self._li = 0
        self._ri = 0
        self._groups = None
        self._gi = (0, 0)

    def open(self):
        super().open()
        from engine.tree import Cmp
        c = self._parsed
        if not isinstance(c, Cmp) or c.op != "=":
            raise EngineError("schema",
                "sort_merge_join: only equality conditions are supported")
        left_attr = c.left if c.left["kind"] == "attr" else c.right
        right_attr = c.right if c.left["kind"] == "attr" else c.left
        if left_attr["kind"] != "attr" or right_attr["kind"] != "attr":
            raise EngineError("schema",
                "sort_merge_join: condition must compare two attributes")
        self._left_key_idx = _resolve_attr_in_schema(left_attr, self.left.schema())
        self._right_key_idx = _resolve_attr_in_schema(right_attr, self.right.schema())
        self._lrows = self._drain_sorted(self.left, self._left_key_idx)
        self._rrows = self._drain_sorted(self.right, self._right_key_idx)
        self._li = 0
        self._ri = 0
        self._groups = None
        self._gi = (0, 0)

    def _drain_sorted(self, op, key_idx):
        rows = []
        while True:
            t = op.next()
            if t is END:
                break
            rows.append(t)
        rows.sort(key=lambda r: r[key_idx])
        return rows

    def next(self):
        while True:
            if self._groups is None:
                while self._li < len(self._lrows) and self._ri < len(self._rrows):
                    lv = self._lrows[self._li][self._left_key_idx]
                    rv = self._rrows[self._ri][self._right_key_idx]
                    self.comparisons += 1
                    if lv < rv:
                        self._li += 1
                    elif lv > rv:
                        self._ri += 1
                    else:
                        lstart = self._li
                        while (self._li < len(self._lrows)
                               and self._lrows[self._li][self._left_key_idx] == lv):
                            self._li += 1
                        rstart = self._ri
                        while (self._ri < len(self._rrows)
                               and self._rrows[self._ri][self._right_key_idx] == rv):
                            self._ri += 1
                        self._groups = (lstart, self._li, rstart, self._ri)
                        self._gi = (lstart, rstart)
                        break
                else:
                    return END
            if self._groups is None:
                return END
            lstart, lend, rstart, rend = self._groups
            li, ri = self._gi
            if li < lend:
                combined = self._lrows[li] + self._rrows[ri]
                self._gi = (li, ri + 1)
                if self._gi[1] >= rend:
                    self._gi = (li + 1, rstart)
                return self._emit(combined)
            else:
                self._groups = None
                self._gi = (0, 0)


class HashJoin(_JoinBase):
    def __init__(self, condition, left, right, frames):
        super().__init__("hash_join", condition, left, right)
        self.frames = frames
        if frames < 3:
            raise EngineError("schema",
                f"hash_join: frames={frames} too small; need B >= 3")
        self._p = max(2, frames - 1)
        self._built = None
        self._probe_rows = []
        self._part_i = 0
        self._pi = 0
        self._current_r = None
        self._current_matches = []
        self._match_i = 0

    def open(self):
        super().open()
        from engine.tree import Cmp
        c = self._parsed
        if not isinstance(c, Cmp) or c.op != "=":
            raise EngineError("schema",
                "hash_join: only equality conditions are supported")
        left_attr = c.left if c.left["kind"] == "attr" else c.right
        right_attr = c.right if c.left["kind"] == "attr" else c.left
        if left_attr["kind"] != "attr" or right_attr["kind"] != "attr":
            raise EngineError("schema",
                "hash_join: condition must compare two attributes")
        self._left_key_idx = _resolve_attr_in_schema(left_attr, self.left.schema())
        self._right_key_idx = _resolve_attr_in_schema(right_attr, self.right.schema())
        self._lparts = self._partition(self.left, self._left_key_idx, self._p)
        self._rparts = self._partition(self.right, self._right_key_idx, self._p)
        self._load_partition(0)

    def _partition(self, op, key_idx, n):
        parts = [[] for _ in range(n)]
        while True:
            t = op.next()
            if t is END:
                break
            h = _h1(t[key_idx]) % n
            parts[h].append(t)
        return parts

    def _load_partition(self, i):
        self._part_i = i
        self._current_r = None
        self._current_matches = []
        self._match_i = 0
        if i >= self._p:
            self._built = None
            self._probe_rows = []
            return
        self._built = {}
        for t in self._lparts[i]:
            k = t[self._left_key_idx]
            self._built.setdefault(k, []).append(t)
        self._probe_rows = self._rparts[i]
        self._pi = 0

    def next(self):
        while self._part_i < self._p:
            while self._current_r is not None and self._match_i < len(self._current_matches):
                l = self._current_matches[self._match_i]
                self._match_i += 1
                self.comparisons += 1
                combined = l + self._current_r
                if eval_condition(self._parsed, combined, self._out_schema):
                    return self._emit(combined)
            self._current_r = None
            self._current_matches = []
            if self._pi < len(self._probe_rows):
                r = self._probe_rows[self._pi]
                self._pi += 1
                matches = self._built.get(r[self._right_key_idx])
                if matches:
                    self._current_r = r
                    self._current_matches = matches
                    self._match_i = 0
                continue
            self._load_partition(self._part_i + 1)
        return END


def _resolve_attr_in_schema(attr, schema):
    name = attr["name"]
    qual = attr.get("qual")
    if qual is not None:
        full = f"{qual}.{name}"
        for i, (qn, _t) in enumerate(schema):
            if qn == full:
                return i
        raise EngineError("name", f"unknown attribute {full}")
    matches = [i for i, (qn, _t) in enumerate(schema) if qn.split(".")[-1] == name]
    if len(matches) == 0:
        raise EngineError("name", f"unknown attribute {name}")
    if len(matches) > 1:
        raise EngineError("ambiguity", f"ambiguous attribute {name}")
    return matches[0]


def _h1(v):
    if isinstance(v, str):
        return hash(v) & 0x7FFFFFFF
    return abs(hash(v)) & 0x7FFFFFFF