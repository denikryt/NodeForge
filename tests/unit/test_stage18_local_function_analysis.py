"""Unit-level checks for Stage 18 local-function capture analysis."""

import ast
import sys
import types

import pytest

pytestmark = pytest.mark.unit


def _import_local_functions_with_stubbed_bpy(monkeypatch):
    """Import local_functions in ordinary CPython without a Blender runtime."""
    monkeypatch.setitem(sys.modules, "bpy", types.SimpleNamespace(data=types.SimpleNamespace(node_groups={})))
    from NodeForge import local_functions

    return local_functions


class _CaptureComp:
    """Minimal compiler-like object consumed by _analyze_captures."""

    def __init__(self, labels, *, vars=None, consts=None, local_functions=None):
        self.vars = dict(vars or {})
        self.consts = dict(consts or {})
        self.local_functions = dict(local_functions or {})
        self.reserved_name_labels = labels


def test_stage18_allowed_compile_time_constants_are_not_hidden_captures(monkeypatch):
    local_functions = _import_local_functions_with_stubbed_bpy(monkeypatch)
    fn = ast.parse("def f(x):\n    return x * pi + tau - e\n").body[0]

    captures = local_functions._analyze_captures(
        _CaptureComp({"pi": "compile-time constant", "tau": "compile-time constant", "e": "compile-time constant"}),
        fn,
    )

    assert captures == ()


def test_stage18_nested_local_function_calls_forward_outer_captures(monkeypatch):
    local_functions = _import_local_functions_with_stubbed_bpy(monkeypatch)
    from NodeForge.constants import TYPE_FLOAT

    class _Value:
        typ = TYPE_FLOAT

    stmts = ast.parse(
        """
a = input_float("A")

def g(x):
    return x + a

def f(x):
    return g(x)
"""
    ).body
    functions = {stmt.name: stmt for stmt in stmts if isinstance(stmt, ast.FunctionDef)}

    captures = local_functions._analyze_captures(
        _CaptureComp({}, vars={"a": _Value()}, local_functions=functions),
        functions["f"],
    )

    assert [capture[0] for capture in captures] == ["a"]
    assert captures[0][2] is True


def test_stage18_helper_namespace_fragment_preserves_distinct_parent_identities(monkeypatch):
    local_functions = _import_local_functions_with_stubbed_bpy(monkeypatch)

    assert local_functions._helper_namespace_fragment("A_B") == "A_B"
    assert local_functions._helper_namespace_fragment("A.B") != local_functions._helper_namespace_fragment("A_B")
    assert local_functions._logical_namespace("A.B") == "A.B"
