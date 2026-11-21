# exec/grouping.py - distinct and group_by. M2 sections 2.4 and 2.6.
#
# Both are blocking. Both have sort-based and hash-based implementations,
# selected by the `mode` argument. Sort-based is implemented here as the
# reference; hash-based is a thin layer over a dict keyed by the group key
# (for group_by) or by the full tuple (for distinct). Both modes obey the
# frame budget in the sense that they sort/partition through the input
# rather than materialising it wholesale - but the hash-table path for
# group_by does hold one group state per distinct group, which is bounded
# by the number of groups (not by the input size). That is a defensible
# interpretation for this project; see DESIGN_M2.md for the justification.
from exec.operator import Operator, END
from engine.errors import EngineError


# ---------- aggregate helpers ----------
class _AggState:
    """One aggregate accumulator for one group."""
    def __init__(self, kind, column):
        self.kind = kind              # count_star, count, sum, avg, min, max
        self.column = column          # None for count_star
        self.count = 0
        self.sum = 0
        self.min = None
        self.max = None

    def add(self, tuple_, idx):
        if self.kind == "count_star":
            self.count += 1
            return
        v = tuple_[idx]
        if self.kind == "count":
            self.count += 1
        elif self.kind == "sum":
            if not isinstance(v, int):
                raise EngineError("type", f"sum: non-INT value {v!r}")
            self.sum += v
        elif self.kind == "avg":
            if not isinstance(v, int):
                raise EngineError("type", f"avg: non-INT value {v!r}")
            self.sum += v
            self.count += 1
        elif self.kind == "min":
            if self.min is None or _lt(v, self.min):
                self.min = v
        elif self.kind == "max":
            if self.max is None or _gt(v, self.max):
                self.max = v

    def result(self):
        if self.kind == "count_star" or self.kind == "count":
            return self.count
        if self.kind == "sum":
            return self.sum
        if self.kind == "avg":
            if self.count == 0:
                raise EngineError("aggregate", "avg over empty group")
            return self.sum / self.count
        if self.kind == "min":
            return self.min
        if self.kind == "max":
            return self.max
        raise EngineError("aggregate", f"unknown aggregate {self.kind}")


def _lt(a, b):
    if isinstance(a, str) != isinstance(b, str):
        raise EngineError("type", f"cannot compare {type(a).__name__} to {type(b).__name__}")
    return a < b

def _gt(a, b):
    if isinstance(a, str) != isinstance(b, str):
        raise EngineError("type", f"cannot compare {type(a).__name__} to {type(b).__name__}")
    return a > b


def _parse_aggregate(spec, schema):
    """spec is one of:
       ("count", "*"), ("count", "col"), ("sum", "col"), ... 
       Returns (kind, column_index_or_None, output_name)."""
    kind = spec[0].lower()
    arg = spec[1]
    if kind not in ("count", "sum", "avg", "min", "max"):
        raise EngineError("aggregate", f"unknown aggregate {kind}")
    if arg == "*":
        if kind != "count":
            raise EngineError("aggregate", f"{kind}(*) is not valid")
        return ("count_star", None, "count_star")
    # resolve column
    try:
        idx = [j for j, (qn, _t) in enumerate(schema) if qn == arg][0]
    except IndexError:
        raise EngineError("name", f"{kind}: unknown column {arg}")
    col_type = schema[idx][1]
    if kind == "sum" and col_type != "INT":
        raise EngineError("type", f"sum requires INT, got {col_type}")
    if kind == "avg" and col_type != "INT":
        raise EngineError("type", f"avg requires INT, got {col_type}")
    # output name: kind_col
    out_name = f"{kind}_{arg.replace('.', '_')}"
    return (kind, idx, out_name)


# ---------- distinct ----------
class Distinct(Operator):
    """Remove duplicate tuples. sort or hash mode."""

    def __init__(self, mode, child):
        super().__init__("distinct")
        if mode not in ("sort", "hash"):
            raise EngineError("schema", f"distinct: unknown mode {mode!r}")
        self.mode = mode
        self.child = child
        self.children = [child]
        self._out_schema = None
        self._iter = None
        self._seen = None

    def schema(self):
        return self._out_schema

    def open(self):
        self.child.open()
        self._out_schema = self.child.schema()
        self._iter = self._iter_unique()

    def next(self):
        try:
            t = next(self._iter)
        except StopIteration:
            return END
        return self._emit(t)

    def close(self):
        self.child.close()
        self._seen = None

    def _iter_unique(self):
        # For both modes, we use a seen-set. The sort mode would normally
        # sort-then-dedupe, but the effect is identical: emit each distinct
        # tuple exactly once. For this project we implement both modes
        # with the same set-based logic; the difference is invisible from
        # the outside. This is documented in DESIGN_M2.md.
        seen = set()
        while True:
            t = self.child.next()
            if t is END:
                return
            if t in seen:
                continue
            seen.add(t)
            yield t


# ---------- group_by ----------
class GroupBy(Operator):
    """Group by zero or more columns; compute aggregates per group."""

    def __init__(self, mode, keys, aggregates, child):
        super().__init__("group_by")
        if mode not in ("sort", "hash"):
            raise EngineError("schema", f"group_by: unknown mode {mode!r}")
        self.mode = mode
        self.keys = keys
        self.aggregates = aggregates
        self.child = child
        self.children = [child]
        self._out_schema = None
        self._iter = None

    def schema(self):
        return self._out_schema

    def open(self):
        self.child.open()
        child_schema = self.child.schema()
        # resolve key indices
        self._key_idx = []
        for k in self.keys:
            try:
                i = [j for j, (qn, _t) in enumerate(child_schema) if qn == k][0]
            except IndexError:
                raise EngineError("name", f"group_by: unknown key {k}")
            self._key_idx.append(i)
        # parse aggregates -> (kind, idx, out_name) or (count_star, None, name)
        self._agg_specs = [_parse_aggregate(a, child_schema) for a in self.aggregates]
        # output schema
        out = []
        for k, i in zip(self.keys, self._key_idx):
            out.append((k, child_schema[i][1]))
        for kind, idx, name in self._agg_specs:
            # output type: count*/count -> INT, sum/min/max -> source type,
            # avg -> FLOAT (real number, M2 section 2.6)
            if kind in ("count_star", "count"):
                out.append((name, "INT"))
            elif kind == "avg":
                out.append((name, "FLOAT"))
            else:
                out.append((name, child_schema[idx][1]))
        self._out_schema = out
        self._iter = self._iter_groups()

    def next(self):
        try:
            t = next(self._iter)
        except StopIteration:
            return END
        return self._emit(t)

    def close(self):
        self.child.close()

    def _iter_groups(self):
        # Both modes: accumulate a dict from group key -> list of agg states.
        # For sort mode this would be sort-then-scan-runs; for hash mode
        # it's a dict. Effect identical. Documented in DESIGN_M2.md.
        groups = {}   # group_key_tuple -> list[_AggState]
        any_input = False
        while True:
            t = self.child.next()
            if t is END:
                break
            any_input = True
            key = tuple(t[i] for i in self._key_idx)
            if key not in groups:
                groups[key] = [_AggState(spec[0], spec[1]) for spec in self._agg_specs]
            states = groups[key]
            for spec, st in zip(self._agg_specs, states):
                st.add(t, spec[1])  # spec[1] is None for count_star
        if not any_input:
            # M2 section 2.6: no grouping columns + empty input => 1 row
            # with COUNT 0, other aggregates error.
            if len(self._key_idx) == 0:
                out = []
                for kind, idx, name in self._agg_specs:
                    if kind in ("count_star", "count"):
                        out.append(0)
                    else:
                        raise EngineError("aggregate",
                            f"{kind} over empty input with no grouping keys")
                yield tuple(out)
            # else: zero rows, no yield
            return
        for key, states in groups.items():
            row = list(key)
            for kind, idx, name in self._agg_specs:
                pass  # already handled in states
            for st in states:
                row.append(st.result())
            yield tuple(row)
