"""Unified function invocation uses source parameters, not interface labels."""

import ast

import pytest

from NodeForge.compiler_identities import GroupCompilationIdentity
from NodeForge.errors import CompileError
from NodeForge.nf_types import NFType
from NodeForge.resolved_environment import ResolvedCatalog, ResolvedEnvironment
from NodeForge.semantic.callable_contracts import SourceCallableParameter, bind_unified_source_arguments
from NodeForge.semantic.group import analyze_group_source
from NodeForge.semantic.ir import IRCall, IRFunctionMaterializationMode
from NodeForge.semantic.source_callable_session import SourceCallableSession


def _parameters():
    """Return logical argument order intentionally different from socket order."""
    return (
        SourceCallableParameter(2, 'geometry', 'Shape', 'geometry', NFType.GEOMETRY, True),
        SourceCallableParameter(0, 'offset', 'Position', 'offset', NFType.VECTOR, True, True),
        SourceCallableParameter(1, 'scale', 'Factor', 'scale', NFType.FLOAT, True),
    )


def test_positional_and_keyword_binding_uses_declaration_order():
    """Keyword and positional targets are independent of final Blender panel positions."""
    result = bind_unified_source_arguments(_parameters(), ('geo',), (('scale', 3),), 'move')
    assert [(p.source_name, p.input_index, val) for p, val in result] == [
        ('geometry', 2, 'geo'), ('scale', 1, 3),
    ]


@pytest.mark.parametrize('positional, keywords, message', [
    ((), (), 'missing required arguments'),
    (('geo', 'pos', 'scale', 'extra'), (), 'too many positional'),
    (('geo',), (('Position', 'val'), ('scale', 1)), 'unknown keyword'),
    (('geo',), (('scale', 2), ('scale', 3)), 'multiple values'),
    (('geo',), ((None, 1),), 'kwargs'),
    (('geo',), (('Scale', 1),), 'unknown keyword'),
])
def test_invalid_source_calls_are_rejected_before_materialization(positional, keywords, message):
    """A physical native default never satisfies missing required DSL arguments."""
    with pytest.raises(CompileError, match=message):
        bind_unified_source_arguments(_parameters(), positional, keywords, 'move')


def _analyze(source):
    """Analyze an existing root path with new AST-based local function call binding."""
    env = ResolvedEnvironment({
        'functions': ResolvedCatalog('functions', {}),
        'examples': ResolvedCatalog('examples', {}),
        'local': ResolvedCatalog('local', {}),
    })
    session = SourceCallableSession(resolved_environment=env)
    group = analyze_group_source(source,
        compilation_identity=GroupCompilationIdentity(None, 'ROOT/binding', 'ROOT', 'ROOT'),
        resolved_environment=env, source_callable_session=session)
    return group, session


def test_optional_omitted_parameters_have_no_call_operands():
    """No operands are emitted for an optional parameter, preserving its interface default."""
    group, session = _analyze('''def f(value: Float, multiplier: Float = 2):
    return value * multiplier
x = f(3)
result = x''')
    prepared = next(iter(session.prepared.values()))
    assert [p.has_default for p in prepared.contract.parameters] == [False, True]
    operations = [op for statement in group.body.statements for op in getattr(getattr(statement, 'value', None), 'operations', ())]
    calls = [op for op in operations if isinstance(op, IRCall)]
    assert len(calls) == 1
    assert len(calls[0].static_arguments) + len(calls[0].arguments) == 1


def test_panel_reordering_has_no_effect_on_call_socket_mapping():
    """A mixed keyword call preserves the declaration-to-final-physical index map."""
    group, session = _analyze('''def f(a: Float, b: Float, c: Float = 9):
    panel([b, a], name="P")
    return a + b + c
x = f(1, b=2)
result = x''')
    prepared = next(iter(session.prepared.values()))
    assert [(p.source_name, p.input_index) for p in prepared.contract.parameters] == [('a', 2), ('b', 1), ('c', 0)]


def test_missing_required_argument_fails_before_function_body_preparation():
    """Explicitly omitted inputs are diagnosed before any function materialization."""
    with pytest.raises(CompileError, match='missing required arguments'):
        _analyze('def f(x: Float):\n    return x\nresult = f()')


def test_label_cannot_be_used_as_keyword_for_local_source_function():
    """The user-facing socket label is not a valid source keyword alias."""
    with pytest.raises(CompileError, match='unknown keyword'):
        _analyze('def f(x: Float["Rate"]):\n    return x\nresult = f(Rate=2)')
