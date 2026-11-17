"""Compiler-only inline call policy and occurrence identity regressions."""

from __future__ import annotations

import pytest

from NodeForge.compiler_identities import (
    CallSiteId, GroupCompilationIdentity, ModuleId, SourceDefinitionRef,
    inline_expansion_owner_scope, module_definition_function_id,
)
from NodeForge.errors import CompileError
from NodeForge.function_instances import (
    function_materialization_owner_scope, instance_key_for_materialization,
)
from NodeForge.resolved_environment import ResolvedEnvironment, ResolvedCatalog
from NodeForge.semantic.group import analyze_group_source
from NodeForge.semantic.ir import IRCall, IRFunctionMaterialization, IRFunctionMaterializationMode as Mode
from NodeForge.semantic.source_callable_session import SourceCallableSession


def _analyze(source):
    """Analyze one module with a real source-call session and stable owner."""
    env = ResolvedEnvironment({k: ResolvedCatalog(k, {}) for k in ('functions', 'examples', 'local')})
    session = SourceCallableSession(resolved_environment=env)
    compiled = analyze_group_source(
        source, compilation_identity=GroupCompilationIdentity(None, 'ROOT', 'ROOT', 'ROOT'),
        resolved_environment=env, source_callable_session=session,
    )
    calls = [op for statement in compiled.body.statements
             for op in getattr(getattr(statement, 'value', None), 'operations', ())
             if isinstance(op, IRCall) and op.materialization is not None]
    return calls, session


@pytest.mark.parametrize('declaration, override, expected_mode', [
    ('', '', Mode.SHARED),
    ('__inline__=True', '', Mode.INLINE),
    ('__inline__: Bool=True', '', Mode.INLINE),
    ('__inline__=False', '__inline__=True', Mode.INLINE),
    ('__inline__=True', '__inline__=False', Mode.SHARED),
    ('__inline__=True', '__inline__=False, __unique__=True', Mode.UNIQUE),
    ('', '__inline__=False, __unique__=True', Mode.UNIQUE),
])
def test_source_call_precedence_and_unique_mode(declaration, override, expected_mode):
    """A call override wins over the defining Bool and unique is still grouped."""
    decl = f', {declaration}' if declaration else ''
    opts = f', {override}' if override else ''
    calls, _ = _analyze(f'def wrapper(value: Float{decl}) -> Float:\n    return value\nresult = wrapper(3{opts})')
    assert len(calls) == 1
    assert calls[0].materialization.mode is expected_mode
    assert (calls[0].materialization.call_site is not None) == (expected_mode is not Mode.SHARED)


@pytest.mark.parametrize('override', [
    '__inline__=3', '__inline__=1.0', '__inline__=value',
    '__inline__=unknown_policy', '__inline__=True, __inline__=False',
])
def test_bad_inline_call_options_are_rejected_before_materialization(override):
    """Neither numeric coercion nor a runtime/duplicate modifier is accepted."""
    source = (f'def wrapper(value: Float) -> Float:\n    return value\n'
              f'input_value = 1\nresult = wrapper(1, {override})')
    # `value` is not in caller CT state, so it is intentionally rejected as runtime/unavailable.
    with pytest.raises(CompileError, match='__inline__'):
        _analyze(source)


@pytest.mark.parametrize('source', [
    'result = cube(1, __inline__=False)',
    'result = node("ShaderNodeValue", __inline__=False)',
    'result = nonexistent(__inline__=False)',
])
def test_inline_option_is_rejected_on_non_source_targets_even_when_false(source):
    """An explicit False cannot silently bypass reserved-keyword validation."""
    with pytest.raises(CompileError, match='__inline__'):
        _analyze(source)


def test_inline_default_and_unique_true_conflict_before_lowering():
    """Call-site unique cannot combine with a declaration's inline True."""
    with pytest.raises(CompileError, match='cannot be combined'):
        _analyze('def wrapper(value: Float, __inline__=True) -> Float:\n'
                 '    return value\nresult = wrapper(3, __unique__=True)')


def test_inline_occurrence_scopes_are_distinct_and_grouped_owner_is_stable():
    """Only expanded occurrences get fresh semantic scopes; SHARED keeps its group key."""
    calls, session = _analyze('def wrapper(value: Float, __inline__=True) -> Float:\n'
                              '    return value\na = wrapper(1)\nb = wrapper(2)\nc = wrapper(3, __inline__=False)')
    assert len(calls) == 3
    first, second, shared = (call.materialization for call in calls)
    assert first.call_site != second.call_site
    assert first.callee == second.callee == shared.callee
    scopes = {inline_expansion_owner_scope(first.call_site), inline_expansion_owner_scope(second.call_site)}
    assert len(scopes) == 2
    assert {key.owner_scope for key in session.prepared if key.owner_scope in scopes} == scopes
    assert shared.mode is Mode.SHARED
    assert shared.call_site is None
    assert function_materialization_owner_scope(shared) not in scopes
    assert instance_key_for_materialization(shared) == ''
    with pytest.raises(ValueError, match='no persisted'):
        instance_key_for_materialization(first)


def test_inline_ir_requires_matching_occurrence_identity():
    """Illegal INLINE metadata never reaches any group materializer."""
    callee = module_definition_function_id(SourceDefinitionRef(ModuleId('ROOT_TEXT', 'doc'), 'f'), '[]')
    other = module_definition_function_id(SourceDefinitionRef(ModuleId('ROOT_TEXT', 'doc'), 'g'), '[]')
    with pytest.raises(ValueError, match='requires a CallSiteId'):
        IRFunctionMaterialization(callee, Mode.INLINE)
    with pytest.raises(ValueError, match='same callee'):
        IRFunctionMaterialization(callee, Mode.INLINE, CallSiteId('ROOT', other, 0))
    call = IRFunctionMaterialization(callee, Mode.INLINE, CallSiteId('ROOT', callee, 0))
    assert inline_expansion_owner_scope(call.call_site) == inline_expansion_owner_scope(call.call_site)
    with pytest.raises(ValueError, match='no persisted'):
        function_materialization_owner_scope(call)
