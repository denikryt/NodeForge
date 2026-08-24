import ast

import pytest

from NodeForge.constants import TYPE_BOOL, TYPE_BUNDLE, TYPE_FLOAT, TYPE_GEOMETRY, TYPE_INT, TYPE_MATERIAL, TYPE_OBJECT, TYPE_STRING, TYPE_TOKEN_NAMES, TYPE_VECTOR
from NodeForge.errors import CompileError
from NodeForge.parsing import _collect_inputs, _parse_source
from NodeForge.values import NodeResult, Value

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
        "Bool = 1\noutput(\"x\", 1)",
        "Bool = 1\nBool += 1\noutput(\"x\", Bool)",
        "for Bool in [1]:\n    x = Bool\noutput(\"x\", 1)",
        "for x, Bool in [(1, 2)]:\n    y = x\noutput(\"x\", 1)",
        "def Bool(x):\n    return x\noutput(\"x\", 1)",
        "def f(Bool):\n    return Bool\noutput(\"x\", 1)",
        "if True:\n    Geometry = 1\noutput(\"x\", 1)",
    ],
)
def test_type_token_bindings_are_rejected(source):
    with pytest.raises(CompileError, match="Type token"):
        _parse_source(source)


def test_type_tokens_are_not_collected_as_implicit_inputs():
    stmts = _parse_source('mask = node("FunctionNodeCompare", output="Result", typ=Bool)\noutput("mask", mask)')
    assert "Bool" not in _collect_inputs(stmts, extra_builtin_names={"node"})


def test_node_result_lookup_and_compile_time_failure():
    result = NodeResult({"Geometry": Value(object(), TYPE_GEOMETRY), "Socket Name": Value(object(), TYPE_FLOAT)})
    assert result.get_output("Geometry").typ == TYPE_GEOMETRY
    assert result.get_output("Socket Name").typ == TYPE_FLOAT
    with pytest.raises(CompileError, match="Unknown raw node output"):
        result.get_output("Missing")
    with pytest.raises(CompileError, match="compile-time only"):
        _ = result.typ
    with pytest.raises(CompileError, match="compile-time only"):
        _ = result.socket


def test_raw_node_parser_rejects_malformed_call_without_blender():
    from NodeForge.builtins import raw_nodes

    class DummyComp:
        consts = {}

    malformed = [
        'node("FunctionNodeCompare")',
        'node("FunctionNodeCompare", output="Result")',
        'node("FunctionNodeCompare", typ=Bool)',
        'node("FunctionNodeCompare", output="Result", typ=1)',
        'node("FunctionNodeCompare", output="Result", typ=Bool, outputs={"Result": Bool})',
        'node("FunctionNodeCompare", output="Result", typ=Bool, props=object())',
        'node("FunctionNodeCompare", output="Result", typ=Bool, inputs={"A": []})',
        'node("FunctionNodeCompare", output="Result", typ=Bool, extra=1)',
    ]
    for source in malformed:
        expr = ast.parse(source, mode="eval").body
        with pytest.raises(CompileError):
            raw_nodes._parse_node_call(DummyComp(), expr)


def test_raw_node_parser_accepts_type_tokens_without_comp_vars():
    from NodeForge.builtins import raw_nodes

    class DummyComp:
        consts = {}
        vars = {"Bool": Value(object(), TYPE_FLOAT)}

    expr = ast.parse('node("FunctionNodeCompare", output="Result", typ=Bool, outputs=None)', mode="eval").body
    with pytest.raises(CompileError):
        raw_nodes._parse_node_call(DummyComp(), expr)

    expr = ast.parse('node("FunctionNodeCompare", output="Result", typ=Bool)', mode="eval").body
    parsed = raw_nodes._parse_node_call(DummyComp(), expr)
    assert parsed["typ"] == TYPE_BOOL


def test_compare_wrappers_do_not_register_public_helpers():
    from NodeForge.builtins import node_wrappers

    assert node_wrappers.NAMES == set()
    assert not hasattr(node_wrappers, "_compare_data_type")
