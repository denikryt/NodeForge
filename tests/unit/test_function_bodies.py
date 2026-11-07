"""FunctionDef body compilation through the native typed Semantic IR engine."""

import ast

import pytest

from NodeForge.compiler_identities import GroupCompilationIdentity, InputDeclarationId
from NodeForge.errors import CompileError
from NodeForge.nf_types import NFType
from NodeForge.resolved_environment import ResolvedCatalog, ResolvedEnvironment
from NodeForge.semantic.call_resolution import CallableEnvironment
from NodeForge.semantic.builtin_calls import INPUT_DECLARATION_BUILTIN_NAMES, IR_CAPABLE_BUILTIN_NAMES
from NodeForge.semantic.function_bodies import compile_function_definition
from NodeForge.semantic.ir import IRInputDeclaration, IROutput, IRPanelDeclaration


def _compile(source, *, constants=None):
    """Compile one AST definition with a detached empty source environment."""
    fn = ast.parse(source).body[0]
    env = ResolvedEnvironment({
        'functions': ResolvedCatalog('functions', {}),
        'examples': ResolvedCatalog('examples', {}),
        'local': ResolvedCatalog('local', {}),
    })
    return compile_function_definition(
        fn, compilation_identity=GroupCompilationIdentity(None, 'FUNCTION/owner', 'module', 'decl'),
        callable_environment=CallableEnvironment(frozenset(IR_CAPABLE_BUILTIN_NAMES | INPUT_DECLARATION_BUILTIN_NAMES), {}, {}),
        resolved_environment=env,
        compile_time_values=constants,
    )


def test_scalar_function_compiles_direct_original_ast_and_typed_output():
    """No generated input_*/output source is reparsed to define the group I/O."""
    group = _compile('''def resize(value: Float, scale: Float = 2) -> Float:
    amount = value * scale
    return amount''')
    assert not group.geometry_mode
    assert [item.source_name for item in group.interface.inputs] == ['value', 'scale']
    assert [item.has_default for item in group.interface.inputs] == [False, True]
    assert [item.display_name for item in group.interface.outputs] == ['Value']
    assert [item.typ for item in group.interface.outputs] == [NFType.FLOAT]
    assert group.body.statements[0].declaration_id == InputDeclarationId('decl', 'value', 0)
    assert isinstance(group.body.statements[0], IRInputDeclaration)
    assert isinstance(group.body.statements[-1], IROutput)
    assert group.source.startswith('def resize(')
    assert 'input_float' not in group.source
    assert 'output(' not in group.source


def test_geometry_return_has_no_implicit_geometry_group_interface():
    """Geometry operations in functions do not introduce implicit root sockets."""
    group = _compile('''def moving(geo: Geometry, shift: Vector) -> Geometry:
    return transform(geo, translation=shift)''')
    assert len(group.interface.inputs) == 2
    assert [x.typ for x in group.interface.inputs] == [NFType.GEOMETRY, NFType.VECTOR]
    assert len(group.interface.outputs) == 1
    assert group.interface.outputs[0].typ is NFType.GEOMETRY
    assert not group.geometry_mode


def test_multiple_outputs_follow_final_flat_tuple_order():
    """Return-name mapping is deterministic without synthesized output() calls."""
    group = _compile('''def split(x: Float, y: Int):
    return y, x, y''')
    assert [x.display_name for x in group.interface.outputs] == ['Y', 'X', 'Y 2']
    assert [x.typ for x in group.interface.outputs] == [NFType.INT, NFType.FLOAT, NFType.INT]
    assert sum(isinstance(node, IROutput) for node in group.body.statements) == 3


def test_panels_preserve_parameters_and_output_types_through_body_lowering():
    """Typed initial runtime parameter bindings use stable panel origins."""
    group = _compile('''def fn(a: Float["First"], b: Float, c: Int):
    panel([c, a], name="Inputs")
    return b''')
    assert [x.source_name for x in group.interface.inputs] == ['b', 'c', 'a']
    assert [x.interface_origin for x in group.interface.inputs] == [
        InputDeclarationId('decl', 'b', 0), InputDeclarationId('decl', 'c', 0), InputDeclarationId('decl', 'a', 0),
    ]
    assert isinstance(group.body.statements[3], IRPanelDeclaration)


def test_preprocessed_compile_time_assignment_is_replayed_for_return():
    """Compile-time residualization never turns a return into a root output declaration."""
    group = _compile('''def answer() -> Int:
    local_constant = 3
    return local_constant''')
    assert not group.interface.inputs
    assert [x.typ for x in group.interface.outputs] == [NFType.INT]
    assert group.body.statements[-1].value.result.typ is NFType.INT


@pytest.mark.parametrize('source, expected', [
    ('def fn(x: Float):\n    x = x + 1', 'final top-level return'),
    ('def fn(x: Float):\n    return x\n    x = 2', 'final top-level return'),
    ('def fn(x: Float):\n    return x, (x, x)', 'nested tuple'),
    ('def fn(x: Float):\n    return []', 'list'),
    ('def fn(x: Float) -> Float:\n    return True', 'returns'),
    ('def fn(x: Float) -> Float:\n    return x, x', 'scalar return annotation'),
    ('def fn(x: Float):\n    return input_float("A")', 'obsolete group interface'),
    ('def fn(x: Float):\n    output(x)\n    return x', 'obsolete group interface'),
])
def test_invalid_return_or_legacy_interface_fails_before_blender(source, expected):
    """Invalid function contracts fail at semantic preparation, without publication."""
    with pytest.raises(CompileError, match=expected):
        _compile(source)


def test_singleton_tuple_return_remains_a_valid_single_value():
    """A flat nonempty tuple of one element is an ordinary valid return."""
    group = _compile('def fn(x: Float):\n    return (x,)')
    assert [item.typ for item in group.interface.outputs] == [NFType.FLOAT]
    assert [item.display_name for item in group.interface.outputs] == ['Value']


@pytest.mark.parametrize('call_name', ['range', 'repeat_range'])
def test_explicit_float_loop_count_remains_float_and_is_rejected(call_name):
    """An Int-only runtime loop cannot respecialize a declared Float parameter."""
    with pytest.raises(CompileError, match='Int'):
        _compile(f"""def fn(count: Float):
    value = 0.0
    for i in {call_name}(count):
        value = value + 1.0
    return value
""")
