"""Named result declaration and structural projection regressions."""

import ast
from dataclasses import replace

import pytest

from NodeForge.errors import CompileError
from NodeForge.nf_types import NFType
from NodeForge.result_keys import is_exact_attribute_key, validate_named_result_key
from NodeForge.semantic.callable_contracts import GroupInterfaceContract, GroupOutputContract
from NodeForge.semantic.group import analyze_group_source
from NodeForge.semantic.ir import IRBinding, IRCall, IRCallableKind, IRNamedOutputs
from NodeForge.semantic.source_callables import analyze_local_return_shape
from NodeForge.semantic.source_callable_session import SourceCallableSession
from test_source_callables import _environment, _identity, _prepare_definition_session


@pytest.mark.parametrize('key', ['value', 'Value', 'class', 'two words', 'K', 'x'*63, 'Г'*31+'x'])
def test_valid_exact_keys(key):
    """Named keys preserve exact spelling and the UTF-8 length boundary."""
    validate_named_result_key(key)
    shape = analyze_local_return_shape(ast.parse(f'def f():\n    return {{{key!r}: 1}}').body[0])
    assert shape.elements[0].result_name == shape.elements[0].socket_name == key


@pytest.mark.parametrize('key', ['', 1, 'x'*64, 'Г'*32, 'a\0b', '\ud800'])
def test_invalid_exact_keys(key):
    """Invalid named keys fail before entering graph preparation."""
    with pytest.raises(ValueError):
        validate_named_result_key(key)
    with pytest.raises(CompileError):
        analyze_local_return_shape(ast.parse(f'def f():\n    return {{{key!r}: 1}}').body[0])


@pytest.mark.parametrize('key,expected', [('value', True), ('Value', True), ('class', False),
    ('two words', False), ('K', False), ('K', True), ('x.y', False), (' x', False)])
def test_exact_attribute_spelling(key, expected):
    """Only exact Python attributes may be advertised as dot projections."""
    assert is_exact_attribute_key(key) is expected


@pytest.mark.parametrize('source', [
    'def f():\n    return {}',
    'def f(x: Float):\n    return {"x": x, "x": x}',
    'def f(x: Float):\n    return {x: x}',
    'def f(x: Float):\n    return {**x}',
    'def f(x: Float):\n    return {"x": {"y": x}}',
    'def f(x: Float):\n    return {"x": (x, x)}',
    'def f(x: Float):\n    return {"x": [x]}',
    'def f(x: Float) -> Float:\n    return {"x": x}',
    'def f(x: Float) -> Float["X"]:\n    return {"x": x}',
    'def f(x: Float):\n    if True:\n        return {"x": x}',
])
def test_invalid_named_declaration(source):
    """Only one explicit flat, unannotated final dictionary is a named return."""
    with pytest.raises(CompileError):
        analyze_local_return_shape(ast.parse(source).body[0])
    with pytest.raises(CompileError):
        _prepare_definition_session(source, 'f')


def test_named_contract_order_and_types():
    """The prepared interface retains exact names, values and declaration order."""
    prepared, _, _ = _prepare_definition_session(
        'def f(x: Float):\n    return {"first": x, "two words": x + 2, "repeat": x, "v": position()}', 'f')
    assert [(o.index, o.display_name, o.result_name, o.typ) for o in prepared.contract.outputs] == [
        (0,'first','first',NFType.FLOAT), (1,'two words','two words',NFType.FLOAT),
        (2,'repeat','repeat',NFType.FLOAT), (3,'v','v',NFType.VECTOR)]
    with pytest.raises(ValueError):
        GroupOutputContract(0, 'wrong', NFType.FLOAT, 'right')
    with pytest.raises(ValueError):
        GroupInterfaceContract((), (GroupOutputContract(0,'a',NFType.FLOAT,'a'), GroupOutputContract(1,'b',NFType.FLOAT)))


def _compile(tail):
    """Analyze a module against a real source callable session."""
    source = 'def f(x: Float):\n    return {"first": x, "two words": x+1, "K": x, "position": position()}\n' + tail
    env = _environment()
    session = SourceCallableSession(resolved_environment=env)
    return analyze_group_source(source, compilation_identity=_identity('ROOT/named','ROOT/named'),
                                resolved_environment=env, source_callable_session=session)


def test_bound_and_direct_projections_preserve_call_occurrences():
    """Bound aliases share leaves; a direct projection is a separate call."""
    compiled = _compile('r = f(2.0)\na = r\nx = r.first\ny = a["first"]\nsame = r["first"]\nz = a["two words"]\nk = r["K"]\np = r.position.x\nw = f(2.0).first\n')
    programs = [s.value for s in compiled.body.statements if hasattr(s,'value')]
    calls = [o for p in programs for o in p.operations if isinstance(o,IRCall) and o.target.kind is IRCallableKind.SOURCE_FUNCTION]
    assert len(calls) == 2
    assert calls[0].result_names == calls[1].result_names == ('first','two words','K','position')
    assert isinstance(programs[0].result, IRNamedOutputs)
    x,y,same = [next(o for o in s.value.operations if isinstance(o,IRBinding)) for s in compiled.body.statements if getattr(s,'source_name',None) in ('x','y','same')]
    # Alias bindings may be copied, but each projection still references a leaf of the same call.
    assert x.binding_id == same.binding_id
    assert x.result.typ is y.result.typ is NFType.FLOAT
    with pytest.raises(ValueError):
        replace(calls[0],result_names=('first',))


@pytest.mark.parametrize('tail', ['r = f(1.0)\nx = r.unknown', 'x = f(1.0)[0]',
    'key = index()\nx = f(1.0)[key]', 'a,b,c,d = f(1.0)', 'x = f(1.0) + 1',
    'x = f(1.0).K', 'x = f(1.0)\nx["first"] = 2.0',
    'def forward(x: Float):\n    return f(x)\ny = forward(1.0)'])
def test_whole_result_and_invalid_projection_rejected(tail):
    """A structural named result is not a general runtime dictionary or tuple."""
    with pytest.raises(CompileError):
        _compile(tail)


def test_singleton_named_result_never_collapses_and_accepts_static_key():
    """A one-entry named call has a structural carrier regardless of arity."""
    compiled = _compile('def single(x: Float):\n    return {"value": x}\nKEY = "value"\nr = single(1.0)\nx = r[KEY]\n')
    programs = [s.value for s in compiled.body.statements if hasattr(s,'value')]
    call = next(o for p in programs for o in p.operations if isinstance(o,IRCall) and o.target.kind is IRCallableKind.SOURCE_FUNCTION)
    assert call.result_names == ('value',)
    assert isinstance(next(p.result for p in programs if call in p.operations), IRNamedOutputs)
    with pytest.raises(CompileError):
        _compile('def single(x: Float):\n    return {"value": x}\ny = single(1.0) + 2.0')


def test_rebinding_does_not_redirect_an_existing_alias():
    """A new call assigned to a name does not alter earlier structural bindings."""
    compiled = _compile('r = f(1.0)\na = r\nr = f(2.0)\nx = r.first\ny = a.first\n')
    x,y = [next(o for o in s.value.operations if isinstance(o, IRBinding))
           for s in compiled.body.statements if getattr(s, 'source_name', None) in ('x','y')]
    assert x.binding_id != y.binding_id


@pytest.mark.parametrize('tail', [
    'def nested(x: Float):\n    return {"nested": f(x)}\nr = nested(1.0)',
    'r = f(1.0)\nx = sin(r)',
    'r = f(1.0)\nfor i in repeat_range(index()):\n    r = f(2.0)\nx = r.first',
])
def test_named_results_cannot_be_runtime_values_or_zone_state(tail):
    """A named result cannot cross runtime socket or zone-state boundaries whole."""
    with pytest.raises(CompileError):
        _compile(tail)


@pytest.mark.parametrize('postponed', [False, True])
def test_static_extension_mapping_annotations(tmp_path, postponed):
    """Evaluated dictionary annotations normalize to immutable ordered exact markers."""
    from test_extension_dynamic_results import _owner, _dynamic_semantic_environment, _INTERFACE
    from NodeForge.semantic.analysis import analyze_expression
    from NodeForge.semantic.lowering import lower_analyzed_expression
    interface = _INTERFACE.replace('-> DynamicResults:', '-> {"first": Float, "Two Words": Float}:')
    if postponed:
        interface = 'from __future__ import annotations\n' + interface
    owner = _owner(tmp_path, interface, backend='def foo(context, value): return {"Two Words": value, "first": value}')
    env, cid = _dynamic_semantic_environment(owner)
    spec = owner.normalize_interface()[0][cid][0]
    assert spec.result.kind == 'NAMED_RESULTS'
    assert spec.result.named_items == (('first', NFType.FLOAT), ('Two Words', NFType.FLOAT))
    expr = ast.parse('foo(f)["Two Words"]',mode='eval').body
    analysis = analyze_expression(expr, env)
    program = lower_analyzed_expression(expr, analysis)
    call = next(op for op in program.operations if isinstance(op, IRCall))
    assert call.result_names == ('first', 'Two Words')
    assert program.result == call.results[1]


@pytest.mark.parametrize('annotation', ['{}', '{"": Float}', '{1: Float}', '{"x": Float | Int}',
    '{"x": {"y": Float}}', '{"x": 1}', '{"x" * 64: Float}'])
def test_invalid_static_extension_named_annotation(tmp_path, annotation):
    """Invalid mappings cannot enter a normalized executable contract."""
    from test_extension_dynamic_results import _owner, _INTERFACE
    interface = _INTERFACE.replace('Float, DynamicResults', 'Float, Int, DynamicResults').replace('-> DynamicResults:', f'-> {annotation}:')
    owner = _owner(tmp_path, interface, backend='def foo(context, value): return value')
    with pytest.raises(CompileError):
        owner.normalize_interface()


def test_named_type_spec_cannot_be_nested_or_parameter(tmp_path):
    """Named executable schemas do not participate in semantic-state or parameter types."""
    import inspect
    from NodeForge.extensions.contracts import TypeSpec, ExtensionParameterSpec
    spec = TypeSpec('NAMED_RESULTS', named_items=(('x', NFType.FLOAT),))
    with pytest.raises(ValueError):
        TypeSpec('LIST', item=spec)
    with pytest.raises(TypeError):
        ExtensionParameterSpec('x', inspect.Parameter.POSITIONAL_OR_KEYWORD, spec, None)
    with pytest.raises(ValueError):
        TypeSpec('NAMED_RESULTS', named_items=(('x', NFType.FLOAT), ('x', NFType.INT)))


def test_dynamic_extension_can_change_only_names(tmp_path):
    """Different keys of equal types remain distinct resolved contracts."""
    from test_extension_dynamic_results import _owner, _dynamic_semantic_environment, _DYNAMIC_INTERFACE
    from NodeForge.semantic.analysis import analyze_expression
    from NodeForge.semantic.lowering import lower_analyzed_expression
    owner = _owner(tmp_path, _DYNAMIC_INTERFACE, backend='def foo(context, *args, **kwargs): pass',
        semantic='from NodeForge.nf_types import NFType\ndef _result_types_foo(args): return {args["domain"].value: NFType.INT}')
    env, _ = _dynamic_semantic_environment(owner)
    for key in ('POINT', 'FACE'):
        expr = ast.parse(f'foo(geo, domain={key!r})[{key!r}]', mode='eval').body
        analysis = analyze_expression(expr, env)
        call = next(op for op in lower_analyzed_expression(expr, analysis).operations if isinstance(op, IRCall))
        assert call.result_names == (key,)
        assert call.results[0].typ is NFType.INT
