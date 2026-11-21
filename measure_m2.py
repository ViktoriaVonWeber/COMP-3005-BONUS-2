# measure_m2.py - M2 section 5 measurements.
# Runs the four required experiments and writes results/m2_measurements.json.
import json, math, os, time
from exec.operator import Operator, END, run_to_completion
from exec.joins import NestedLoopJoin, SortMergeJoin, HashJoin
from exec.sort import Sort


class ListOperator(Operator):
    def __init__(self, tuples, schema):
        super().__init__("list")
        self.tuples = tuples
        self._schema = schema
        self.i = 0
        self.children = []
    def open(self):
        self.i = 0
    def next(self):
        if self.i >= len(self.tuples):
            return END
        t = self.tuples[self.i]
        self.i += 1
        return self._emit(t)
    def close(self):
        pass
    def schema(self):
        return self._schema


LSCHEMA = [("R.b", "INT"), ("R.pad", "INT")]
RSCHEMA = [("S.b", "INT"), ("S.pad", "INT")]


def make_join_inputs(n, m):
    """n rows in R, m rows in S. Keys are modulo 100, so lots of duplicates."""
    lrows = [(i % 100, i) for i in range(n)]
    rrows = [(i % 100, i) for i in range(m)]
    return (ListOperator(lrows, LSCHEMA), ListOperator(rrows, RSCHEMA))


def pages_for_rows(n, tuple_bytes=12):
    """Estimate page count: n rows * tuple_bytes / 4096, minimum 1."""
    return max(1, math.ceil(n * tuple_bytes / 4096))


def measure_joins():
    results = []
    sizes = [100, 500, 2000]
    Bs = [4, 16, 64, 256]
    for n in sizes:
        for m in sizes:
            M = pages_for_rows(n)
            N = pages_for_rows(m)
            for B in Bs:
                for name, cls in [
                    ("nested_loop_join", NestedLoopJoin),
                    ("sort_merge_join", SortMergeJoin),
                    ("hash_join", HashJoin),
                ]:
                    left, right = make_join_inputs(n, m)
                    try:
                        j = cls("R.b = S.b", left, right, frames=B)
                        t0 = time.time()
                        rows = run_to_completion(j)
                        dt = time.time() - t0
                        c = j.counters()
                        if name == "nested_loop_join":
                            formula_reads = M + math.ceil(M / (B - 2)) * N
                            formula_comparisons = n * m
                        elif name == "sort_merge_join":
                            def sort_cost(pages, B):
                                if pages <= B:
                                    return pages
                                passes = 1 + math.ceil(math.log(math.ceil(pages / B)) / math.log(B - 1))
                                return 2 * pages * (passes - 1) + pages
                            formula_reads = sort_cost(M, B) + sort_cost(N, B) + M + N
                            formula_comparisons = None
                        else:
                            formula_reads = 3 * (M + N)
                            formula_comparisons = None
                        results.append({
                            "join": name, "n": n, "m": m, "B": B,
                            "M_pages": M, "N_pages": N,
                            "measured_reads": c["page_reads"],
                            "measured_writes": c["page_writes"],
                            "measured_comparisons": c["comparisons"],
                            "formula_reads": formula_reads,
                            "formula_comparisons": formula_comparisons,
                            "output_rows": len(rows),
                            "wall_time_s": dt,
                        })
                        print(f"  {name:<18} n={n:<5} m={m:<5} B={B:<3} "
                              f"reads={c['page_reads']:<4} comps={c['comparisons']:<9} "
                              f"t={dt:.3f}s out={len(rows)}")
                    except Exception as e:
                        results.append({
                            "join": name, "n": n, "m": m, "B": B, "error": str(e),
                        })
                        print(f"  {name:<18} n={n:<5} m={m:<5} B={B:<3} ERROR: {e}")
    return results


def measure_sort_passes():
    results = []
    combos = [
        (8, 3), (8, 4), (40, 4), (80, 4), (80, 8), (160, 8),
        (320, 16), (400, 20),
    ]
    for n_pages, B in combos:
        n_tuples = n_pages * 100
        src = ListOperator([(i,) for i in range(n_tuples, 0, -1)],
                           [("t.a", "INT")])
        try:
            s = Sort([("t.a", "asc")], src, frames=B, work_dir="/tmp")
            t0 = time.time()
            rows = run_to_completion(s)
            dt = time.time() - t0
            if n_pages <= B:
                formula_passes = 1
            else:
                formula_passes = 1 + math.ceil(math.log(math.ceil(n_pages / B)) / math.log(B - 1))
            results.append({
                "N_pages": n_pages, "B": B, "n_tuples": n_tuples,
                "measured_passes_estimated": len(s._temp_files),
                "formula_passes": formula_passes,
                "output_rows": len(rows),
                "wall_time_s": dt,
            })
            print(f"  sort N_pages={n_pages:<4} B={B:<3} "
                  f"temp_files={len(s._temp_files):<3} formula_passes={formula_passes} "
                  f"t={dt:.3f}s out={len(rows)}")
        except Exception as e:
            results.append({
                "N_pages": n_pages, "B": B, "error": str(e),
            })
            print(f"  sort N_pages={n_pages} B={B} ERROR: {e}")
    return results


def measure_crossover():
    results = {"by_B": [], "by_size": []}
    n, m = 2000, 2000
    for B in [4, 8, 16, 32, 64, 128, 256]:
        row = {"B": B, "n": n, "m": m}
        for name, cls in [
            ("nested_loop_join", NestedLoopJoin),
            ("sort_merge_join", SortMergeJoin),
            ("hash_join", HashJoin),
        ]:
            left, right = make_join_inputs(n, m)
            try:
                j = cls("R.b = S.b", left, right, frames=B)
                t0 = time.time()
                run_to_completion(j)
                dt = time.time() - t0
                row[name + "_wall_s"] = dt
            except Exception as e:
                row[name + "_error"] = str(e)
        results["by_B"].append(row)
        print(f"  by_B B={B:<4} bnl={row.get('nested_loop_join_wall_s', 'err'):}")

    B = 32
    for n in [100, 500, 2000, 5000]:
        m = n
        row = {"B": B, "n": n, "m": m}
        for name, cls in [
            ("nested_loop_join", NestedLoopJoin),
            ("sort_merge_join", SortMergeJoin),
            ("hash_join", HashJoin),
        ]:
            left, right = make_join_inputs(n, m)
            try:
                j = cls("R.b = S.b", left, right, frames=B)
                t0 = time.time()
                run_to_completion(j)
                dt = time.time() - t0
                row[name + "_wall_s"] = dt
            except Exception as e:
                row[name + "_error"] = str(e)
        results["by_size"].append(row)
        print(f"  by_size n={n:<5} bnl={row.get('nested_loop_join_wall_s', 'err')}")
    return results


def measure_skew():
    n = 2000
    skew_key = 42
    n_skewed = int(n * 0.4)
    lrows = [(skew_key, i) for i in range(n_skewed)] + \
            [(i % 100, i) for i in range(n - n_skewed)]
    rrows = [(skew_key, i) for i in range(n_skewed)] + \
            [(i % 100, i) for i in range(n - n_skewed)]
    left = ListOperator(lrows, LSCHEMA)
    right = ListOperator(rrows, RSCHEMA)
    B = 32
    j = HashJoin("R.b = S.b", left, right, frames=B)
    t0 = time.time()
    rows = run_to_completion(j)
    dt = time.time() - t0
    P = max(2, B - 1)
    part_sizes_l = [0] * P
    part_sizes_r = [0] * P
    for t in lrows:
        part_sizes_l[hash(t[0]) % P] += 1
    for t in rrows:
        part_sizes_r[hash(t[0]) % P] += 1
    return {
        "n": n, "skew_fraction": 0.4, "B": B, "P": P,
        "partition_sizes_left": part_sizes_l,
        "partition_sizes_right": part_sizes_r,
        "max_partition_left": max(part_sizes_l),
        "max_partition_right": max(part_sizes_r),
        "output_rows": len(rows),
        "wall_time_s": dt,
        "overflows_budget": max(part_sizes_l) > (B - 1) * 100,
    }


def main():
    print("measure 1: joins cost vs formula ...")
    m1 = measure_joins()
    print(f"  {len(m1)} runs")
    print("measure 2: sort pass counts ...")
    m2 = measure_sort_passes()
    print(f"  {len(m2)} combos")
    print("measure 3: crossover ...")
    m3 = measure_crossover()
    print(f"  {len(m3['by_B'])} by_B, {len(m3['by_size'])} by_size")
    print("measure 4: skew ...")
    m4 = measure_skew()
    print(f"  max partition: left={m4['max_partition_left']} right={m4['max_partition_right']}")

    out = {
        "measure1_joins": m1,
        "measure2_sort_passes": m2,
        "measure3_crossover": m3,
        "measure4_skew": m4,
    }
    os.makedirs("results", exist_ok=True)
    with open("results/m2_measurements.json", "w") as f:
        json.dump(out, f, indent=2)
    print("wrote results/m2_measurements.json")


if __name__ == "__main__":
    main()