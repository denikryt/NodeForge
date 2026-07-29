"""Unit coverage for local-function fixed tuple returns and annotations."""

import ast
import sys
import types

import pytest

from NodeForge.constants import TYPE_FLOAT, TYPE_TOKEN_NAMES
from NodeForge.errors import CompileError
from NodeForge.values import TupleValue, Value


def _local_functions(monkeypatch):
    """Import local_functions with a minimal Blender module stand-in."""
    monkeypatch.setitem(sys.modules, "bpy", types.SimpleNamespace(data=types.SimpleNamespace(node_groups={})))
    sys.modules.pop("NodeForge.local_functions", None)
    from NodeForge import local_functions
    return local_functions


def _function(source):
    """Parse and return the only function definition in source."""
    return ast.parse(source).body[0]


def test_scalar_return_shape_preserves_value_name(monkeypatch):
    module = _local_functions(monkeypatch)
    shape = module.analyze_local_return_shape(_function("def f(x):\n    return x\n"))
    assert len(shape.elements) == 1
    assert shape.elements[0].socket_name == "Value"


def test_tuple_return_names_and_order(monkeypatch):
    module = _local_functions(monkeypatch)
    fn = _function("def f(x):\n    doubled = x * 2\n    return doubled, x + 1, doubled\n")
    shape = module.analyze_local_return_shape(fn)
    assert [item.socket_name for item in shape.elements] == ["Doubled", "Value 2", "Doubled 2"]
    source = module.local_function_source(fn, {"x": TYPE_FLOAT}, return_shape=shape)
    assert source.splitlines()[-3:] == [
        "output('Doubled', doubled)",
        "output('Value 2', x + 1)",
        "output('Doubled 2', doubled)",
    ]


@pytest.mark.parametrize("body", ["return ()", "return a, (b, c)", "return [a, b]"])
def test_invalid_return_shapes_are_controlled(monkeypatch, body):
    module = _local_functions(monkeypatch)
    with pytest.raises(CompileError):
        module.analyze_local_return_shape(_function(f"def f():\n    {body}\n"))


def test_annotations_accept_registry_and_reject_complex(monkeypatch):
    module = _local_functions(monkeypatch)
    for name, typ in TYPE_TOKEN_NAMES.items():
        annotation = ast.parse(name, mode="eval").body
        assert module.resolve_local_parameter_annotation(annotation) == typ
    with pytest.raises(CompileError):
        module.resolve_local_parameter_annotation(ast.parse("list[Float]", mode="eval").body)


def test_tuple_value_indexing_and_bounds():
    values = TupleValue((Value(object(), TYPE_FLOAT), Value(object(), TYPE_FLOAT)))
    assert values.get_item(0) is values.values[0]
    assert values.get_item(-1) is values.values[1]
    with pytest.raises(CompileError, match="out of range"):
        values.get_item(2)


def test_parser_accepts_flat_unpacking_for_statement_lowering():
    from NodeForge.parsing import _parse_source

    statements = _parse_source("a, b = split(value)")
    target = statements[0].targets[0]
    assert isinstance(target, ast.Tuple)
    assert [item.id for item in target.elts] == ["a", "b"]


def test_compile_time_preprocessor_preserves_runtime_unpacking():
    from NodeForge.consteval import _preprocess_compile_time

    statements = ast.parse("a = 1\nb = 2\na, b = split(value)").body
    processed, constants = _preprocess_compile_time(statements)
    assert len(processed) == 1
    assert isinstance(processed[0].targets[0], ast.Tuple)
    assert "a" not in constants
    assert "b" not in constants
