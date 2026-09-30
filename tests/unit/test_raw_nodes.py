"""Unit coverage for raw-node semantic vocabulary after removal of the legacy AST adapter."""

import ast

import pytest

from NodeForge.builtin_call_semantics import IR_CAPABLE_BUILTIN_NAMES, analyze_builtin_call
from NodeForge.call_resolution import NamedOutputsCallResult, RuntimeCallResult
from NodeForge.constants import (
    TYPE_BOOL, TYPE_BUNDLE, TYPE_FLOAT, TYPE_GEOMETRY, TYPE_INT,
    TYPE_MATERIAL, TYPE_OBJECT, TYPE_STRING, TYPE_TOKEN_NAMES, TYPE_VECTOR,
)
from NodeForge.errors import CompileError
from NodeForge.parsing import _collect_inputs, _parse_source

pytestmark = pytest.mark.unit


def test_type_token_names_are_authoritative_runtime_types():
    assert TYPE_TOKEN_NAMES == {
        "Float": TYPE_FLOAT,
        "Int": TYPE_INT,
        "Bool": TYPE_BOOL,
        "Vector": TYPE_VECTOR,
        "Geometry": TYPE_GEOMETRY,
        "Material": TYPE_MATERIAL,
        "Object": TYPE_OBJECT,
        "String": TYPE_STRING,
        "Bundle": TYPE_BUNDLE,
    }


@pytest.mark.parametrize(
    "source",
    [
        'Bool = 1\noutput("x", 1)',
        'Bool = 1\nBool += 1\noutput("x", Bool)',
        'for Bool in [1]:\n    x = Bool\noutput("x", 1)',
        'for x, Bool in [(1, 2)]:\n    y = x\noutput("x", 1)',
        'def Bool(x):\n    return x\noutput("x", 1)',
        'def f(Bool):\n    return Bool\noutput("x", 1)',
        'if True:\n    Geometry = 1\noutput("x", 1)',
    ],
)
def test_type_token_bindings_are_rejected(source):
    with pytest.raises(CompileError, match="Type token"):
        _parse_source(source)


def test_type_tokens_are_not_collected_as_implicit_inputs():
    stmts = _parse_source('mask = node("FunctionNodeCompare", output="Result", typ=Bool)\noutput("mask", mask)')
    assert "Bool" not in _collect_inputs(stmts, extra_builtin_names={"node"})


def _runtime_type(_node, _name, _context):
    return TYPE_FLOAT


def test_raw_node_semantics_are_owned_by_typed_builtin_analysis():
    expr = ast.parse('node("ShaderNodeValue", output="Value", typ=Float)', mode="eval").body
    result = analyze_builtin_call("node", expr, {}, _runtime_type)
    assert isinstance(result.result, RuntimeCallResult)
    assert result.result.typ is TYPE_FLOAT
    assert "node" in IR_CAPABLE_BUILTIN_NAMES


def test_raw_node_named_outputs_are_semantic_shape_not_backend_container():
    expr = ast.parse(
        'node("ShaderNodeSeparateXYZ", outputs={"X": Float, "Y": Float})',
        mode="eval",
    ).body
    result = analyze_builtin_call("node", expr, {}, _runtime_type)
    assert isinstance(result.result, NamedOutputsCallResult)
    assert result.result.items == (("X", TYPE_FLOAT), ("Y", TYPE_FLOAT))


def test_raw_node_semantics_reject_malformed_call_before_backend():
    expr = ast.parse('node("FunctionNodeCompare")', mode="eval").body
    with pytest.raises(CompileError):
        analyze_builtin_call("node", expr, {}, _runtime_type)


def test_raw_node_backend_module_has_no_source_ast_adapter():
    from NodeForge.builtins import raw_nodes

    assert not hasattr(raw_nodes, "compile_call")
    assert not hasattr(raw_nodes, "_parse_node_call")
    assert not hasattr(raw_nodes, "build_raw_node")


def test_raw_node_semantics_publish_complete_normalized_metadata_record():
    """Omitted raw-node collections become explicit normalized state before semantic lowering."""
    expr = ast.parse('node("ShaderNodeValue", output="Value", typ=Float)', mode="eval").body
    result = analyze_builtin_call("node", expr, {}, _runtime_type)
    options = dict(result.options)
    assert tuple(options) == (
        "bl_idname",
        "props",
        "inputs",
        "raw_output_mode",
        "output",
        "typ",
        "outputs",
    )
    assert options["props"] == ()
    assert options["inputs"] == ()
    assert options["raw_output_mode"] == "SINGLE_OUTPUT"
    assert options["output"] == "Value"
    assert options["typ"] is TYPE_FLOAT
    assert options["outputs"] is None


def test_raw_backend_helper_requires_complete_normalized_metadata_arguments():
    """Backend raw-node construction cannot recreate omitted source metadata containers."""
    from NodeForge.builtins.raw_nodes import build_materialized_raw_node

    with pytest.raises(TypeError, match="props"):
        build_materialized_raw_node(
            object(),
            bl_idname="ShaderNodeValue",
            inputs={},
            output="Value",
            typ=TYPE_FLOAT,
            outputs=None,
        )
    with pytest.raises(TypeError, match="inputs"):
        build_materialized_raw_node(
            object(),
            bl_idname="ShaderNodeValue",
            props={},
            output="Value",
            typ=TYPE_FLOAT,
            outputs=None,
        )
