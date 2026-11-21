# tests/test_m2_planner.py - plan validation errors.
from exec.planner import build_plan
from engine.errors import EngineError


class MockCatalog:
    def get_table(self, name):
        return {"schema": [("a", "INT"), ("b", "INT")], "first_page": 0}
    def data_path(self, name):
        return "/tmp/nonexistent"


class MockPool:
    pass


def test_missing_op_errors():
    try:
        build_plan({}, MockCatalog(), MockPool(), frames=4)
        assert False
    except EngineError as e:
        assert "missing 'op'" in str(e)


def test_unknown_op_errors():
    try:
        build_plan({"op": "merge_join"}, MockCatalog(), MockPool(), frames=4)
        assert False
    except EngineError as e:
        assert "unknown operator" in str(e)


def test_sort_missing_keys_errors():
    try:
        build_plan({"op": "sort",
                    "child": {"op": "seqscan", "table": "R"}},
                   MockCatalog(), MockPool(), frames=4)
        assert False
    except EngineError as e:
        assert "'keys'" in str(e)


def test_filter_condition_not_string_errors():
    try:
        build_plan({"op": "filter", "condition": 42,
                    "child": {"op": "seqscan", "table": "R"}},
                   MockCatalog(), MockPool(), frames=4)
        assert False
    except EngineError as e:
        assert "must be str" in str(e)


def test_project_columns_not_list_errors():
    try:
        build_plan({"op": "project", "columns": "R.a",
                    "child": {"op": "seqscan", "table": "R"}},
                   MockCatalog(), MockPool(), frames=4)
        assert False
    except EngineError as e:
        assert "'columns'" in str(e)


def test_distinct_bad_mode_errors():
    try:
        build_plan({"op": "distinct", "mode": "bogus",
                    "child": {"op": "seqscan", "table": "R"}},
                   MockCatalog(), MockPool(), frames=4)
        assert False
    except EngineError as e:
        assert "must be one of" in str(e)


def test_missing_child_errors():
    try:
        build_plan({"op": "filter", "condition": "R.a > 0"},
                   MockCatalog(), MockPool(), frames=4)
        assert False
    except EngineError as e:
        assert "'child'" in str(e)

