# exec/operator.py - the iterator interface every M2 operator implements.
# Contract (M2 section 1.1):
#   open()              prepare, acquire buffers, do any blocking work
#   next() -> tuple | END   produce the next output tuple
#   close()             release every buffer and temporary file
#   counters()          per-operator counters
#
# After close, every page the operator pinned must be unpinned and every
# temporary file it created must be deleted.

class _EndSentinel:
    """The single END-of-stream value. Use `is END`, not `== END`."""
    __slots__ = ()
    def __repr__(self): return "END"

END = _EndSentinel()


class Operator:
    """Base class. Every M2 operator subclasses this."""

    def __init__(self, name):
        self.name = name
        self.tuples_out = 0
        self.comparisons = 0
        self.page_reads = 0
        self.page_writes = 0

    def open(self):
        raise NotImplementedError(f"{type(self).__name__}.open")

    def next(self):
        raise NotImplementedError(f"{type(self).__name__}.next")

    def close(self):
        raise NotImplementedError(f"{type(self).__name__}.close")

    def counters(self):
        return {
            "name": self.name,
            "tuples_out": self.tuples_out,
            "comparisons": self.comparisons,
            "page_reads": self.page_reads,
            "page_writes": self.page_writes,
        }

    def _emit(self, tuple_):
        self.tuples_out += 1
        return tuple_


def run_to_completion(root):
    root.open()
    rows = []
    while True:
        t = root.next()
        if t is END:
            break
        rows.append(t)
    root.close()
    return rows


def collect_counters(root, out=None, depth=0):
    if out is None:
        out = []
    c = root.counters()
    c["depth"] = depth
    out.append(c)
    for child in getattr(root, "children", []):
        collect_counters(child, out, depth + 1)
    return out
