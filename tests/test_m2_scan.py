# tests/test_m2_scan.py - seqscan and filter against M1 data.
import os, shutil, tempfile
from storage.catalog import Catalog
from storage.loader import load_csv
from storage.disk import DiskManager
from storage.buffer import BufferPool
from exec.scan import SeqScan, Filter
from exec.operator import END


def setup_db():
    d = tempfile.mkdtemp()
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
    return d, cat, disk, pool


def teardown_db(d, disk):
    disk.close()
    shutil.rmtree(d)


def test_seqscan_returns_all_rows():
    d, cat, disk, pool = setup_db()
    try:
        scan = SeqScan("Alerts", cat, pool)
        scan.open()
        rows = []
        while True:
            t = scan.next()
            if t is END:
                break
            rows.append(t)
        scan.close()
        assert len(rows) == 5
        assert rows[0] == (1, 9, "1.2.3.4")
        assert rows[4] == (5, 7, "1.2.3.4")
    finally:
        teardown_db(d, disk)


def test_seqscan_schema():
    d, cat, disk, pool = setup_db()
    try:
        scan = SeqScan("Alerts", cat, pool)
        scan.open()
        assert scan.schema() == [("Alerts.id", "INT"),
                                 ("Alerts.severity", "INT"),
                                 ("Alerts.ioc", "VARCHAR")]
        scan.close()
    finally:
        teardown_db(d, disk)


def test_filter_passes_matching_rows():
    d, cat, disk, pool = setup_db()
    try:
        scan = SeqScan("Alerts", cat, pool)
        f = Filter("Alerts.severity > 6", scan)
        f.open()
        rows = []
        while True:
            t = f.next()
            if t is END:
                break
            rows.append(t)
        f.close()
        assert len(rows) == 3
        assert {r[0] for r in rows} == {1, 3, 5}
    finally:
        teardown_db(d, disk)


def test_filter_increments_comparisons_per_tuple():
    d, cat, disk, pool = setup_db()
    try:
        scan = SeqScan("Alerts", cat, pool)
        f = Filter("Alerts.severity > 6", scan)
        f.open()
        while f.next() is not END:
            pass
        f.close()
        assert f.counters()["comparisons"] == 5
    finally:
        teardown_db(d, disk)


def test_filter_schema_passes_through():
    d, cat, disk, pool = setup_db()
    try:
        scan = SeqScan("Alerts", cat, pool)
        f = Filter("Alerts.severity > 6", scan)
        f.open()
        assert f.schema() == scan.schema()
        f.close()
    finally:
        teardown_db(d, disk)


def test_filter_empty_result():
    d, cat, disk, pool = setup_db()
    try:
        scan = SeqScan("Alerts", cat, pool)
        f = Filter("Alerts.severity > 100", scan)
        f.open()
        rows = []
        while True:
            t = f.next()
            if t is END:
                break
            rows.append(t)
        f.close()
        assert rows == []
        assert f.counters()["comparisons"] == 5
    finally:
        teardown_db(d, disk)
