# storage/loader.py — CSV loader. No header line, one record per line,
# fields comma-separated, double-quoted if containing a comma.
# Errors name the line number and stop.
from storage.record import encode_tuple, encode_int
from storage.heap import HeapFile
from storage.disk import DiskManager
from engine.errors import EngineError
import os


def parse_csv_line(line):
    """Split a CSV line respecting double quotes. Returns list of str fields."""
    out, buf, i, in_q = [], [], 0, False
    while i < len(line):
        c = line[i]
        if in_q:
            if c == '"':
                if i + 1 < len(line) and line[i+1] == '"':
                    buf.append('"'); i += 2; continue
                in_q = False; i += 1; continue
            buf.append(c); i += 1
        else:
            if c == ',':
                out.append("".join(buf)); buf = []; i += 1
            elif c == '"':
                in_q = True; i += 1
            else:
                buf.append(c); i += 1
    out.append("".join(buf))
    return out


def _coerce(field, ctype, clen, lineno, cname):
    if ctype == "INT":
        try: return int(field.strip())
        except ValueError:
            raise EngineError("schema", f"line {lineno}: column {cname}: bad INT {field!r}")
    if ctype == "VARCHAR":
        s = field
        if len(s.encode("utf-8")) > clen:
            raise EngineError("schema",
                f"line {lineno}: column {cname}: {len(s)} bytes > VARCHAR({clen})")
        return s
    raise EngineError("schema", f"unknown type {ctype}")


def load_csv(catalog, table_name, csv_path):
    entry = catalog.get_table(table_name)
    schema = entry["schema"]
    disk = DiskManager(catalog.data_path(table_name))
    heap = HeapFile(disk, table_name)
    n = 0
    with open(csv_path, "r") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.rstrip("\n")
            if not line:
                continue
            fields = parse_csv_line(line)
            if len(fields) != len(schema):
                raise EngineError("schema",
                    f"line {lineno}: expected {len(schema)} fields, got {len(fields)}")
            tup = tuple(_coerce(f, t, l, lineno, name)
                        for f, (name, t, l) in zip(fields, schema))
            rec = encode_tuple(tup, schema)
            heap.insert(rec)
            n += 1
    catalog.update_table(table_name, num_tuples=n)
    disk.close()
    return n
