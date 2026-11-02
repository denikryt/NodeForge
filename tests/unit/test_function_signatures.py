"""Typed function signature/source socket regression contracts for issue #48."""

import ast

import pytest

from NodeForge.compiler_identities import InputDeclarationId, BindingId
from NodeForge.errors import CompileError
from NodeForge.nf_types import NFType
from NodeForge.semantic.function_signatures import compile_function_signature
from NodeForge.semantic.ir import IRInputDeclaration


def _signature(source: str, **kwargs):
    """Compile the first function declaration with stable test owner identity."""
    fn = ast.parse(source).body[0]
    return compile_function_signature(fn, owner_scope='owner', declaration_owner='definition', **kwargs)


@pytest.mark.parametrize('annotation, expected', [
    ('Float', NFType.FLOAT), ('Int', NFType.INT), ('Bool', NFType.BOOL),
    ('Vector', NFType.VECTOR), ('String', NFType.STRING),
    ('Geometry', NFType.GEOMETRY), ('Material', NFType.MATERIAL),
    ('Object', NFType.OBJECT), ('Bundle', NFType.BUNDLE),
])
def test_supported_typed_required_parameters_have_no_declared_default(annotation, expected):
    """Every supported type can be required without canonicalizing scalar None."""
    signature = _signature(f'def fn(value: {annotation}):\n    return value')
    item = signature.logical_inputs[0]
    assert item.source_name == 'value'
    assert item.typ is expected
    assert item.default is None
    assert item.has_default is False
    assert signature.declarations[0].has_default is False
    assert signature.declarations[0].default is None
    assert signature.declarations[0].declaration_id == InputDeclarationId('definition', 'value', 0)


@pytest.mark.parametrize('annotation, value, expected_type, expected_value', [
    ('Float', '2', NFType.FLOAT, 2.0),
    ('Int', '2', NFType.INT, 2),
    ('Bool', 'True', NFType.BOOL, True),
    ('String', '"text"', NFType.STRING, 'text'),
    ('Vector', 'vector(1, 2, 3)', NFType.VECTOR, (1.0, 2.0, 3.0)),
])
def test_typed_defaults_are_canonical_compile_time_values(annotation, value, expected_type, expected_value):
    """Declared '=' survives separately from native Blender unlinked values."""
    signature = _signature(f'def fn(value: {annotation} = {value}):\n    return value')
    item = signature.logical_inputs[0]
    assert item.has_default
    assert item.typ is expected_type
    assert item.default == expected_value
    assert signature.declarations[0].default == expected_value
    assert signature.declarations[0].has_default


def test_positional_argument_order_is_independent_of_panel_socket_projection():
    """Input origin and declaration order survive panel moves and display labels."""
    signature = _signature('''def fn(a: Float["Width"], b: Vector, c: Int = 2):
    panel([c, a], name="Dimensions", collapsed=True)
    return a
''')
    assert [item.source_name for item in signature.logical_inputs] == ['a', 'b', 'c']
    assert [item.source_name for item in signature.physical_inputs] == ['b', 'c', 'a']
    assert [item.display_name for item in signature.logical_inputs] == ['Width', 'b', 'c']
    assert signature.panels[0].collapsed is True
    assert signature.panels[0].member_origins == (
        InputDeclarationId('definition', 'c', 0), InputDeclarationId('definition', 'a', 0)
    )
    assert [record.target_binding_id for record in signature.declarations] == [
        BindingId('owner', 0), BindingId('owner', 1), BindingId('owner', 2)
    ]


def test_stable_origin_is_not_based_on_label_or_physical_order():
    """Renaming a display label or changing a panel never renames a socket ID."""
    first = _signature('''def fn(first: Float["A"], second: Float):
    panel([first], name="Data")
    return first''')
    second = _signature('''def fn(first: Float["B"], second: Float):
    panel([second, first], name="Other")
    return first''')
    assert [item.interface_origin for item in first.logical_inputs] == [
        item.interface_origin for item in second.logical_inputs
    ]
    assert [item.display_name for item in first.logical_inputs] != [
        item.display_name for item in second.logical_inputs
    ]


@pytest.mark.parametrize('annotation', [
    'Color', 'float', 'Float[""]', 'Float["  "]', 'Vector[1]',
    'Float["X", "Y"]', 'Float[prefix]', 'dict[str, str]',
])
def test_invalid_annotations_and_labels_fail_before_materialization(annotation):
    """Invalid source annotations cannot reach Blender interface construction."""
    with pytest.raises(CompileError):
        _signature(f'def fn(value: {annotation}):\n    return value')


@pytest.mark.parametrize('signature, expected', [
    ('value: Float = "text"', 'Float'),
    ('value: Int = 2.5', 'Int'),
    ('value: Geometry = 1', 'GEOMETRY'),
    ('value: Float = unknown', 'compile-time default'),
    ('value: Bool = 0', 'Bool'),
])
def test_mismatched_or_unavailable_parameter_defaults_fail(signature, expected):
    """No incorrect default is accepted as a native socket fallback."""
    with pytest.raises(CompileError, match=expected):
        _signature(f'def fn({signature}):\n    return value')


@pytest.mark.parametrize('signature', [
    '__unique__: Float', '*args: Float', '**kwargs: Float',
    'value: Float, *, extra: Float', 'value: Float, /, extra: Float',
])
def test_unsupported_signature_shapes_fail_at_signature_boundary(signature):
    """Compiler control options and Python variadics are not public socket parameters."""
    with pytest.raises(CompileError):
        _signature(f'def fn({signature}):\n    return 1')


@pytest.mark.parametrize('body', [
    'value = 2\n    panel([value], name="Data")',
    'panel([missing], name="Data")',
    'panel([value, value], name="Data")',
    'panel([value], name="Data")\n    panel([value], name="Data")',
    'if True:\n        panel([value], name="Data")',
    'panel([value], name="Data", collapsed=1)',
])
def test_invalid_panels_fail_before_body_lowering(body):
    """Only valid prefix panel declarations may reparent input origins."""
    with pytest.raises(CompileError):
        _signature(f'def fn(value: Float):\n    {body}\n    return value')


def test_unannotated_default_or_explicit_inference_is_supported():
    """Type facts from a default or unique call specialization can type a parameter."""
    assert _signature('def fn(value=3):\n    return value').logical_inputs[0].typ is NFType.INT
    required = _signature('def fn(value):\n    return value', inferred_types={'value': NFType.VECTOR})
    assert required.logical_inputs[0].typ is NFType.VECTOR
    assert not required.logical_inputs[0].has_default


def test_unannotated_uninferrable_parameter_does_not_guess_float():
    """An unknown required parameter cannot acquire an arbitrary Float default."""
    with pytest.raises(CompileError, match='cannot infer type'):
        _signature('def fn(value):\n    return value')


def test_ir_input_declaration_rejects_absent_default_with_value():
    """Typed IR enforces presence independently of its stored value."""
    with pytest.raises(ValueError, match='Required input'):
        IRInputDeclaration(
            BindingId('scope', 0), InputDeclarationId('def', 'x', 0),
            'x', 'X', NFType.FLOAT, 2.0, False,
        )


def test_repeated_panel_move_uses_last_declared_parent():
    """Existing physical panel projection permits explicitly reparenting an input."""
    signature = _signature('''def fn(value: Float, other: Float):
    panel([value], name="First")
    panel([other, value], name="Second")
    return value''')
    assert [item.source_name for item in signature.physical_inputs] == ['other', 'value']
    assert [item.source_name for item in signature.logical_inputs] == ['value', 'other']


@pytest.mark.parametrize('name', ['cube', 'Float', 'output', 'pi', 'panel'])
def test_signature_rejects_reserved_parameter_bindings(name):
    """A function parameter must not replace a builtin, type or compile-time token."""
    with pytest.raises(CompileError, match='reserved source name'):
        _signature(f'def fn({name}: Float):\n    return {name}')


def test_ir_optional_input_must_have_a_supported_real_default():
    """An optional input may not hide missing or unsupported defaults."""
    with pytest.raises(ValueError, match='needs a declared default'):
        IRInputDeclaration(
            BindingId('scope', 0), InputDeclarationId('def', 'x', 0),
            'x', 'X', NFType.FLOAT, None, True,
        )
    with pytest.raises(ValueError, match='does not support'):
        IRInputDeclaration(
            BindingId('scope', 0), InputDeclarationId('def', 'geo', 0),
            'geo', 'Geometry', NFType.GEOMETRY, 2.0, True,
        )
