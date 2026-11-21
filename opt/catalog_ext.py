# opt/catalog_ext.py - read/write statistics in the catalog.
#
# M1's catalog page format reserves the tail of one 4 KB page for a
# JSON-encoded statistics blob. A full equi-depth histogram (k=20) per
# column overflows that budget for tables with many columns, so we store
# a compact form and reconstruct on read. The compact form is also
# shrunk to fit a fixed byte budget (STATS_BUDGET) before writing.

import json

MAX_CUTS = 20
STATS_BUDGET = 2600  # bytes; leaves ~1500 for name, schema, and metadata


def get_stats(catalog, table):
    entry = catalog.get_table(table)
    stats = entry.get("statistics") or {}
    if not stats or "columns" not in stats:
        return None
    return _expand(stats)


def put_stats(catalog, table, stats):
    compact = _compact(stats)
    _shrink_to_fit(compact, budget=STATS_BUDGET)
    catalog.update_table(table, statistics=compact)


def has_stats(catalog, table):
    return get_stats(catalog, table) is not None


def _compact(stats):
    out = {
        "analyzed_at": stats.get("analyzed_at"),
        "n_tuples": stats.get("n_tuples", 0),
        "n_pages": stats.get("n_pages", 0),
        "width": stats.get("width", 1),
        "columns": {},
    }
    for colname, col in stats.get("columns", {}).items():
        hist = col.get("hist", {})
        buckets = hist.get("buckets", [])
        cuts = []
        cum = 0
        for b in buckets[:MAX_CUTS]:
            cum += b.get("count", 0)
            cuts.append([_jsonable(b.get("hi")), cum])
        out["columns"][colname] = {
            "ndv": col.get("ndv", 0),
            "min": _jsonable(col.get("min")),
            "max": _jsonable(col.get("max")),
            "cuts": cuts,
        }
    return out


def _expand(stats):
    out = {
        "analyzed_at": stats.get("analyzed_at"),
        "n_tuples": stats.get("n_tuples", 0),
        "n_pages": stats.get("n_pages", 0),
        "width": stats.get("width", 1),
        "columns": {},
    }
    for colname, col in stats.get("columns", {}).items():
        cuts = col.get("cuts", [])
        ndv = col.get("ndv", 1) or 1
        total = cuts[-1][1] if cuts else 0
        buckets = []
        prev_hi = col.get("min")
        prev_cum = 0
        for (hi, cum) in cuts:
            count = cum - prev_cum
            bucket_ndv = max(1, round(ndv * count / total)) if total > 0 else 1
            buckets.append({
                "lo": prev_hi,
                "hi": hi,
                "count": count,
                "ndv": bucket_ndv,
            })
            prev_hi = hi
            prev_cum = cum
        out["columns"][colname] = {
            "ndv": ndv,
            "min": col.get("min"),
            "max": col.get("max"),
            "hist": {"k": len(buckets), "buckets": buckets},
        }
    return out


def _shrink_to_fit(compact, budget):
    while len(json.dumps(compact).encode("utf-8")) > budget:
        shrunk = False
        for col in compact["columns"].values():
            cuts = col.get("cuts", [])
            if len(cuts) > 2:
                last = cuts[-1]
                new = cuts[::2]
                if new[-1] is not last:
                    new.append(last)
                col["cuts"] = new
                shrunk = True
        if not shrunk:
            for col in compact["columns"].values():
                col["cuts"] = []
            break


def default_stats(catalog, table):
    entry = catalog.get_table(table)
    schema = entry["schema"]
    n_tuples = entry.get("num_tuples", 0) or 0
    n_pages = entry.get("num_pages", 0) or 0
    if n_pages == 0:
        n_pages = max(1, (n_tuples * 50 + 4095) // 4096)
    columns = {}
    for (colname, ctype, clen) in schema:
        if ctype == "INT":
            lo, hi = 0, 1000
        else:
            lo, hi = "", "zzzz"
        ndv = max(1, int(n_tuples ** 0.5)) if n_tuples > 0 else 1
        columns[colname] = {
            "ndv": ndv, "min": lo, "max": hi,
            "hist": {"k": 0, "buckets": []},
        }
    return {
        "analyzed_at": None,
        "n_tuples": n_tuples,
        "n_pages": n_pages,
        "width": sum((4 if ct == "INT" else 2 + (cl or 20))
                     for (_cn, ct, cl) in schema),
        "columns": columns,
        "defaults": True,
    }


def _jsonable(v):
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    return str(v)