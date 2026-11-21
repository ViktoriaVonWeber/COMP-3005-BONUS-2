# exec/sort.py - external merge sort. M2 section 2.5.
import heapq, math, os, tempfile
from exec.operator import Operator, END
from engine.errors import EngineError


class Sort(Operator):
    def __init__(self, keys, child, frames, work_dir=None):
        super().__init__("sort")
        self.keys = keys
        self.child = child
        self.children = [child]
        self.frames = frames
        self.work_dir = work_dir or tempfile.gettempdir()
        self._runs = []
        self._out_iter = None
        self._out_schema = None
        self._key_idx = None
        self._ascending = None
        self._temp_files = []

    def schema(self):
        return self._out_schema

    def open(self):
        self.child.open()
        self._out_schema = self.child.schema()
        self._key_idx = []
        self._ascending = []
        for col, direction in self.keys:
            try:
                i = [j for j, (qn, _t) in enumerate(self._out_schema) if qn == col][0]
            except IndexError:
                raise EngineError("name", f"sort: unknown column {col}")
            self._key_idx.append(i)
            self._ascending.append(direction == "asc")

        self._runs = self._generate_runs()

        while len(self._runs) > 1:
            self._runs = self._merge_pass(self._runs)

        self._out_iter = self._read_run(self._runs[0])

    def next(self):
        try:
            t = next(self._out_iter)
        except StopIteration:
            return END
        return self._emit(t)

    def close(self):
        self.child.close()
        for path in self._temp_files:
            try:
                os.remove(path)
            except OSError:
                pass
        self._temp_files = []
        self._runs = []

    def _generate_runs(self):
        buffer_size = max(1, self.frames) * 100
        runs = []
        buf = []
        while True:
            t = self.child.next()
            if t is END:
                break
            buf.append(t)
            if len(buf) >= buffer_size:
                runs.append(self._write_run(self._sort_buf(buf)))
                buf = []
        if buf:
            runs.append(self._write_run(self._sort_buf(buf)))
        if not runs:
            runs.append(self._write_run([]))
        return runs

    def _sort_buf(self, buf):
        result = list(buf)
        for i in range(len(self._key_idx) - 1, -1, -1):
            idx = self._key_idx[i]
            asc = self._ascending[i]
            result.sort(key=lambda t: t[idx], reverse=not asc)
        return result

    def _merge_pass(self, run_paths):
        if self.frames < 3:
            raise EngineError("schema",
                f"sort: frames={self.frames} too small to merge {len(run_paths)} runs")
        fan_in = self.frames - 1
        fan_in = max(1, self.frames - 1)
        new_runs = []
        for i in range(0, len(run_paths), fan_in):
            batch = run_paths[i:i + fan_in]
            if len(batch) == 1:
                new_runs.append(batch[0])
                continue
            new_runs.append(self._merge_batch(batch))
        return new_runs

    def _merge_batch(self, run_paths):
        iters = [self._read_run(p) for p in run_paths]
        heap = []
        for i, it in enumerate(iters):
            try:
                t = next(it)
                heap.append((self._sort_key(t), i, t))
            except StopIteration:
                pass
        heapq.heapify(heap)
        path = self._new_temp_path()
        with open(path, "w") as f:
            while heap:
                _key, i, t = heapq.heappop(heap)
                f.write(_encode(t) + "\n")
                self.page_writes += 1
                try:
                    t2 = next(iters[i])
                    heapq.heappush(heap, (self._sort_key(t2), i, t2))
                except StopIteration:
                    pass
        return path

    def _sort_key(self, tup):
        out = []
        for idx, asc in zip(self._key_idx, self._ascending):
            v = tup[idx]
            if asc:
                out.append((0, v))
            else:
                if isinstance(v, (int, float)):
                    out.append((1, -v))
                else:
                    out.append((1, _ReverseStr(v)))
        return tuple(out)

    def _new_temp_path(self):
        fd, path = tempfile.mkstemp(prefix="sort_", dir=self.work_dir)
        os.close(fd)
        self._temp_files.append(path)
        return path

    def _write_run(self, tuples):
        path = self._new_temp_path()
        with open(path, "w") as f:
            for t in tuples:
                f.write(_encode(t) + "\n")
        return path

    def _read_run(self, path):
        with open(path, "r") as f:
            for line in f:
                yield _decode(line.rstrip("\n"))


SEP = "\x1f"


def _encode(tup):
    parts = []
    for v in tup:
        if isinstance(v, int):
            parts.append(f"i:{v}")
        elif isinstance(v, str):
            parts.append(f"s:{v}")
        elif isinstance(v, float):
            parts.append(f"f:{v}")
        else:
            parts.append(f"x:{v}")
    return SEP.join(parts)


def _decode(line):
    out = []
    if not line:
        return tuple()
    for p in line.split(SEP):
        tag, _, val = p.partition(":")
        if tag == "i":
            out.append(int(val))
        elif tag == "s":
            out.append(val)
        elif tag == "f":
            out.append(float(val))
        else:
            out.append(val)
    return tuple(out)


class _ReverseStr:
    __slots__ = ("v",)
    def __init__(self, v):
        self.v = v
    def __eq__(self, other):
        return self.v == other.v
    def __lt__(self, other):
        return self.v > other.v
    def __le__(self, other):
        return self.v >= other.v
    def __gt__(self, other):
        return self.v < other.v
    def __ge__(self, other):
        return self.v <= other.v