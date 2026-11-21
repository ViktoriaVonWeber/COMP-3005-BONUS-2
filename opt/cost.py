# opt/cost.py - cost model. M4 section 4.
#
# Cost is page I/O: reads plus writes. CPU is ignored.
# M and N are estimated page counts of the inputs; B is the frame budget.
#
# These formulas MUST match M2 section 2 exactly, because REPORT_M4
# compares predicted cost against measured I/O. Where M2 does not
# implement a formula (joins materialise in memory, reporting 0 reads),
# REPORT_M4 says so explicitly and points at the gap.

import math

from engine.errors import EngineError


# ------------------------------------------------------------
# Base operators
# ------------------------------------------------------------

def seqscan(n_pages):
    """Sequential scan of a table with n_pages data pages."""
    return n_pages


def filter_cost(_child_cost):
    """filter, project: pipelined, no I/O of their own."""
    return 0


def project_cost(_child_cost):
    return 0


def limit_cost(_child_cost):
    return 0


def sort_cost(n_pages, B):
    """External merge sort. M4 section 4:
         2 * M * (passes - 1) + M
       passes = 1 + ceil( log(ceil(M/B)) / log(B-1) )
       Returns 0 if the input already fits in B frames (a single
       in-memory sort, no I/O beyond reading the input once).
    """
    if n_pages <= 0:
        return 0
    if n_pages <= B:
        # Fits in memory: one pass, no spills. Cost is just reading
        # the input, which the operator above already paid for.
        return 0
    passes = 1 + math.ceil(
        math.log(math.ceil(n_pages / B)) / math.log(B - 1)
    )
    return 2 * n_pages * (passes - 1) + n_pages


def distinct_cost(n_pages, B, mode="sort"):
    """distinct: cost of the sort or hash partitioning it uses."""
    if mode == "sort":
        return sort_cost(n_pages, B)
    # hash: partitioning + probing, same shape as hash_join
    return 2 * n_pages


def group_by_cost(n_pages, B, mode="sort"):
    """group_by: cost of the sort or hash partitioning it uses."""
    if mode == "sort":
        return sort_cost(n_pages, B)
    return 2 * n_pages


# ------------------------------------------------------------
# Joins
# ------------------------------------------------------------

def nested_loop_join(m_pages, n_pages, B):
    """Block nested loop. M4 section 4:
         M + ceil(M / (B-2)) * N
    """
    if B < 3:
        raise EngineError("schema",
            f"nested_loop_join: frames={B} too small; need B >= 3")
    return m_pages + math.ceil(m_pages / (B - 2)) * n_pages


def sort_merge_join(m_pages, n_pages, B):
    """Sort-merge join. M4 section 4:
         sort(M) + sort(N) + M + N
    """
    return sort_cost(m_pages, B) + sort_cost(n_pages, B) + m_pages + n_pages


def hash_join(m_pages, n_pages, B):
    """Grace hash join. M4 section 4:
         3 * (M + N)
    """
    return 3 * (m_pages + n_pages)


# ------------------------------------------------------------
# Set operators
# ------------------------------------------------------------

def union_all_cost(_left_pages, _right_pages):
    """union_all: pipelined, no I/O of its own."""
    return 0


def union_cost(left_pages, right_pages, B):
    """union: concatenation followed by duplicate removal.
    Cost is the dedup (sort or hash) on the combined input."""
    return distinct_cost(left_pages + right_pages, B, mode="sort")


def intersect_cost(left_pages, right_pages, B):
    """intersect: blocking, sorts both inputs."""
    return sort_cost(left_pages, B) + sort_cost(right_pages, B)


def except_cost(left_pages, right_pages, B):
    """except: blocking, sorts both inputs."""
    return sort_cost(left_pages, B) + sort_cost(right_pages, B)


# ------------------------------------------------------------
# Convenience: cost of one node given its inputs
# ------------------------------------------------------------

def node_cost(op, input_pages, B, left_pages=None, right_pages=None):
    """Dispatch by operator name. `input_pages` is the single input for
    unary operators; `left_pages`/`right_pages` are used for binary ones.
    """
    if op in ("seqscan",):
        return input_pages
    if op in ("filter", "project", "limit"):
        return 0
    if op == "sort":
        return sort_cost(input_pages, B)
    if op == "distinct":
        return distinct_cost(input_pages, B)
    if op == "group_by":
        return group_by_cost(input_pages, B)
    if op == "nested_loop_join":
        return nested_loop_join(left_pages, right_pages, B)
    if op == "sort_merge_join":
        return sort_merge_join(left_pages, right_pages, B)
    if op == "hash_join":
        return hash_join(left_pages, right_pages, B)
    if op == "union_all":
        return 0
    if op == "union":
        return union_cost(left_pages, right_pages, B)
    if op == "intersect":
        return intersect_cost(left_pages, right_pages, B)
    if op == "except":
        return except_cost(left_pages, right_pages, B)
    raise EngineError("schema", f"cost: unknown operator {op!r}")