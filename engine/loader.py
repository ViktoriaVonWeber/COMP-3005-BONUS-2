from engine.errors import EngineError

class RelationData:
    def __init__(self, name, attrs):
        self.name = name; self.attrs = attrs; self.rows = set()
    def schema(self):
        return [(a, "INT") for a in self.attrs]

def load_p1_file(path):
    catalog = {}
    with open(path, "r") as f:
        lines = f.readlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip(); i += 1
        if not line or line.startswith("//"): continue
        if "(" not in line or ")" not in line or "=" not in line:
            raise EngineError("syntax", f"bad header: {line!r}")
        name = line[:line.index("(")].strip()
        attrs = [a.strip() for a in line[line.index("(")+1:line.index(")")].split(",")]
        rows = []
        while i < len(lines):
            t = lines[i].strip(); i += 1
            if not t or t.startswith("//"): continue
            if t == "}": break
            vals = [v.strip() for v in t.split(",")]
            if len(vals) != len(attrs):
                raise EngineError("schema",
                    f"{name}: expected {len(attrs)} values, got {len(vals)}")
            rows.append(tuple(_parse_val(v) for v in vals))
        else:
            raise EngineError("syntax", f"{name}: missing closing brace")
        rel = RelationData(name, attrs)
        rel.rows = set(rows)
        catalog[name] = rel
    return catalog

def _parse_val(v):
    try:    return int(v)
    except: return v.strip("'")
