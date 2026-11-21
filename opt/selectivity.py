# opt/selectivity.py - selectivity formulas. M4 section 2.
#
# Selectivity is the fraction of input tuples a predicate keeps, in [0, 1].
# These are the required default formulas. Some are worth improving
# (see DESIGN_M4.md for what we improved and why).
#
# Every formula takes the input cardinality (number of tuples coming in)
# and returns the expected number of output tuples, at least 1, rounded up.

import math

from engine.errors import EngineError
from opt.catalog_ext import get_stats, default_stats


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------

def selectivity(stats, colname, predicate):
    """Return a selectivity in [0, 1] for a single-column predicate.
    predicate is a tuple:
      ("=", value)         equality
      ("<", value)         strict less-than
      ("<=", value)
      (">", value)
      (">=", value)
      ("<>", value)        not equal
    """
    op, value = predicate
    if op == "=":
        return _sel_eq(stats, colname, value)
    if op in ("<", "<=", ">", ">="):
        return _sel_range(stats, colname, op, value)
    if op == "<>":
        return 1.0 - _sel_eq(stats, colname, value)
    raise EngineError("schema", f"unknown predicate op {op!r}")


def selectivity_join(stats_left, col_left, n_left,
                     stats_right, col_right, n_right):
    """Selectivity of R.a = S.b. M4 section 2:
         output rows = n(R) * n(S) / max(ndv(R.a), ndv(S.b))
       Returns a selectivity in [0, 1] relative to n(R) * n(S).
    """
    ndv_l = _ndv(stats_left, col_left)
    ndv_r = _ndv(stats_right, col_right)
    denom = max(ndv_l, ndv_r, 1)
    return 1.0 / denom


def selectivity_and(s1, s2):
    """p AND q. Independence assumption (M4 section 2), which the report
    will later break on purpose."""
    return s1 * s2


def selectivity_or(s1, s2):
    """p OR q."""
    return s1 + s2 - s1 * s2


def selectivity_not(s):
    """NOT p."""
    return 1.0 - s


def selectivity_default():
    """Any other join predicate: 0.1 by default. M4 section 2 requires
    this to be stated in the design document rather than hidden."""
    return 0.1


# ------------------------------------------------------------
# Cardinality helpers
# ------------------------------------------------------------

def out_rows(n_in, sel):
    """Output cardinality of a node: n_in * sel, rounded up, never below 1."""
    return max(1, math.ceil(n_in * sel))


def out_pages(rows, width):
    """Output pages: ceil(rows * width / 4096)."""
    return max(1, math.ceil(rows * width / 4096))


# ------------------------------------------------------------
# Equality: histogram-based when available
# ------------------------------------------------------------

def _sel_eq(stats, colname, value):
    col = _column(stats, colname)
    hist = col["hist"]
    k = hist.get("k", 0)
    buckets = hist.get("buckets", [])
    n_tuples = stats.get("n_tuples", 0)

    # M4 formula: find the bucket holding v. Selectivity is
    #   count(bucket) / (n_tuples * ndv(bucket)).
    # With no histogram: 1 / ndv(c).
    if k == 0 or not buckets or n_tuples <= 0:
        ndv = col["ndv"] or 1
        return 1.0 / ndv

    for b in buckets:
        if b["lo"] <= value <= b["hi"]:
            if b["ndv"] <= 0 or n_tuples <= 0:
                return 0.0
            return b["count"] / (n_tuples * b["ndv"])
    # Value outside every bucket's [lo, hi]. Return 0 (it's not present).
    # In practice value will fall between buckets if it's a rare value
    # outside the observed min/max, so 0 is defensible.
    return 0.0


# ------------------------------------------------------------
# Range: sum whole buckets on the correct side, plus a linear fraction
# of the bucket containing v.
# ------------------------------------------------------------

def _sel_range(stats, colname, op, value):
    col = _column(stats, colname)
    hist = col["hist"]
    k = hist.get("k", 0)
    buckets = hist.get("buckets", [])
    n_tuples = stats.get("n_tuples", 0)

    if k == 0 or not buckets or n_tuples <= 0:
        # No histogram: fall back to a uniform assumption over [min, max].
        return _sel_range_uniform(col, op, value)

    total = 0
    for b in buckets:
        lo, hi, count = b["lo"], b["hi"], b["count"]
        if _bucket_fully_on_side(lo, hi, op, value):
            total += count
        elif _bucket_partially_in_range(lo, hi, value):
            # Linear fraction of the bucket.
            frac = _linear_fraction(lo, hi, op, value)
            total += count * frac
    return total / n_tuples


def _bucket_fully_on_side(lo, hi, op, value):
    if op == "<":
        return hi < value
    if op == "<=":
        return hi <= value
    if op == ">":
        return lo > value
    if op == ">=":
        return lo >= value
    return False


def _bucket_partially_in_range(lo, hi, value):
    return lo <= value <= hi


def _linear_fraction(lo, hi, op, value):
    """Fraction of a bucket [lo, hi] that satisfies the predicate,
    assuming uniform distribution within the bucket."""
    if hi == lo:
        # Single-value bucket. Either it satisfies or it doesn't.
        return 1.0 if _value_satisfies(lo, op, value) else 0.0
    if op == "<":
        return max(0.0, (value - lo) / (hi - lo))
    if op == "<=":
        return min(1.0, (value - lo + 1) / (hi - lo + 1))
    if op == ">":
        return max(0.0, (hi - value) / (hi - lo))
    if op == ">=":
        return min(1.0, (hi - value + 1) / (hi - lo + 1))
    return 0.0


def _value_satisfies(v, op, value):
    if op == "<":
        return v < value
    if op == "<=":
        return v <= value
    if op == ">":
        return v > value
    if op == ">=":
        return v >= value
    return False


def _sel_range_uniform(col, op, value):
    lo, hi = col["min"], col["max"]
    if lo is None or hi is None:
        return 0.5
    if hi == lo:
        return 1.0 if _value_satisfies(lo, op, value) else 0.0
    if op == "<":
        return max(0.0, min(1.0, (value - lo) / (hi - lo)))
    if op == "<=":
        return max(0.0, min(1.0, (value - lo) / (hi - lo)))
    if op == ">":
        return max(0.0, min(1.0, (hi - value) / (hi - lo)))
    if op == ">=":
        return max(0.0, min(1.0, (hi - value) / (hi - lo)))
    return 0.5


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def _column(stats, colname):
    cols = stats.get("columns", {})
    if colname not in cols:
        raise EngineError("name", f"no statistics for column {colname}")
    return cols[colname]


def _ndv(stats, colname):
    col = _column(stats, colname)
    return col.get("ndv") or 1