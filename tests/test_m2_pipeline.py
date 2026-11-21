# tests/test_m2_pipeline.py - project, limit, union_all.
import os, shutil, tempfile
from storage.catalog import Catalog
from storage.loader import load_csv
from storage.disk import DiskManager
from storage.buffer import BufferPool
from exec.scan import SeqScan, Filter
from exec.pipeline import Project, Limit, UnionAll
from exec.operator import END, run_to_completion


def make_alerts(d):
    cat = Catalog(d)
    cat.create_table("Alerts", [("id", "INT", None),
                                ("severity", "INT", None),
                                ("ioc", "VARCHAR", 32)])
    csv = os.path.join(d, "alerts.csv")
    with open(csv, "w") as f:
        f.write("1,9,1.2.3.4\n"
                "2,5,5.6.7.8\n"
                "3,8,1.2.3.4\n"
                "4,2,9.9.9.9\n"
                "5,7,1.2.3.4\n")
    load_csv(cat, "Alerts", csv)
    disk = DiskManager(cat.data_path("Alerts"))
    pool = BufferPool(disk, frames=4, policy="lru")
    return cat, disk, pool


# ---- project ----

def test_project_selects_columns_in_order():
    d = tempfile.mkdtemp()
    try:
        cat, disk, pool = make_alerts(d)
        scan = SeqScan("Alerts", cat, pool)
        p = Project(["Alerts.ioc", "Alerts.id"], scan)
        rows = run_to_completion(p)
        assert rows[0] == ("1.2.3.4", 1)
        assert p.schema() == [("Alerts.ioc", "VARCHAR"), ("Alerts.id", "INT")]
    finally:
        disk.close(); shutil.rmtree(d)


def test_project_keeps_duplicates():
    """M2 section 2.3: project does not dedupe."""
    d = tempfile.mkdtemp()
    try:
        cat, disk, pool = make_alerts(d)
        scan = SeqScan("Alerts", cat, pool)
        p = Project(["Alerts.ioc"], scan)
        rows = run_to_completion(p)
        # iocs: 1.2.3.4, 5.6.7.8, 1.2.3.4, 9.9.9.9, 1.2.3.4
        # bag semantics: 5 rows, three of them 1.2.3.4
        assert len(rows) == 5
        assert sum(1 for r in rows if r == ("1.2.3.4",)) == 3
    finally:
        disk.close(); shutil.rmtree(d)


def test_project_unknown_column_errors():
    from engine.errors import EngineError
    d = tempfile.mkdtemp()
    try:
        cat, disk, pool = make_alerts(d)
        scan = SeqScan("Alerts", cat, pool)
        p = Project(["Alerts.nope"], scan)
        try:
            p.open()
            assert False, "should have raised"
        except EngineError as e:
            assert "unknown column" in str(e)
    finally:
        disk.close(); shutil.rmtree(d)


# ---- limit ----

def test_limit_truncates():
    d = tempfile.mkdtemp()
    try:
        cat, disk, pool = make_alerts(d)
        scan = SeqScan("Alerts", cat, pool)
        lim = Limit(2, scan)
        rows = run_to_completion(lim)
        assert len(rows) == 2
        assert rows[0] == (1, 9, "1.2.3.4")
        assert rows[1] == (2, 5, "5.6.7.8")
    finally:
        disk.close(); shutil.rmtree(d)


def test_limit_stops_pulling():
    """M2 section 2.10: limit must actually stop pulling."""
    d = tempfile.mkdtemp()
    try:
        cat, disk, pool = make_alerts(d)
        scan = SeqScan("Alerts", cat, pool)
        lim = Limit(2, scan)
        lim.open()
        lim.next()
        lim.next()
        # third call must return END without consuming more input
        t = lim.next()
        assert t is END
        lim.close()
        # scan emitted exactly 2 tuples
        assert scan.counters()["tuples_out"] == 2
    finally:
        disk.close(); shutil.rmtree(d)


def test_limit_zero():
    d = tempfile.mkdtemp()
    try:
        cat, disk, pool = make_alerts(d)
        scan = SeqScan("Alerts", cat, pool)
        lim = Limit(0, scan)
        rows = run_to_completion(lim)
        assert rows == []
    finally:
        disk.close(); shutil.rmtree(d)


# ---- union_all ----

def test_union_all_concatenates():
    d = tempfile.mkdtemp()
    try:
        cat, disk, pool = make_alerts(d)
        s1 = SeqScan("Alerts", cat, pool)
        s2 = SeqScan("Alerts", cat, pool)
        u = UnionAll(s1, s2)
        rows = run_to_completion(u)
        # 5 + 5 = 10 rows, duplicates preserved
        assert len(rows) == 10
        assert u.schema() == [("Alerts.id", "INT"),
                              ("Alerts.severity", "INT"),
                              ("Alerts.ioc", "VARCHAR")]
    finally:
        disk.close(); shutil.rmtree(d)


def test_union_all_preserves_duplicates():
    """union_all does not dedupe."""
    d = tempfile.mkdtemp()
    try:
        cat, disk, pool = make_alerts(d)
        s1 = SeqScan("Alerts", cat, pool)
        s2 = SeqScan("Alerts", cat, pool)
        u = UnionAll(s1, s2)
        rows = run_to_completion(u)
        # every row appears exactly twice
        from collections import Counter
        c = Counter(rows)
        for row, n in c.items():
            assert n == 2
    finally:
        disk.close(); shutil.rmtree(d)


def test_union_all_type_mismatch_errors():
    """Both sides must be position-wise type compatible."""
    from engine.errors import EngineError
    d = tempfile.mkdtemp()
    try:
        cat = Catalog(d)
        cat.create_table("A", [("x", "INT", None)])
        cat.create_table("B", [("y", "VARCHAR", 10)])
        with open(os.path.join(d, "a.csv"), "w") as f:
            f.write("1\n")
        with open(os.path.join(d, "b.csv"), "w") as f:
            f.write("hello\n")
        load_csv(cat, "A", os.path.join(d, "a.csv"))
        load_csv(cat, "B", os.path.join(d, "b.csv"))
        disk = DiskManager(cat.data_path("A"))
        pool = BufferPool(disk, frames=4, policy="lru")
        sa = SeqScan("A", cat, pool)
        # B uses a different disk; open a second pool for it
        disk_b = DiskManager(cat.data_path("B"))
        pool_b = BufferPool(disk_b, frames=4, policy="lru")
        sb = SeqScan("B", cat, pool_b)
        u = UnionAll(sa, sb)
        try:
            u.open()
            assert False, "should have raised"
        except EngineError as e:
            assert "type mismatch" in str(e)
        disk.close(); disk_b.close()
    finally:
        shutil.rmtree(d)
