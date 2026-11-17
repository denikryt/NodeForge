"""Canonical module definition identities, lexical imports and frozen preparation."""

import pytest
from NodeForge.compiler_identities import (
    ModuleId, SourceDefinitionRef, module_definition_function_id, GroupCompilationIdentity,
)
from NodeForge.function_instances import function_materialization_owner_scope
from NodeForge.errors import CompileError
from NodeForge.semantic.ir import IRCall
from NodeForge.resolved_environment import ResolvedEnvironment, ResolvedCatalog
from NodeForge.semantic.group import analyze_group_source
from NodeForge.semantic.source_callable_session import SourceCallableSession


def _env():
    return ResolvedEnvironment({k: ResolvedCatalog(k, {}) for k in ('functions','examples','local')})


def _analyze(source):
    env = _env()
    session = SourceCallableSession(resolved_environment=env)
    group = analyze_group_source(source,
        compilation_identity=GroupCompilationIdentity(None, 'TEST', 'TEST', 'TEST'),
        resolved_environment=env, source_callable_session=session)
    return group, session


def test_module_function_id_contains_canonical_module_and_specialization():
    first = SourceDefinitionRef(ModuleId('ROOT_TEXT', 'document-A'), 'move')
    second = SourceDefinitionRef(ModuleId('ROOT_TEXT', 'document-B'), 'move')
    a = module_definition_function_id(first, '["FLOAT"]')
    assert a != module_definition_function_id(second, '["FLOAT"]')
    assert a != module_definition_function_id(first, '["VECTOR"]')
    assert a.stable_key() == module_definition_function_id(first, '["FLOAT"]').stable_key()


def test_module_function_uses_physical_owner_and_unified_parameter_binding():
    group, session = _analyze('''def move(a: Float, b: Float = 2) -> Float:
    return a * b

first = move(3)
second = move(4, __unique__=True)
combined = first + second
''')
    assert len(session.prepared) == 2
    ids = {key.function_id for key in session.prepared}
    assert len(ids) == 1
    function_id = next(iter(ids))
    assert function_id.kind == 'MODULE_DEF'
    assert all(key.owner_scope for key in session.prepared)
    for key, prepared in session.prepared.items():
        assert prepared.contract.parameters[1].has_default
        assert key.owner_scope == prepared.group.identity.owner_scope
    calls = [op for stmt in group.body.statements
             for op in getattr(getattr(stmt, 'value', None), 'operations', ())
             if isinstance(op, IRCall)]
    assert len(calls) == 2
    assert all(call.target.function_id == function_id for call in calls)


def test_invalid_module_call_raises_without_prepared_artifact():
    with pytest.raises(CompileError, match='missing required'):
        _analyze('def f(x: Float):\n    return x\ncombined = f()')


def test_explicit_float_parameter_identity_is_independent_of_argument_types_and_defaults():
    """Both Int and Float arguments reference one declared Float owner, not inferred specializations."""
    group, session = _analyze('def scaled(value: Float = 2.0) -> Float:\n    return value\n\none = scaled()\ntwo = scaled(3)\nthree = scaled(3.5)\n')
    assert len(session.prepared) == 1
    prepared, = session.prepared.values()
    parameter, = prepared.contract.parameters
    assert parameter.typ.name == 'FLOAT'
    assert parameter.has_default
    assert prepared.group.interface.inputs[0].default == 2.0
    calls = [op for stmt in group.body.statements
             for op in getattr(getattr(stmt, 'value', None), 'operations', ())
             if isinstance(op, IRCall)]
    assert len(calls) == 3
    assert len({op.target.function_id for op in calls}) == 1


def test_unannotated_parameter_is_rejected_before_call_type_specialization():
    """Supplying an Int at a call site never fills a missing source annotation."""
    with pytest.raises(CompileError, match='requires an explicit type annotation'):
        _analyze('def f(value):\n    return value\nresult = f(3)\n')

@pytest.mark.parametrize('modifier', ('__inline__=True', '__inline__: Bool=True'))
def test_inline_declaration_does_not_affect_grouped_function_identity_or_sockets(modifier):
    """A signature control does not alter existing physical grouping identity."""
    base = 'def f(value: Float = 2.0) -> Float:\n    return value\nresult = f(5)\n'
    with_inline = base.replace('value: Float = 2.0)', f'value: Float = 2.0, {modifier})')
    regular, session1 = _analyze(base)
    controlled, session2 = _analyze(with_inline)
    key1, = session1.prepared
    key2, = session2.prepared
    assert key1.function_id == key2.function_id
    assert len(session1.prepared[key1].contract.parameters) == len(session2.prepared[key2].contract.parameters) == 1
    assert [p.source_name for p in session2.prepared[key2].contract.parameters] == ['value']
    assert [p.source_name for p in session2.prepared[key2].group.interface.inputs] == ['value']


@pytest.mark.parametrize('source', [
    'def f(value: Float, __inline__=True) -> Float:\n    return __inline__',
    'def f(value: Float, __inline__=False) -> Float:\n    return value\n',
])
def test_inline_preference_preflight_checks_unused_definitions(source):
    """A valid unused function is accepted; an illegal body reference is not."""
    if 'return __inline__' in source:
        with pytest.raises(CompileError, match='cannot read compiler-only __inline__'):
            _analyze(source)
    else:
        _analyze(source)
