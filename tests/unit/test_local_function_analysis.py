"""Unit checks for frontend-owned local-function capture analysis."""

import ast
from pathlib import Path

import pytest

from NodeForge.compiler_identities import BindingId, local_function_id
from NodeForge.constants import TYPE_FLOAT
from NodeForge.runtime_bindings import RuntimeBindingSymbol
from NodeForge.source_callables import analyze_local_captures

pytestmark = pytest.mark.unit


def _runtime_bindings(names):
    """Build detached frontend runtime symbols for capture fixtures."""
    return {
        name: RuntimeBindingSymbol(BindingId("capture-test", index), TYPE_FLOAT)
        for index, name in enumerate(names)
    }


def _captures(fn, *, runtime=(), consts=None, functions=None, labels=None):
    """Run the permanent frontend capture analyzer with detached semantic state."""
    return analyze_local_captures(
        fn,
        local_functions=dict(functions or {}),
        runtime_bindings=_runtime_bindings(runtime),
        compile_time_values=dict(consts or {}),
        reserved_name_labels=dict(labels or {}),
    )


def test_local_function_allowed_compile_time_constants_are_not_hidden_captures():
    fn = ast.parse("def f(x):\n    return x * pi + tau - e\n").body[0]
    assert _captures(fn, labels={"pi": "compile-time constant", "tau": "compile-time constant", "e": "compile-time constant"}) == ()


def test_same_name_runtime_capture_wins_over_compile_time():
    """Runtime binding metadata remains authoritative when CTFE has the same name."""
    fn = ast.parse("def f():\n    return a\n").body[0]
    captures = _captures(fn, runtime=("a",), consts={"a": 7})
    assert len(captures) == 1
    assert captures[0].name == "a"
    assert captures[0].runtime is True
    assert captures[0].typ is TYPE_FLOAT


def test_local_function_nested_local_function_calls_forward_outer_captures():
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


def test_local_function_definition_owner_identity_is_non_lossy():
    """Distinct parent owner strings remain distinct in canonical local FunctionId identity."""
    dotted = local_function_id("A.B", "helper", "x:FLOAT")
    underscored = local_function_id("A_B", "helper", "x:FLOAT")
    assert dotted != underscored
    assert dotted.stable_key() != underscored.stable_key()


def test_local_call_hands_physical_materialization_to_function_materializer():
    """Frontend source-call analysis and physical local-helper realization stay separate."""
    root = Path(__file__).resolve().parents[2]
    semantic_source = (root / "semantic_analysis.py").read_text(encoding="utf-8")
    lowering_source = (root / "blender_ir_lowering.py").read_text(encoding="utf-8")
    physical_source = (root / "local_functions.py").read_text(encoding="utf-8")

    assert "session.prepare_local(" in semantic_source
    assert "IRFunctionMaterialization(" in semantic_source
    assert "build_prepared_local_materialization_spec" in lowering_source
    assert ".materialize_local(" in lowering_source
    assert "def compile_local_function_call" not in physical_source
    assert "resolve_reusable_function_materialization" not in physical_source
