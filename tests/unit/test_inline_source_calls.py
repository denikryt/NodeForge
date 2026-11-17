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


@pytest.mark.parametrize('nested_mode, expected_count', [
    ('__unique__=True', 2), ('', 1),
])
def test_inline_occurrences_prepare_distinct_nested_call_owners(nested_mode, expected_count):
    """Unique child scopes follow their inlined parent, while shared children reuse."""
    kw = f', {nested_mode}' if nested_mode else ''
    calls, session = _analyze(
        'def leaf(value: Float) -> Float:\n    return value\n'
        'def outer(value: Float, __inline__=True) -> Float:\n'
        f'    return leaf(value{kw})\n'
        'first = outer(2.0)\nsecond = outer(3.0)\n'
    )
    outer_keys = [key for key in session.prepared if key.function_id.name == 'outer']
    leaf_keys = [key for key in session.prepared if key.function_id.name == 'leaf']
    assert len(outer_keys) == 2
    assert len(leaf_keys) == expected_count
    assert len({key.owner_scope for key in leaf_keys}) == expected_count
    for call in calls:
        assert session.get_inline_prepared(call.materialization).contract.function_id == call.materialization.callee
    with pytest.raises(CompileError, match='requires an INLINE source call'):
        session.get_inline_prepared(IRFunctionMaterialization(
            calls[0].materialization.callee, Mode.SHARED,
        ))


@pytest.mark.parametrize('source', [
    'def a(x: Float, __inline__=True) -> Float:\n    return a(x)\nv = a(3)',
    'def a(x: Float, __inline__=True) -> Float:\n    return b(x, __inline__=False)\n'
    'def b(x: Float) -> Float:\n    return a(x)\nv = a(3)',
])
def test_mixed_grouped_inline_recursive_cycle_is_rejected_before_backend(source):
    """Recursive definition detection uses FunctionId, not owner-scope identity."""
    with pytest.raises(CompileError, match='Recursive (module definitions|source function calls)'):
        _analyze(source)


def test_inline_dependency_fingerprint_stable_until_semantic_body_or_default_changes():
    """An inline source change invalidates the physical parent without a new group."""
    from NodeForge.function_instances import FunctionCompilationTrace, inline_semantic_fingerprint

    original = '''def inner(value: Float, offset: Float = 2.0, __inline__=True) -> Float:
    return value + offset
result = inner(3.0)
'''
    def fingerprint(source):
        calls, session = _analyze(source)
        call = calls[0].materialization
        prepared = session.get_inline_prepared(call)
        with FunctionCompilationTrace().group('outer', {'source': 'unchanged'}) as frame:
            frame.record_inline_dependency(call, inline_semantic_fingerprint(prepared))
            assert len(frame.child_rows) == 1
            assert frame.child_rows[0]['owner'] == inline_expansion_owner_scope(call.call_site)
            return frame.finish('unchanged-interface').fingerprint

    assert fingerprint(original) == fingerprint(original)
    assert fingerprint(original) != fingerprint(original.replace('value + offset', 'value * offset'))
    assert fingerprint(original) != fingerprint(original.replace('2.0', '4.0'))


def test_inline_dependency_rejects_grouped_or_invalid_materialization():
    """Trace recording must never create an inline owner for physical SHARED calls."""
    from NodeForge.function_instances import FunctionCompilationTrace, inline_semantic_fingerprint

    calls, session = _analyze('''def leaf(value: Float, __inline__=True) -> Float:
    return value
result = leaf(3.0)
''')
    call = calls[0].materialization
    prepared = session.get_inline_prepared(call)
    with FunctionCompilationTrace().group('parent', {}) as frame:
        with pytest.raises(ValueError, match='INLINE materialization'):
            frame.record_inline_dependency(IRFunctionMaterialization(call.callee, Mode.SHARED), 'abc')
        with pytest.raises(ValueError, match='INLINE materialization'):
            frame.record_inline_dependency('bad materialization', 'abc')
        assert not frame.child_rows
    with pytest.raises(TypeError, match='PreparedSourceCallable'):
        inline_semantic_fingerprint(object())


def test_inline_fingerprint_includes_extension_revision():
    """A changed extension digest invalidates the same prepared source body."""
    from dataclasses import replace
    from NodeForge.function_instances import inline_semantic_fingerprint
    from NodeForge.semantic.source_callables import PreparedSourceCallable

    calls, session = _analyze('''def leaf(value: Float, __inline__=True) -> Float:
    return value
result = leaf(1.0)
''')
    original = session.get_inline_prepared(calls[0].materialization)
    def variant(digest):
        group = replace(original.group, extension_dependencies=((('vendor', 'extension'), digest),))
        return PreparedSourceCallable(original.contract, group, original.source_record)

    assert inline_semantic_fingerprint(variant('revision-A')) == inline_semantic_fingerprint(variant('revision-A'))
    assert inline_semantic_fingerprint(variant('revision-A')) != inline_semantic_fingerprint(variant('revision-B'))
