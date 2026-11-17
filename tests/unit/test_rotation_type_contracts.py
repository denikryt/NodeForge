"""Public Rotation type contracts without the Blender runtime."""

import ast

import pytest

from NodeForge.errors import CompileError
from NodeForge.nf_types import NFType, deserialize_nf_type, serialize_nf_type
from NodeForge.semantic.callable_contracts import source_argument_type_matches
from NodeForge.semantic.builtin_calls import analyze_builtin_call
from NodeForge.semantic.call_resolution import NamedOutputsCallResult, RuntimeCallResult
from NodeForge.semantic.constants import OBJECT_PROPERTY_TYPES, TYPE_TOKEN_NAMES
from NodeForge.semantic.function_signatures import compile_function_signature, resolve_parameter_annotation
from NodeForge.semantic.parsing import _parse_source


def _signature(source):
    """Compile a single declaration independently of physical materialization."""
    return compile_function_signature(
        ast.parse(source).body[0], owner_scope="rotation", declaration_owner="rotation"
    )


def test_rotation_annotation_and_return_type_keep_exact_public_identity():
    """Required and labeled declarations retain the canonical runtime type."""
    assert TYPE_TOKEN_NAMES["Rotation"] is NFType.ROTATION
    signature = _signature('def echo(value: Rotation["Angle"]) -> Rotation:\n    return value')
    assert signature.logical_inputs[0].typ is NFType.ROTATION
    assert signature.logical_inputs[0].display_name == "Angle"
    assert not signature.logical_inputs[0].has_default
    assert resolve_parameter_annotation(ast.parse('Rotation["Output"]', mode="eval").body) == (NFType.ROTATION, "Output")
    assert serialize_nf_type(NFType.ROTATION) == "ROTATION"
    assert deserialize_nf_type("ROTATION") is NFType.ROTATION
    assert OBJECT_PROPERTY_TYPES["rotation"] is NFType.VECTOR


def test_raw_rotation_output_selectors_are_runtime_rotation():
    """Both raw node output forms use Rotation without a new type alias."""
    def source_type(_node, _name, _context):
        return NFType.FLOAT

    single = ast.parse('node("FunctionNodeEulerToRotation", output="Rotation", typ=Rotation)', mode="eval").body
    named = ast.parse('node("FunctionNodeEulerToRotation", outputs={"Rotation": Rotation})', mode="eval").body
    assert analyze_builtin_call("node", single, {}, source_type).result == RuntimeCallResult(NFType.ROTATION)
    result = analyze_builtin_call("node", named, {}, source_type).result
    assert isinstance(result, NamedOutputsCallResult)
    assert result.items == (("Rotation", NFType.ROTATION),)


def test_rotation_is_reserved_and_runtime_vector_is_not_rotation():
    """Public type tokens remain reserved and runtime compatibility stays strict."""
    for source in ('Rotation = 1', 'def Rotation(v: Float):\n    return v', 'def fn(Rotation: Float):\n    return Rotation'):
        with pytest.raises(CompileError):
            _parse_source(source)
    assert source_argument_type_matches(NFType.ROTATION, NFType.ROTATION)
    assert not source_argument_type_matches(NFType.ROTATION, NFType.VECTOR)
    assert not source_argument_type_matches(NFType.VECTOR, NFType.ROTATION)
    assert source_argument_type_matches(NFType.FLOAT, NFType.INT)
