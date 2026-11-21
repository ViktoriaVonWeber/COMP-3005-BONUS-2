# tests/test_m2_cli.py - exec run / exec trace end-to-end.
import json, os, shutil, subprocess, sys, tempfile


def _setup_db():
    d = tempfile.mkdtemp()
    subprocess.check_call([sys.executable, "-m", "storage", "create-table",
                           "--db", d, "--name", "R",
                           "--schema", "a:INT,b:INT"])
    csv = os.path.join(d, "r.csv")
    with open(csv, "w") as f:
        f.write("1,10\n2,20\n3,30\n")
    subprocess.check_call([sys.executable, "-m", "storage", "load",
                           "--db", d, "--table", "R", "--file", csv])
    return d


def test_run_seqscan():
    d = _setup_db()
    try:
        plan = {"op": "seqscan", "table": "R"}
        plan_path = os.path.join(d, "plan.json")
        with open(plan_path, "w") as f:
            json.dump(plan, f)
        out = subprocess.check_output(
            [sys.executable, "-m", "exec", "run",
             "--db", d, "--plan", plan_path, "--frames", "4", "--json"]
        )
        data = json.loads(out)
        assert len(data["rows"]) == 3
        assert data["schema"] == [["R.a", "INT"], ["R.b", "INT"]]
    finally:
        shutil.rmtree(d)


def test_run_filter():
    d = _setup_db()
    try:
        plan = {"op": "filter", "condition": "R.a > 1",
                "child": {"op": "seqscan", "table": "R"}}
        plan_path = os.path.join(d, "plan.json")
        with open(plan_path, "w") as f:
            json.dump(plan, f)
        out = subprocess.check_output(
            [sys.executable, "-m", "exec", "run",
             "--db", d, "--plan", plan_path, "--frames", "4", "--json"]
        )
        data = json.loads(out)
        assert len(data["rows"]) == 2
    finally:
        shutil.rmtree(d)


def test_bad_plan_exits_nonzero():
    d = _setup_db()
    try:
        plan = {"op": "filter", "condition": 42,
                "child": {"op": "seqscan", "table": "R"}}
        plan_path = os.path.join(d, "plan.json")
        with open(plan_path, "w") as f:
            json.dump(plan, f)
        result = subprocess.run(
            [sys.executable, "-m", "exec", "run",
             "--db", d, "--plan", plan_path, "--frames", "4", "--json"],
            capture_output=True, text=True
        )
        assert result.returncode != 0
        assert "must be a string" in result.stderr
    finally:
        shutil.rmtree(d)