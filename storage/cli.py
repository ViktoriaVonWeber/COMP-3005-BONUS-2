# storage/cli.py — the five storage subcommands. JSON shapes match M1 section 7.
import argparse, json, os, sys
from storage.catalog import Catalog
from storage.loader import load_csv
from storage.disk import DiskManager
from storage.buffer import BufferPool
from storage.heap import HeapFile
from storage.record import decode_tuple
from storage.page import SlottedPage, HEADER_SIZE, SLOT_SIZE, PAGE_SIZE
from engine.errors import EngineError


def cmd_create_table(args):
    cat = Catalog(args.db)
    schema = []
    for part in args.schema.split(","):
        name, typ = part.split(":")
        typ = typ.strip()
        if typ == "INT":
            schema.append((name.strip(), "INT", None))
        elif typ.startswith("VARCHAR"):
            n = int(typ[typ.index("(") + 1:typ.index(")")])
            schema.append((name.strip(), "VARCHAR", n))
        else:
            raise EngineError("schema", f"unknown type {typ!r}")
    cat.create_table(args.name, schema)
    print(json.dumps({"created": args.name, "schema": schema}))


def cmd_load(args):
    cat = Catalog(args.db)
    n = load_csv(cat, args.table, args.file)
    print(json.dumps({"table": args.table, "rows_loaded": n}))


def cmd_scan(args):
    cat = Catalog(args.db)
    entry = cat.get_table(args.table)
    disk = DiskManager(cat.data_path(args.table))
    pool = BufferPool(disk, args.frames, policy="lru")
    heap = HeapFile(disk, args.table)
    rows = []
    for i, (_rid, raw) in enumerate(heap.scan()):
        if args.limit and i >= args.limit:
            break
        tup, _ = decode_tuple(raw, entry["schema"])
        rows.append(list(tup))
    out = {
        "table": args.table,
        "schema": [list(c) for c in entry["schema"]],
        "rows": rows,
        "stats": pool.stats(),
    }
    print(json.dumps(out))
    disk.close()


def cmd_layout(args):
    cat = Catalog(args.db)
    entry = cat.get_table(args.table)
    disk = DiskManager(cat.data_path(args.table))
    raw = disk.read_page(args.page)
    page = SlottedPage(args.page, raw)
    out = {"page": args.page, "slot_count": page.slot_count,
           "free_end": page.free_end, "free_space": page.free_space(),
           "slots": []}
    for i in range(page.slot_count):
        off, L = __import__("struct").unpack_from("<HH", page.data, HEADER_SIZE + SLOT_SIZE * i)
        out["slots"].append({"slot": i, "offset": off, "length": L,
                             "dead": off == 0})
    print(json.dumps(out))
    disk.close()


def cmd_trace(args):
    disk = DiskManager(os.path.join(args.db, "trace_scratch.db"))
    pool = BufferPool(disk, args.frames, policy=args.policy)
    with open(args.file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            pid = int(line)
            while disk.num_pages() <= pid:
                disk.allocate_page()
            pool.fetch_page(pid)
            pool.unpin_page(pid, is_dirty=False)
    out = {"policy": args.policy, "frames": args.frames, **pool.stats()}
    print(json.dumps(out))
    disk.close()
    os.remove(disk.path)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="storage")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("create-table")
    p.add_argument("--db", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--schema", required=True)
    p.set_defaults(fn=cmd_create_table)

    p = sub.add_parser("load")
    p.add_argument("--db", required=True)
    p.add_argument("--table", required=True)
    p.add_argument("--file", required=True)
    p.set_defaults(fn=cmd_load)

    p = sub.add_parser("scan")
    p.add_argument("--db", required=True)
    p.add_argument("--table", required=True)
    p.add_argument("--frames", type=int, required=True)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_scan)

    p = sub.add_parser("layout")
    p.add_argument("--db", required=True)
    p.add_argument("--table", required=True)
    p.add_argument("--page", type=int, required=True)
    p.set_defaults(fn=cmd_layout)

    p = sub.add_parser("trace")
    p.add_argument("--db", required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--frames", type=int, required=True)
    p.add_argument("--policy", choices=["lru", "clock"], required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_trace)

    args = ap.parse_args(argv)
    try:
        args.fn(args)
    except EngineError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"error: internal: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()