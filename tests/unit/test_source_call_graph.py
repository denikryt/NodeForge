"""Source-call graph derives entry candidates from resolved lexical identities."""

import pytest

from NodeForge.compiler_identities import ModuleId
from NodeForge.errors import CompileError
from NodeForge.semantic.source_modules import parse_source_module, resolve_source_module, resolve_source_call_graph
from NodeForge.semantic.source_callable_session import SourceCallableSession
from NodeForge.compiler_identities import GroupCompilationIdentity
from NodeForge.resolved_environment import ResolvedCatalog, ResolvedEnvironment
from NodeForge.semantic.group import analyze_group_source


def _module(source):
    """Create one complete module snapshot and resolve its declarations."""
    return resolve_source_module(parse_source_module(source, ModuleId('ROOT_TEXT', 'graph-test')))


def _graph(source):
    """Resolve source-call edges without attempting Blender materialization."""
    return resolve_source_call_graph(_module(source))


def test_call_graph_uses_incoming_edges_for_definition_only_entries():
    """A->B->C plus unrelated D selects exactly A and D as entries."""
    graph = _graph('''def A():
    return B()
def B():
    return C()
def C():
    return 1
def D():
    return 2''')
    assert {ref.name for ref in graph.vertices} == {'A','B','C','D'}
    assert {(a.name, b.name) for a, b in graph.edges} == {('A','B'),('B','C')}
    assert [ref.name for ref in graph.entries] == ['A','D']


def test_aliases_resolve_to_canonical_definition_even_when_forward():
    """Both module aliases and function-local aliases have the original target identity."""
    graph = _graph('''alias = B
def A():
    helper = alias
    return helper()
def B():
    return 3''')
    assert {(a.name, b.name) for a, b in graph.edges} == {('A', 'B')}
    assert [ref.name for ref in graph.entries] == ['A']


def test_compile_time_unreachable_branches_do_not_add_edges():
    """Unreachable body calls do not influence automatic root entry selection."""
    graph = _graph('''def A():
    if False:
        B()
    return 1
def B():
    return 2''')
    assert not graph.edges
    assert [ref.name for ref in graph.entries] == ['A','B']


def test_top_level_invocations_do_not_add_internal_graph_edges():
    """Entry computation is unaffected by executable composition calls."""
    graph = _graph('''def A():
    return 1
def B():
    return 2
x = B()''')
    assert not graph.edges
    assert [ref.name for ref in graph.entries] == ['A','B']


@pytest.mark.parametrize('source,cycle', [
    ('def A():\n    return A()', 'A -> A'),
    ('def A():\n    return B()\ndef B():\n    return A()', 'A -> B -> A'),
    ('alias = A\ndef A():\n    return alias()', 'A -> A'),
])
def test_recursion_is_rejected_from_resolved_edges(source, cycle):
    """SCCs and self-loops are diagnosed rather than published as entry groups."""
    with pytest.raises(CompileError, match='Recursive module definitions') as raised:
        _graph(source)
    assert cycle in str(raised.value)


def test_duplicate_alias_rejected_before_lowering():
    """Two declarations may not claim the same function-alias binding."""
    with pytest.raises(CompileError, match='Duplicate function alias'):
        _module('def f():\n    return 1\na = f\na = f')


def test_alias_call_produces_same_cached_function_definition():
    """Source aliases used at level zero resolve the original function specialization."""
    environment = ResolvedEnvironment({name: ResolvedCatalog(name, {}) for name in ('functions','examples','local')})
    session = SourceCallableSession(resolved_environment=environment)
    source = '''alias = later
x = alias(1)
def later(n: Float) -> Float:
    return n + 1
result = x'''
    group = analyze_group_source(source,
        compilation_identity=GroupCompilationIdentity(None, 'ROOT/alias', 'ROOT/alias', 'ROOT/alias'),
        resolved_environment=environment, source_callable_session=session)
    assert len(session.prepared) == 1
    assert next(iter(session.prepared.values())).contract.function_id.name == 'later'
    assert not group.interface.outputs
    assert [ref.name for ref in group.source_call_graph.entries] == ["later"]


def test_nested_definition_alias_compiles_through_the_same_semantic_lookup():
    """An alias inside a function body materializes the canonical callee."""
    environment = ResolvedEnvironment({name: ResolvedCatalog(name, {}) for name in ('functions','examples','local')})
    session = SourceCallableSession(resolved_environment=environment)
    source = '''def A(n: Float) -> Float:
    helper = B
    return helper(n)
def B(n: Float) -> Float:
    return n * 2
x = A(2)
result = x'''
    group = analyze_group_source(source,
        compilation_identity=GroupCompilationIdentity(None, 'ROOT/nested-alias', 'ROOT/nested-alias', 'ROOT/nested-alias'),
        resolved_environment=environment, source_callable_session=session)
    assert {(a.name, b.name) for a, b in group.source_call_graph.edges} == {('A','B')}
    assert {prepared.contract.function_id.name for prepared in session.prepared.values()} == {'A', 'B'}

@pytest.mark.parametrize('flag,entries,recursive', [
    ('False', ['b'], False),
    ('True', None, True),
])
def test_module_compile_time_flags_control_reachable_recursive_edges(flag, entries, recursive):
    """Source-level compile-time constants select edges before cycle analysis."""
    source = f'''FLAG = {flag}
def a():
    if FLAG:
        b()
    return 1
def b():
    return a()'''
    if recursive:
        with pytest.raises(CompileError, match='Recursive module definitions'):
            _graph(source)
    else:
        assert [ref.name for ref in _graph(source).entries] == entries


def test_local_compile_time_assignments_are_observed_in_source_order():
    """A later constant must not reinterpret an earlier branch or its edges."""
    source = '''def a():
    flag = False
    if flag:
        b()
    flag = True
    if flag:
        c()
    return 1
def b():
    return 2
def c():
    return 3'''
    graph = _graph(source)
    assert {(u.name, v.name) for u, v in graph.edges} == {('a', 'c')}
    assert [entry.name for entry in graph.entries] == ['a', 'b']


def test_parameter_shadows_same_named_module_constant_in_graph():
    """A dynamic function parameter cannot be treated as the module's constant."""
    source = '''FLAG = False
def a(FLAG: Bool):
    if FLAG:
        b()
    return 1
def b():
    return 2'''
    graph = _graph(source)
    assert {(u.name, v.name) for u, v in graph.edges} == {('a', 'b')}


def test_module_snapshot_revision_changes_edges_between_compilations():
    """Each independently resolved source revision has its own CT environment."""
    original = '''CHOOSE = False
def a():
    if CHOOSE:
        b()
    return 1
def b():
    return 2'''
    changed = original.replace('CHOOSE = False', 'CHOOSE = True')
    first = _graph(original)
    second = _graph(changed)
    assert not first.edges
    assert {(u.name, v.name) for u, v in second.edges} == {('a', 'b')}
    assert not first.edges  # resolved snapshots do not read live source again
