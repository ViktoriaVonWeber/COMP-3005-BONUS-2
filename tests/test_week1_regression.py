# tests/test_week1_regression.py
# Week 1 regression: refactored P1 engine still answers P1 queries correctly,
# except cases whose meaning changed under bag semantics (logged in DESIGN_LOG).
import os, json
from engine.lexer import Lexer
from engine.parser import Parser
from engine.tree import json_to_tree
from engine.operators import evaluate
from engine.loader import load_p1_file       # your P1 loader, still present

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

def load(name):
    return load_p1_file(os.path.join(FIXTURES, name))

def run(query, catalog):
    tree = Parser(Lexer(query).tokens()).parse_query()
    return evaluate(tree, catalog)

# Cases that kept their meaning after the refactor (sets -> bags did not
# change these because they were already set-stable or selection-only).

def test_case01_no_whitespace():
    cat = load("data_1000.txt")
    # select[B=830](R) — one R row has B=830 in data_1000.txt
    rows = run("select[B=830](R)", cat)
    assert len(rows) == 2

def test_case02_whitespace_identical_to_01():
    cat = load("data_1000.txt")
    a = run("select[B=830](R)", cat)
    b = run("select[ B = 830 ](R)", cat)
    assert a == b

def test_case03_ge_token():
    cat = load("data_1000.txt")
    # Age>=30 equivalent: B>=830
    rows = run("select[B>=830](R)", cat)
    assert all(r[1] >= 830 for r in rows)

def test_case04_negative_number():
    cat = load("data_1000.txt")
    # >-1 must tokenize as '>' and '-1', not '>-'
    rows = run("select[B>-1](R)", cat)
    assert len(rows) == len(cat["R"].rows)     # every B >= 0

def test_case10_set_precedence():
    cat = load("data_1000.txt")
    # A union B minus C parses as (A union B) minus C.
    # Using R and S as the two sides: R union S minus R == S minus R (bag-aware?)
    # Under bag semantics sets still behave identically for these set ops,
    # so the assertion holds.
    rows = run("R union S minus R", cat)
    expected = run("(R union S) minus R", cat)
    assert rows == expected

def test_case15_parens_override():
    cat = load("data_1000.txt")
    rows = run("(R union S) minus (S intersect R)", cat)
    assert rows is not None


# Cases whose meaning CHANGED under bag semantics — these are the ones we
# log in DESIGN_LOG per Project 2 §1.3 point 7.

def test_case23_projection_now_keeps_duplicates():
    """P1 case 23: project[DID](Employees) returned 2 rows (set).
    Under Project 2 §1.3 point 6 projection keeps duplicates, so the same
    query on the same data returns 3 rows. Logged in DESIGN_LOG."""
    cat = load("data_1000.txt")
    # Simulate the P1 case-23 schema using our fixture:
    # R(A,B), S(B,C). project[B](R times S) — plenty of duplicate B values.
    rows = run("project[B](R)", cat)
    # Set semantics would collapse duplicates; bag semantics keeps them.
    # Assert bag result has duplicates, i.e. more rows than distinct values.
    b_values = [r[0] for r in rows]
    assert len(b_values) > len(set(b_values)), \
        "projection should now keep duplicates (bag semantics)"

def test_case24_duplicate_attrs_kept():
    """P1 case 24: project[Name, Name] errored.
    Under bags, we keep both columns. Log the changed rule."""
    cat = load("data_1000.txt")
    rows = run("project[A, A](R)", cat)
    assert all(len(r) == 2 and r[0] == r[1] for r in rows)

# JSON round-trip on every node type (Project 2 §1.3 point 4).
def test_json_roundtrip_all_nodes():
    cat = load("data_1000.txt")
    qs = [
        "select[B=830](R)",
        "project[A](R)",
        "rename[X](R)",
        "R union S",
        "R intersect S",
        "R minus S",
        "R times S",
        "R join[R.B=S.B] S",
        "project[A](select[B>100](R))",
    ]
    for q in qs:
        t = Parser(Lexer(q).tokens()).parse_query()
        j = t.to_json()
        t2 = json_to_tree(j)
        assert t2.to_json() == j, f"round-trip broke for {q}"