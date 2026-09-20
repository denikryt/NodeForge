"""Structural-semantics regressions for frontend local-function capture analysis."""

import ast

import pytest

from NodeForge.compiler_identities import BindingId, local_function_id
from NodeForge.constants import TYPE_FLOAT
from NodeForge.runtime_bindings import RuntimeBindingSymbol
from NodeForge.source_callables import analyze_local_captures

pytestmark = pytest.mark.unit


def _runtime_bindings(names):
    """Build detached runtime symbols for structural capture fixtures."""
    return {
        name: RuntimeBindingSymbol(BindingId("structural-capture-test", index), TYPE_FLOAT)
        for index, name in enumerate(names)
    }


def _captures(fn, *, runtime=(), consts=None, functions=None, labels=None):
    """Run local capture analysis with no structural arrays/builders unless requested."""
    return analyze_local_captures(
        fn,
        local_functions=dict(functions or {}),
        runtime_bindings=_runtime_bindings(runtime),
        compile_time_values=dict(consts or {}),
        reserved_name_labels=dict(labels or {}),
    )


def test_structural_allowed_compile_time_constants_are_not_hidden_captures():
    fn = ast.parse("def f(x):\n    return x * pi + tau - e\n").body[0]
    assert _captures(fn, labels={"pi": "compile-time constant", "tau": "compile-time constant", "e": "compile-time constant"}) == ()


def test_structural_same_name_runtime_capture_wins_over_compile_time():
    fn = ast.parse("def f():\n    return a\n").body[0]
    captures = _captures(fn, runtime=("a",), consts={"a": 7})
    assert [(capture.name, capture.runtime, capture.typ) for capture in captures] == [("a", True, TYPE_FLOAT)]


def test_structural_nested_local_function_calls_forward_outer_captures():
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
    captures = _captures(functions["f"], runtime=("a",), functions=functions)
    assert [capture.name for capture in captures] == ["a"]
    assert captures[0].runtime is True


def test_structural_parent_identity_is_not_normalized_by_display_spelling():
    dotted = local_function_id("A.B", "helper", "x:FLOAT")
    underscored = local_function_id("A_B", "helper", "x:FLOAT")
    assert dotted.stable_key() != underscored.stable_key()
