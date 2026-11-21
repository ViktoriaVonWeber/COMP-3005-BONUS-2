# workload/build_dataset.py - build the Module 4 measurement database.
#
# M4 section 7 says the 20-query workload and dataset are "published
# alongside this document." We did not have the published versions, so
# we construct our own and document that in DESIGN_M4.md.
#
# The dataset is designed so that:
#   - All four measurements have work to do (varying selectivities,
#     joins of different sizes, enough rows that plan choice matters).
#   - §7's "where it breaks" case has a real failure: two columns,
#     Emp.did and Emp.region, are strongly correlated (region is a
#     deterministic function of did), which violates the independence
#     assumption in rule 2's selectivity estimate.
#
# Usage:
#   python -m workload.build_dataset --db /tmp/m4db
#
# Creates:
#   Emp(id, name, did, region, age, salary)       10,000 rows
#   Dept(did, title, budget, city)                    50 rows
#   Region(region, country)                            5 rows
#   Log(log_id, emp_id, action)                    20,000 rows
#
# The correlation: for every employee, region = region_of(did).
# So WHERE did = d AND region = r is either 1/50 * 1 (if r matches d's
# region) or 0 (if not), but the optimizer's independence formula
# computes 1/50 * 1/5 = 1/250 regardless.

import argparse
import os
import random
import sys

from storage.catalog import Catalog
from storage.loader import load_csv


# Dept did -> (title, budget, city, region)
DEPT_ROWS = [
    (1,  "Engineering",  500000, "Seattle",   "NA"),
    (2,  "Sales",        200000, "New York",  "NA"),
    (3,  "Marketing",    150000, "New York",  "NA"),
    (4,  "Support",      100000, "Austin",    "NA"),
    (5,  "Finance",      300000, "Boston",    "NA"),
    (6,  "Legal",         50000, "Boston",    "NA"),
    (7,  "HR",            80000, "Austin",    "NA"),
    (8,  "Research",     400000, "Seattle",   "NA"),
    (9,  "Design",       120000, "Portland",  "NA"),
    (10, "QA",            90000, "Portland",  "NA"),
]
# Pads out to 50 rows, still with region
for i in range(11, 51):
    DEPT_ROWS.append((i, f"Dept{i}", 50000 + i * 1000, "City" + str(i % 7), "NA"))

# Region -> country
REGION_ROWS = [
    ("NA",  "United States"),
    ("EU",  "Germany"),
    ("APAC", "Japan"),
    ("LATAM", "Brazil"),
    ("MEA", "Egypt"),
]

# Which region each department maps to (deterministic; the other
# columns are deterministic too, which is what we exploit in §7).
DEPT_REGION = {}
for (did, _t, _b, _c, reg) in DEPT_ROWS[:10]:
    DEPT_REGION[did] = reg
# Departments 11-50 stay in NA (their dept region was already NA).


def build(db_dir, n_emp=10000, n_log=20000, seed=42):
    random.seed(seed)
    os.makedirs(db_dir, exist_ok=True)
    cat = Catalog(db_dir)

    # ---- Emp ----
    cat.create_table("Emp", [
        ("id", "INT", None),
        ("name", "VARCHAR", 20),
        ("did", "INT", None),
        ("region", "VARCHAR", 10),
        ("age", "INT", None),
        ("salary", "INT", None),
    ])
    emp_csv = os.path.join(db_dir, "_emp.csv")
    names = ["Ann", "Bob", "Cat", "Dan", "Eve", "Fay", "Gus", "Hal",
             "Ivy", "Jon", "Kim", "Lee", "Meg", "Ned", "Oli", "Pam",
             "Quy", "Rob", "Sam", "Tom", "Uma", "Vic", "Wen", "Xue",
             "Yan", "Zed"]
    with open(emp_csv, "w") as f:
        for i in range(n_emp):
            name = random.choice(names) + str(i % 1000)
            did = random.randint(1, 50)
            region = DEPT_REGION.get(did, "NA")  # <-- the correlation
            age = random.randint(22, 65)
            salary = random.randint(40000, 200000)
            f.write(f"{i},{name},{did},{region},{age},{salary}\n")

    # ---- Dept ----
    cat.create_table("Dept", [
        ("did", "INT", None),
        ("title", "VARCHAR", 20),
        ("budget", "INT", None),
        ("city", "VARCHAR", 20),
    ])
    dept_csv = os.path.join(db_dir, "_dept.csv")
    with open(dept_csv, "w") as f:
        for (did, title, budget, city, _reg) in DEPT_ROWS:
            f.write(f"{did},{title},{budget},{city}\n")

    # ---- Region ----
    cat.create_table("Region", [
        ("region", "VARCHAR", 10),
        ("country", "VARCHAR", 20),
    ])
    region_csv = os.path.join(db_dir, "_region.csv")
    with open(region_csv, "w") as f:
        for (reg, country) in REGION_ROWS:
            f.write(f"{reg},{country}\n")

    # ---- Log ----
    cat.create_table("Log", [
        ("log_id", "INT", None),
        ("emp_id", "INT", None),
        ("action", "VARCHAR", 20),
    ])
    log_csv = os.path.join(db_dir, "_log.csv")
    actions = ["login", "logout", "view", "edit", "delete", "search"]
    with open(log_csv, "w") as f:
        for i in range(n_log):
            emp_id = random.randint(0, n_emp - 1)
            action = random.choice(actions)
            f.write(f"{i},{emp_id},{action}\n")

    # Load all four
    from storage.loader import load_csv
    load_csv(cat, "Emp", emp_csv)
    load_csv(cat, "Dept", dept_csv)
    load_csv(cat, "Region", region_csv)
    load_csv(cat, "Log", log_csv)

    # Clean up CSVs
    for path in (emp_csv, dept_csv, region_csv, log_csv):
        try:
            os.remove(path)
        except OSError:
            pass

    print(f"built {db_dir}: Emp={n_emp}, Dept=50, Region=5, Log={n_log}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="workload.build_dataset")
    ap.add_argument("--db", required=True)
    ap.add_argument("--n-emp", type=int, default=10000)
    ap.add_argument("--n-log", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args(argv)
    build(args.db, n_emp=args.n_emp, n_log=args.n_log, seed=args.seed)


if __name__ == "__main__":
    main()