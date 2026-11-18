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

@pytest.mark.parametrize('value, expected', [
    ('(0, -1, 7)', (0.0, -1.0, 7.0)),
    ('[0, 1, 2]', (0.0, 1.0, 2.0)),
    ('(0.1, -0.2, 7.5)', (0.1, -0.2, 7.5)),
])
def test_rotation_source_defaults_store_canonical_xyz_and_declaration_identity(value, expected):
    """A declared optional Rotation retains one binary32 triple across IR/contract."""
    from NodeForge.semantic.numeric_semantics import normalize_float_constant

    signature = _signature(f'def echo(value: Rotation = {value}) -> Rotation:\n    return value')
    actual = tuple(normalize_float_constant(v) for v in expected)
    assert signature.logical_inputs[0].default == actual
    assert signature.declarations[0].default == actual
    assert type(signature.declarations[0].default) is tuple
    assert signature.logical_inputs[0].has_default
    assert signature.logical_inputs[0].interface_origin == signature.declarations[0].declaration_id


@pytest.mark.parametrize('value, expected_fragment', [
    ('(1, 2)', 'exactly three'), ('(1, 2, 3, 4)', 'exactly three'),
    ('(1, True, 3)', 'component 1'), ('(1, "x", 3)', 'component 1'),
    ('(2147483648, 0, 0)', 'Rotation default'),
    ('(1e50, 0, 0)', 'Rotation default'),
    ('(missing, 0, 0)', 'Rotation default'),
])
def test_rotation_source_defaults_reject_bad_values_with_parameter_context(value, expected_fragment):
    """Both normalizer and earlier constant evaluator errors identify the owner."""
    with pytest.raises(CompileError) as exc:
        _signature(f'def echo(value: Rotation = {value}) -> Rotation:\n    return value')
    assert 'value' in str(exc.value)
    assert expected_fragment in str(exc.value)


def test_rotation_source_default_cannot_reference_runtime_parameter():
    """Earlier parameter-resolution guard remains authoritative for defaults."""
    with pytest.raises(CompileError, match='runtime parameter'):
        _signature('def echo(value: Rotation, other: Rotation = value) -> Rotation:\n    return other')


@pytest.mark.parametrize('value', [
    (float('nan'), 0, 0), (0, float('inf'), 0), (0, 0, 1e50),
    (0, True, 0), (0, '2', 0), (0, 0), (1, 2, 3, 4),
])
def test_rotation_detached_default_rejects_invalid_shape_or_components(value):
    """Shared normalizer rejects malformed detached Extension and source values."""
    from NodeForge.semantic.callable_contracts import canonicalize_group_input_default

    with pytest.raises(CompileError, match='Rotation default'):
        canonicalize_group_input_default(NFType.ROTATION, value)


def test_required_rotation_default_guard_remains_strict():
    """Unspecified default cannot turn a required Rotation into optional input."""
    from NodeForge.semantic.callable_contracts import GroupInputContract

    required = GroupInputContract('value', 'Value', NFType.ROTATION, None, False, None)
    assert required.default is None
    assert not required.has_default
    with pytest.raises(ValueError):
        GroupInputContract('value', 'Value', NFType.ROTATION, (0., 0., 0.), False, None)


def test_rotation_public_token_does_not_expand_other_operation_capabilities():
    """Typed Rotation sockets remain distinct from operation-specific supported types."""
    from NodeForge.type_capabilities import (
        SWITCH_INPUT_TYPES, REPEAT_ITEM_TYPES, CAPTURE_ITEM_TYPES, ATTRIBUTE_DATA_TYPES,
    )

    for capability in (SWITCH_INPUT_TYPES, REPEAT_ITEM_TYPES,
                       CAPTURE_ITEM_TYPES, ATTRIBUTE_DATA_TYPES):
        assert NFType.ROTATION not in capability
        assert NFType.VECTOR in capability
    assert NFType.COLOR in SWITCH_INPUT_TYPES
    assert NFType.COLOR in ATTRIBUTE_DATA_TYPES
