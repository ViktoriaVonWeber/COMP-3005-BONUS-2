# tests/test_m1_catalog.py — catalog + loader end to end.
import os, tempfile, shutil
from storage.catalog import Catalog
from storage.loader import load_csv
from storage.disk import DiskManager
from storage.heap import HeapFile
from storage.record import encode_tuple


def test_catalog_create_and_get():
    d = tempfile.mkdtemp()
    try:
        cat = Catalog(d)
        cat.create_table("Alerts", [("id","INT",None),("severity","INT",None),
                                    ("ioc","VARCHAR",32)])
        e = cat.get_table("Alerts")
        assert e["name"] == "Alerts"
        assert len(e["schema"]) == 3
        assert cat.list_tables() == ["Alerts"]
    finally:
        shutil.rmtree(d)


def test_load_csv_and_read_back():
    d = tempfile.mkdtemp()
    try:
        cat = Catalog(d)
        cat.create_table("Alerts", [("id","INT",None),("severity","INT",None),
                                    ("ioc","VARCHAR",32)])
        csv = os.path.join(d, "alerts.csv")
        with open(csv, "w") as f:
            f.write("1,9,1.2.3.4\n2,5,5.6.7.8\n3,8,1.2.3.4\n")
        n = load_csv(cat, "Alerts", csv)
        assert n == 3
        assert cat.get_table("Alerts")["num_tuples"] == 3
        # read back
        disk = DiskManager(cat.data_path("Alerts"))
        heap = HeapFile(disk, "Alerts")
        recs = [r for _rid, r in heap.scan()]
        assert len(recs) == 3
        # decode one
        from storage.record import decode_tuple
        tup, _ = decode_tuple(recs[0], cat.get_table("Alerts")["schema"])
        assert tup == (1, 9, "1.2.3.4")
    finally:
        shutil.rmtree(d)


def test_bad_int_names_lineno():
    from engine.errors import EngineError
    d = tempfile.mkdtemp()
    try:
        cat = Catalog(d)
        cat.create_table("Alerts", [("id","INT",None)])
        csv = os.path.join(d, "bad.csv")
        with open(csv, "w") as f:
            f.write("1\nnot_an_int\n")
        try:
            load_csv(cat, "Alerts", csv)
            assert False, "should have raised"
        except EngineError as e:
            assert "line 2" in str(e)
    finally:
        shutil.rmtree(d)
