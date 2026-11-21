# storage/catalog.py — catalog stored in catalog.db via the buffer pool.
# On-page format is ours to design; must reserve space for M4 statistics.
#
# Catalog page layout (per table entry, one entry per page):
#   offset  size  field
#        0     2  name_len          uint16
#        2     n  name              UTF-8
#     2+n     2  ncols             uint16
#   then per column (repeated ncols times):
#      1 byte   type_code        1=INT, 2=VARCHAR
#      1 byte   name_len
#      n bytes  name
#      1 byte   varlen            (0 for INT, n for VARCHAR(n))
#   next:
#      4 bytes  first_page        uint32
#      4 bytes  num_pages         uint32
#      4 bytes  num_tuples        uint32
#   reserved for M4 statistics:
#      4 bytes  stats_len         uint32 (0 = none yet)
#      0..      stats bytes       JSON encoded (or empty)
import json, os, struct
from storage.disk import DiskManager
from storage.buffer import BufferPool
from storage.page import PAGE_SIZE, HEADER_SIZE
from engine.errors import EngineError

MAGIC = b"CATL"


class Catalog:
    def __init__(self, db_dir):
        self.db_dir = db_dir
        os.makedirs(db_dir, exist_ok=True)
        self.disk = DiskManager(os.path.join(db_dir, "catalog.db"))
        self.pool = BufferPool(self.disk, frames=16, policy="lru")
        self._load()

    def _load(self):
        self.tables = {}
        n = self.disk.num_pages()
        for pid in range(n):
            data = self.disk.read_page(pid)
            if data[:4] != MAGIC:
                continue
            entry = self._decode(data)
            if entry:
                self.tables[entry["name"]] = entry

    def _encode(self, entry):
        buf = bytearray(PAGE_SIZE)
        buf[0:4] = MAGIC
        name = entry["name"].encode("utf-8")
        struct.pack_into("<H", buf, 4, len(name))
        buf[6:6+len(name)] = name
        off = 6 + len(name)
        struct.pack_into("<H", buf, off, len(entry["schema"]))
        off += 2
        for (cname, ctype, clen) in entry["schema"]:
            buf[off] = 1 if ctype == "INT" else 2; off += 1
            cb = cname.encode("utf-8")
            buf[off] = len(cb); off += 1
            buf[off:off+len(cb)] = cb; off += len(cb)
            buf[off] = 0 if ctype == "INT" else clen; off += 1
        struct.pack_into("<III", buf, off,
                         entry["first_page"], entry["num_pages"], entry["num_tuples"])
        off += 12
        stats = entry.get("statistics") or {}
        sb = json.dumps(stats).encode("utf-8")
        struct.pack_into("<I", buf, off, len(sb)); off += 4
        buf[off:off+len(sb)] = sb
        return bytes(buf)

    def _decode(self, data):
        if data[:4] != MAGIC: return None
        off = 4
        nlen = struct.unpack_from("<H", data, off)[0]; off += 2
        name = data[off:off+nlen].decode("utf-8"); off += nlen
        ncols = struct.unpack_from("<H", data, off)[0]; off += 2
        schema = []
        for _ in range(ncols):
            tcode = data[off]; off += 1
            cname_len = data[off]; off += 1
            cname = data[off:off+cname_len].decode("utf-8"); off += cname_len
            clen = data[off]; off += 1
            ctype = "INT" if tcode == 1 else "VARCHAR"
            schema.append((cname, ctype, clen if ctype == "VARCHAR" else None))
        first_page, num_pages, num_tuples = struct.unpack_from("<III", data, off); off += 12
        stats_len = struct.unpack_from("<I", data, off)[0]; off += 4
        stats = json.loads(data[off:off+stats_len].decode("utf-8")) if stats_len else {}
        return {"name": name, "schema": schema, "first_page": first_page,
                "num_pages": num_pages, "num_tuples": num_tuples, "statistics": stats}

    # ----- API -----
    def create_table(self, name, schema):
        if name in self.tables:
            raise EngineError("schema", f"table {name} already exists")
        entry = {"name": name, "schema": schema,
                 "first_page": 0, "num_pages": 0, "num_tuples": 0,
                 "statistics": {}}
        pid = self.disk.allocate_page()
        self.disk.write_page(pid, self._encode(entry))
        self.tables[name] = entry

    def get_table(self, name):
        if name not in self.tables:
            raise EngineError("name", f"unknown table {name}")
        return self.tables[name]

    def update_table(self, name, **fields):
        entry = self.get_table(name)
        for k, v in fields.items():
            entry[k] = v
        # find this entry's catalog page and rewrite
        for pid in range(self.disk.num_pages()):
            data = self.disk.read_page(pid)
            if data[:4] == MAGIC:
                e = self._decode(data)
                if e and e["name"] == name:
                    self.disk.write_page(pid, self._encode(entry))
                    return
        raise EngineError("schema", f"catalog entry for {name} not found on disk")

    def list_tables(self):
        return sorted(self.tables.keys())

    def data_path(self, name):
        return os.path.join(self.db_dir, f"{name}.db")
