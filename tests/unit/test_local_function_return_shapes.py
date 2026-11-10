"""Unit coverage for active local-function returns and serialized identities."""

import ast
import sys
import types

import pytest

from NodeForge.errors import CompileError
from NodeForge.nf_types import NFType


def _source_callables():
    """Return the Blender-independent source-call semantic helper module."""
    from NodeForge.semantic import source_callables
    return source_callables


def _function(source):
    """Parse and return the only function definition in source."""
    return ast.parse(source).body[0]


def test_scalar_return_shape_uses_returned_variable_name():
    module = _source_callables()
    shape = module.analyze_local_return_shape(_function("def f(x):\n    return x\n"))
    assert len(shape.elements) == 1
    assert shape.elements[0].socket_name == "X"


def test_tuple_return_names_and_order():
    module = _source_callables()
    fn = _function("def f(x):\n    doubled = x * 2\n    return doubled, x + 1, doubled\n")
    shape = module.analyze_local_return_shape(fn)
    assert [item.socket_name for item in shape.elements] == ["Doubled", "Value 2", "Doubled 2"]


@pytest.mark.parametrize("body", ["return ()", "return a, (b, c)", "return [a, b]"])
def test_invalid_return_shapes_are_controlled(body):
    module = _source_callables()
    with pytest.raises(CompileError):
        module.analyze_local_return_shape(_function(f"def f():\n    {body}\n"))


def test_parser_accepts_flat_unpacking_for_statement_lowering():
    from NodeForge.semantic.parsing import _parse_source

    statements = _parse_source("a, b = split(value)")
    target = statements[0].targets[0]
    assert isinstance(target, ast.Tuple)
    assert [item.id for item in target.elts] == ["a", "b"]


def test_compile_time_preprocessor_preserves_runtime_unpacking():
    from NodeForge.semantic.residualization import _preprocess_compile_time

    statements = ast.parse("a = 1\nb = 2\na, b = split(value)").body
    preprocessed = _preprocess_compile_time(statements)
    assert len(preprocessed.statements) == 1
    assert isinstance(preprocessed.statements[0].targets[0], ast.Tuple)
    assert "a" not in preprocessed.final_compile_time.values
    assert "b" not in preprocessed.final_compile_time.values


def test_return_shape_metadata_serializes_historical_type_tokens(monkeypatch):
    """Local helper return metadata stays byte-for-byte compatible."""
    module = _source_callables()
    shape = module.analyze_local_return_shape(_function("def f(x):\n    return x\n"))
    monkeypatch.setitem(sys.modules, "bpy", types.SimpleNamespace(data=types.SimpleNamespace(node_groups={})))
    sys.modules.pop("NodeForge.blender.local_functions", None)
    from NodeForge.blender import local_functions
    assert local_functions._serialize_return_shape(shape, (NFType.FLOAT,)) == '[{"key":"return:0","name":"X","type":"FLOAT"}]'


def test_local_signature_serialization_preserves_historical_identity():
    """Canonical in-memory types retain the exact local helper signature bytes."""
    module = _source_callables()
    assert module.serialize_local_signature(("a", "b"), {"a": NFType.FLOAT, "b": NFType.VECTOR}) == "a:FLOAT,b:VECTOR"


@pytest.mark.parametrize("label", ["X" * 64, "Г" * 32])
def test_scalar_explicit_output_label_above_blender_socket_limit_is_rejected(label):
    module = _source_callables()
    fn = _function("def f():\n    return 1.0\n")
    with pytest.raises(CompileError, match="63-byte UTF-8"):
        module.analyze_local_return_shape(fn, output_label=label)


def test_output_label_limit_is_utf8_bytes_not_characters():
    module = _source_callables()
    fn = _function("def f():\n    return 1.0\n")
    assert module.analyze_local_return_shape(fn, output_label="X" * 63).elements[0].socket_name == "X" * 63
    assert module.analyze_local_return_shape(fn, output_label="Г" * 31).elements[0].socket_name == "Г" * 31


def test_automatic_output_label_above_blender_socket_limit_falls_back_to_value():
    module = _source_callables()
    identifier = "many_very_long_words_in_a_single_result_variable_that_exceeds_socket_capacity"
    fn = _function(f"def f():\n    return {identifier}\n")
    shape = module.analyze_local_return_shape(fn)
    assert shape.elements[0].socket_name == "Value"


def test_suffixing_a_duplicate_tuple_output_must_obey_same_limit():
    module = _source_callables()
    identifier = "x" * 62
    fn = _function(f"def f():\n    return {identifier}, {identifier}\n")
    with pytest.raises(CompileError, match="63-byte UTF-8"):
        module.analyze_local_return_shape(fn)
