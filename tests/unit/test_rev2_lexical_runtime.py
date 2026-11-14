"""Rev2 F06-F09: function-local ownership is independent of outer source bindings."""

import pytest

from NodeForge.compiler_identities import GroupCompilationIdentity, ModuleId
from NodeForge.errors import CompileError
from NodeForge.resolved_environment import ResolvedCatalog, ResolvedEnvironment
from NodeForge.semantic.group import analyze_group_source
from NodeForge.semantic.source_callable_session import SourceCallableSession
from NodeForge.semantic.source_modules import (
    parse_source_module, resolve_source_call_graph, resolve_source_module,
)


def _compile(source):
    environment = ResolvedEnvironment({
        name: ResolvedCatalog(name, {}) for name in ('functions', 'examples', 'local')
    })
    session = SourceCallableSession(resolved_environment=environment)
    return analyze_group_source(
        source,
        compilation_identity=GroupCompilationIdentity(None, 'ROOT/lexical', 'ROOT/lexical', 'ROOT/lexical'),
        resolved_environment=environment,
        source_callable_session=session,
    )


def _edges(source):
    resolved = resolve_source_module(parse_source_module(source, ModuleId('ROOT_TEXT', 'lexical')))
    return {(left.name, right.name) for left, right in resolve_source_call_graph(resolved).edges}


def test_parameter_shadows_named_function_without_affecting_sibling_call_graph():
    source = '''def h(x: Float):
    return x * 10
def f(h: Float):
    return h + 1
def g(x: Float):
    return h(x)
y = f(5) + g(2)'''
    group = _compile(source)
    assert {(a.name, b.name) for a, b in group.source_call_graph.edges} == {('g', 'h')}


def test_ordinary_local_shadows_same_named_function_without_affecting_other_scopes():
    source = '''def h(x: Float):
    return x * 10
def f(x: Float):
    h = x
    return h + 1
def g(x: Float):
    return h(x)
y = f(5) + g(2)'''
    group = _compile(source)
    assert {(a.name, b.name) for a, b in group.source_call_graph.edges} == {('g', 'h')}


@pytest.mark.parametrize('body', [
    'return h(5)',
    'h = 3\n    return h(5)',
])
def test_local_non_callable_never_falls_back_to_outer_function(body):
    source = f'''def h(x: Float):
    return x + 5
def f(h: Float):
    {body}
y = f(3)'''
    with pytest.raises(CompileError, match=r'(Cannot call|used before assignment)'):
        _compile(source)
    assert ('f', 'h') not in _edges(source)


def test_local_binding_read_before_assignment_is_not_module_constant():
    source = '''K = 100
def f(x: Float):
    y = K + 1
    K = x
    return y
result = f(1)'''
    with pytest.raises(CompileError, match=r"Local name 'K' used before assignment"):
        _compile(source)


def test_local_binding_call_before_assignment_is_not_module_function():
    source = '''def h(x: Float):
    return x
def f(x: Float):
    y = h(x)
    h = x
    return y
result = f(1)'''
    with pytest.raises(CompileError, match='Cannot call'):
        _compile(source)
    assert not _edges(source)


def test_ambiguous_conditional_local_assignment_does_not_inherit_module_name():
    source = '''K = 100
def f(x: Float, flag: Bool):
    if flag:
        K = x
    else:
        y = x
    return K
result = f(1, True)'''
    with pytest.raises(CompileError):
        _compile(source)


def test_alias_call_before_its_lexical_assignment_is_invalid():
    source = '''def h(x: Float):
    return x + 1
def f(x: Float):
    y = helper(x)
    helper = h
    return y
result = f(2)'''
    with pytest.raises(CompileError, match=r'(helper.*before assignment|Cannot call.*helper|Local alias.*before assignment)'):
        _compile(source)


def test_assigned_alias_resolves_to_canonical_function():
    source = '''def h(x: Float):
    return x + 1
def f(x: Float):
    helper = h
    return helper(x)
result = f(2)'''
    group = _compile(source)
    assert {(a.name, b.name) for a, b in group.source_call_graph.edges} == {('f','h')}


@pytest.mark.parametrize('iterator', ['range(2)', 'repeat_range(count)'])
def test_loop_index_can_temporarily_shadow_outer_callable(iterator):
    source = f'''def h(x: Float):
    return x + 10
def f(count: Int):
    total = 0.0
    for h in {iterator}:
        total = total + h
    return total + h(1.0)
result = f(3)'''
    group = _compile(source)
    assert {(a.name, b.name) for a, b in group.source_call_graph.edges} == {('f','h')}


@pytest.mark.parametrize('iterator', ['range(2)', 'repeat_range(count)'])
def test_calling_loop_index_does_not_fall_back_to_outer_callable(iterator):
    source = f'''def h(x: Float):
    return x + 10
def f(count: Int):
    total = 0.0
    for h in {iterator}:
        total = h(1.0)
    return total
result = f(3)'''
    with pytest.raises(CompileError, match='Cannot call'):
        _compile(source)
    assert not _edges(source)
