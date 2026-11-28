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


def test_recursion_diagnostic_follows_caller_to_callee_direction():
    """A directed three-definition cycle must read in source-call order."""
    source = '''def A():
    return C()
def B():
    return A()
def C():
    return B()'''
    with pytest.raises(CompileError, match='Recursive module definitions') as raised:
        _graph(source)
    assert 'A -> C -> B -> A' in str(raised.value)


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


def test_nested_compile_time_assignments_select_the_reachable_entry():
    """A constant set inside a known branch is visible to its nested if."""
    source = '''FLAG = False

def a():
    if True:
        FLAG = True
        if FLAG:
            value = b()
    return 1

def b():
    return 2
'''
    graph = _graph(source)
    assert {(src.name, dst.name) for src, dst in graph.edges} == {('a', 'b')}
    assert [entry.name for entry in graph.entries] == ['a']


def test_nested_compile_time_false_branch_does_not_create_a_false_cycle():
    """Source-order updates to a selected nested branch prune a dead call."""
    source = '''FLAG = True

def a():
    if True:
        FLAG = False
        if FLAG:
            b()
    return 1

def b():
    return a()
'''
    graph = _graph(source)
    assert {(src.name, dst.name) for src, dst in graph.edges} == {('b', 'a')}
    assert [entry.name for entry in graph.entries] == ['b']


def test_nested_compile_time_reachability_reports_real_recursion():
    """Do not lose a genuinely reachable edge in a mutually recursive pair."""
    source = '''FLAG = False

def a():
    if True:
        FLAG = True
        if FLAG:
            b()
    return 1

def b():
    return a()
'''
    with pytest.raises(CompileError, match='Recursive module definitions'):
        _graph(source)


def test_selected_branch_assignments_reach_following_sibling_statements():
    """A known branch's updated constant survives beyond the if statement."""
    source = '''FLAG = True

def a():
    if True:
        FLAG = False
    if FLAG:
        b()
    return 1

def b():
    return 2
'''
    graph = _graph(source)
    assert not graph.edges
    assert [entry.name for entry in graph.entries] == ['a', 'b']


def test_runtime_branch_still_retains_both_possible_calls():
    """A runtime Bool cannot erase one of two potentially reachable callees."""
    source = '''FLAG = False

def a(flag: Bool):
    if flag:
        FLAG = True
        if FLAG:
            b()
    else:
        c()
    return 1

def b():
    return 2

def c():
    return 3
'''
    graph = _graph(source)
    assert {(src.name, dst.name) for src, dst in graph.edges} == {('a', 'b'), ('a', 'c')}
    assert [entry.name for entry in graph.entries] == ['a']


def test_repeat_zone_state_does_not_become_a_false_compile_time_constant():
    """Runtime loop state stays dynamic when the graph scans individual statements."""
    source = '''def a(count: Int):
    x = 1
    for i in repeat_range(count):
        x = x + 1
        if x == 2:
            b()
    return 1

def b():
    return 2
'''
    graph = _graph(source)
    assert {(src.name, dst.name) for src, dst in graph.edges} == {('a', 'b')}
    assert [entry.name for entry in graph.entries] == ['a']


def test_runtime_branch_writes_cannot_be_treated_as_unconditional_constants():
    """After a runtime if, a possibly changed flag keeps dependent calls reachable."""
    source = '''FLAG = False

def a(cond: Bool):
    if cond:
        FLAG = True
    if FLAG:
        b()
    return 1

def b():
    return 2
'''
    graph = _graph(source)
    assert {(src.name, dst.name) for src, dst in graph.edges} == {('a', 'b')}


@pytest.mark.parametrize('iterator', ('repeat_range(count)', 'range(count)'))
def test_runtime_loop_index_shadows_module_constant_in_graph(iterator):
    """A runtime index, not an outer constant, determines reachable callees."""
    source = f'''i = 100

def a(count: Int):
    x = 1.0
    for i in {iterator}:
        if i == 1:
            x = b(x)
        x = x + 1.0
    return x

def b(x: Float):
    return x + 10.0
'''
    shadowed = _graph(source)
    control = _graph(source.replace('i = 100\n', ''))
    expected = {('a', 'b')}
    assert {(src.name, dst.name) for src, dst in shadowed.edges} == expected
    assert shadowed.edges == control.edges
    assert [ref.name for ref in shadowed.entries] == ['a']


def test_runtime_loop_target_scope_restores_module_constant_after_loop():
    """The loop body sees a dynamic index; a later sibling sees the outer constant."""
    graph = _graph('''i = 100

def a(count: Int):
    x = 1.0
    for i in repeat_range(count):
        if i == 1:
            x = b(x)
    if i == 100:
        x = c(x)
    if i == 1:
        x = d(x)
    return x

def b(x: Float):
    return x + 1.0

def c(x: Float):
    return x + 1.0

def d(x: Float):
    return x + 1.0
''')
    assert {(src.name, dst.name) for src, dst in graph.edges} == {
        ('a', 'b'), ('a', 'c'),
    }
    assert [ref.name for ref in graph.entries] == ['a', 'd']


def test_nested_runtime_loop_indices_shadow_outer_constant():
    """A nested runtime loop with the same target name retains dynamic reachability."""
    graph = _graph('''i = 100

def a(count: Int):
    x = 1.0
    for i in repeat_range(count):
        for i in repeat_range(2):
            if i == 1:
                x = b(x)
    return x

def b(x: Float):
    return x + 10.0
''')
    assert {(src.name, dst.name) for src, dst in graph.edges} == {('a', 'b')}


def test_module_constant_outside_runtime_loop_remains_compile_time():
    """Shadowing is lexical, not a permanent removal of the module value."""
    graph = _graph('''i = 100

def a(count: Int):
    x = 1.0
    if i == 1:
        x = b(x)
    for i in repeat_range(count):
        x = x + 1.0
    return x

def b(x: Float):
    return x + 10.0
''')
    assert not graph.edges


@pytest.mark.parametrize('iterator', ('range(3)', '[0, 1, 2]'))
def test_static_loop_target_shadows_same_named_module_constant(iterator):
    """A known iterable binds each item, never the enclosing module constant."""
    graph = _graph(f'''i = 100

def a():
    x = 1.0
    for i in {iterator}:
        if i == 1:
            x = b(x)
    return x

def b(x: Float):
    return x + 10.0
''')
    assert {(src.name, dst.name) for src, dst in graph.edges} == {('a', 'b')}
    assert [ref.name for ref in graph.entries] == ['a']


def test_static_loop_does_not_invent_calls_from_unvisited_iteration_values():
    graph = _graph('''i = 100

def a():
    x = 1.0
    for i in [2, 3]:
        if i == 1:
            x = b(x)
    return x

def b(x: Float):
    return x + 10.0
''')
    assert not graph.edges
    assert [ref.name for ref in graph.entries] == ['a', 'b']


def test_static_loop_compile_time_writes_flow_between_iterations():
    """A later unrolled iteration sees compile-time writes of its predecessor."""
    source = '''FLAG = False

def a(x: Float = 1):
    for i in [0, 1]:
        if FLAG:
            x = b(x)
        FLAG = True
    return x

def b(x: Float):
    return x + 10
'''
    graph = _graph(source)
    assert {(src.name, dst.name) for src, dst in graph.edges} == {('a', 'b')}
    assert [entry.name for entry in graph.entries] == ['a']


def test_static_loop_compile_time_writes_flow_to_following_statement():
    """After a fully unrolled loop, later tests see its final CT state."""
    graph = _graph('''FLAG = False

def a():
    for i in [0, 1]:
        FLAG = True
    if FLAG:
        b()
    return 1

def b():
    return 2
''')
    assert {(src.name, dst.name) for src, dst in graph.edges} == {('a', 'b')}
    assert [entry.name for entry in graph.entries] == ['a']


def test_static_loop_compile_time_false_state_does_not_add_spurious_edge():
    """An overwritten condition must not leave a phantom call or false cycle."""
    graph = _graph('''FLAG = True

def a():
    for i in [0, 1]:
        FLAG = False
        if FLAG:
            b()
    return 1

def b():
    return a()
''')
    assert {(src.name, dst.name) for src, dst in graph.edges} == {('b', 'a')}
    assert [entry.name for entry in graph.entries] == ['b']


def test_static_loop_compile_time_later_iteration_detects_real_cycle():
    """The second iteration's reachable call participates in cycle detection."""
    source = '''FLAG = False

def a():
    for i in [0, 1]:
        if FLAG:
            b()
        FLAG = True
    return 1

def b():
    return a()
'''
    with pytest.raises(CompileError, match='Recursive module definitions'):
        _graph(source)


def test_static_loop_target_restores_outer_compile_time_binding_after_loop():
    """The lexical loop target must not overwrite a module-level constant."""
    graph = _graph('''i = 100

def a():
    for i in [0, 1]:
        if i == 1:
            b()
    if i == 100:
        c()
    return 1

def b():
    return 2

def c():
    return 3
''')
    assert {(src.name, dst.name) for src, dst in graph.edges} == {('a', 'b'), ('a', 'c')}
    assert [entry.name for entry in graph.entries] == ['a']


def test_nested_static_loops_carry_state_across_outer_iterations():
    """Effects in an inner unrolled loop reach the next outer iteration."""
    graph = _graph('''FLAG = False

def a():
    for outer in [0, 1]:
        for inner in [0, 1]:
            if FLAG:
                b()
            FLAG = True
    return 1

def b():
    return 2
''')
    assert {(src.name, dst.name) for src, dst in graph.edges} == {('a', 'b')}
    assert [entry.name for entry in graph.entries] == ['a']

@pytest.mark.parametrize(('assignment', 'expected'), [
    ('', 'helper'),
    ('helper = x + 1.0\n    ', 'helper'),
])
def test_shadowed_module_function_is_copied_as_a_local_numeric_value(assignment, expected):
    """A textual RHS is not a source alias when it resolves to a function local."""
    param = 'helper: Float' if not assignment else 'x: Float'
    src = f'''def helper(x: Float) -> Float:
    return x * 2.0

def outer({param}) -> Float:
    {assignment}copied = helper
    return copied
result = outer(5.0)'''
    assert not _graph(src).edges
    environment = ResolvedEnvironment({name: ResolvedCatalog(name, {})
                                       for name in ('functions', 'examples', 'local')})
    session = SourceCallableSession(resolved_environment=environment)
    analyze_group_source(src,
        compilation_identity=GroupCompilationIdentity(None, 'ROOT/numeric-shadow', 'ROOT/numeric-shadow', 'ROOT/numeric-shadow'),
        resolved_environment=environment, source_callable_session=session)
    assert {item.contract.function_id.name for item in session.prepared.values()} == {'outer'}


@pytest.mark.parametrize('assignment', ('', 'helper = x + 1.0\n    '))
def test_call_of_copied_numeric_local_is_rejected_without_false_call_edge(assignment):
    """A number cannot become a module function merely by sharing its name."""
    param = 'helper: Float' if not assignment else 'x: Float'
    src = f'''def helper(x: Float) -> Float:
    return x * 2.0

def outer({param}) -> Float:
    {assignment}copied = helper
    return copied(3.0)
result = outer(5.0)'''
    assert not _graph(src).edges
    environment = ResolvedEnvironment({name: ResolvedCatalog(name, {})
                                       for name in ('functions', 'examples', 'local')})
    session = SourceCallableSession(resolved_environment=environment)
    with pytest.raises(CompileError, match=r"Cannot call 'copied'.*line \d+, column \d+"):
        analyze_group_source(src,
            compilation_identity=GroupCompilationIdentity(None, 'ROOT/noncallable-shadow', 'ROOT/noncallable-shadow', 'ROOT/noncallable-shadow'),
            resolved_environment=environment, source_callable_session=session)
    assert not any(item.contract.function_id.name == 'helper' for item in session.prepared.values())


def test_chained_source_aliases_keep_canonical_function_identity():
    src = '''def outer(x: Float) -> Float:
    first = helper
    second = first
    return second(x)

def helper(x: Float) -> Float:
    return x * 2.0
result = outer(5.0)'''
    assert {(a.name, b.name) for a, b in _graph(src).edges} == {('outer', 'helper')}
    environment = ResolvedEnvironment({name: ResolvedCatalog(name, {})
                                       for name in ('functions', 'examples', 'local')})
    session = SourceCallableSession(resolved_environment=environment)
    analyze_group_source(src,
        compilation_identity=GroupCompilationIdentity(None, 'ROOT/chained-source-alias', 'ROOT/chained-source-alias', 'ROOT/chained-source-alias'),
        resolved_environment=environment, source_callable_session=session)
    assert {item.contract.function_id.name for item in session.prepared.values()} == {'outer', 'helper'}


def test_copy_of_shadowed_module_level_alias_remains_numeric():
    """A parameter shadows aliases exported by the same source module too."""
    src = '''module_alias = helper

def helper(x: Float) -> Float:
    return x * 2.0

def outer(module_alias: Float) -> Float:
    copied = module_alias
    return copied
result = outer(5.0)'''
    assert not _graph(src).edges
    environment = ResolvedEnvironment({name: ResolvedCatalog(name, {})
                                       for name in ('functions', 'examples', 'local')})
    session = SourceCallableSession(resolved_environment=environment)
    analyze_group_source(src,
        compilation_identity=GroupCompilationIdentity(None, 'ROOT/shadow-module-alias', 'ROOT/shadow-module-alias', 'ROOT/shadow-module-alias'),
        resolved_environment=environment, source_callable_session=session)
    assert {item.contract.function_id.name for item in session.prepared.values()} == {'outer'}


def test_function_body_read_before_later_local_shadows_source_alias():
    """A later normal assignment owns the name from function entry, not from use."""
    src = '''def helper(x: Float) -> Float:
    return x * 2.0

def outer(x: Float) -> Float:
    copied = helper
    helper = x + 1.0
    return copied
result = outer(5.0)'''
    assert not _graph(src).edges
    environment = ResolvedEnvironment({name: ResolvedCatalog(name, {})
                                       for name in ('functions', 'examples', 'local')})
    with pytest.raises(CompileError, match=r"Local name 'helper' used before assignment"):
        analyze_group_source(src,
            compilation_identity=GroupCompilationIdentity(None, 'ROOT/later-shadow', 'ROOT/later-shadow', 'ROOT/later-shadow'),
            resolved_environment=environment,
            source_callable_session=SourceCallableSession(resolved_environment=environment))


def _uninvoked(source):
    """Analyze actual executable IR and return selected canonical entry names."""
    from NodeForge.semantic.source_modules import uninvoked_entries
    environment = ResolvedEnvironment({name: ResolvedCatalog(name, {})
                                       for name in ('functions', 'examples', 'local')})
    session = SourceCallableSession(resolved_environment=environment)
    prepared = analyze_group_source(source,
        compilation_identity=GroupCompilationIdentity(None, 'ROOT/entry-test', 'ROOT/entry-test', 'ROOT/entry-test'),
        resolved_environment=environment, source_callable_session=session)
    return [ref.name for ref in uninvoked_entries(prepared.source_call_graph, prepared.body)]


@pytest.mark.parametrize(('root', 'expected'), [
    ('result = a()', ['preview', 'other']),
    ('alias = a\nresult = alias()', ['preview', 'other']),
    ('alias = a', ['a', 'preview', 'other']),
    ('result = a(__inline__=True)', ['preview', 'other']),
    ('result = a(__unique__=True)', ['preview', 'other']),
    ('result = a() + a()', ['preview', 'other']),
    ('a()', ['preview', 'other']),
    ('result = helper()', ['a', 'preview', 'other']),
    ('for i in range(2):\n    result = a()', ['preview', 'other']),
    ('for i in range(0):\n    a()', ['a', 'preview', 'other']),
    ('result = a()\nfor i in repeat_range(2):\n    result = result + a()', ['preview', 'other']),
    ('if False:\n    result = a()\nelse:\n    result = preview()', ['other']),
])
def test_uninvoked_entries_from_root_ir(root, expected):
    """Only root invocations suppress independent entries; dependencies stay nested."""
    declarations = '''def a() -> Float:
    return 1.0

def helper() -> Float:
    return 2.0

def preview() -> Float:
    return helper()

def other() -> Float:
    return 3.0
'''
    assert _uninvoked(declarations + root) == expected
    assert [ref.name for ref in _graph(declarations + root).entries] == ['a', 'preview', 'other']


@pytest.mark.parametrize('source', [
    'def a(__inline__=True) -> Float:\n    return 1.0\nx = a()',
    'def a():\n    return 1.0, 2.0\nx, y = a()',
    'def a():\n    return {"first": 1.0, "second": 2.0}\nx = a().first',
    'def a() -> Bool:\n    return True\nif a():\n    x = 1.0\nelse:\n    x = 2.0',
    'def a() -> Int:\n    return 2\nx = 1.0\nfor i in repeat_range(a()):\n    x = x + 1.0',
    'x = a()\ndef a() -> Float:\n    return 1.0',
])
def test_uninvoked_entry_expression_and_control_flow_calls(source):
    """Expression, condition, iteration and forward targets are canonical invocations."""
    assert _uninvoked(source) == []


def test_foreign_same_name_call_does_not_suppress_local_entry(tmp_path, monkeypatch):
    """The same leaf name in another module has a distinct definition identity."""
    from test_nested_package_functions import _install_namespace_package
    from NodeForge import local_sources
    monkeypatch.setattr(local_sources, "_catalog_input_paths", lambda: ((), ()))
    from NodeForge import packages
    from NodeForge.environment_resolution import resolve_environment
    from NodeForge.semantic.source_modules import uninvoked_entries
    try:
        _install_namespace_package(tmp_path, {'foreign.nf': 'def a() -> Float:\n    return 2.0\n'})
        environment = resolve_environment()
        source = 'from packages import nested\ndef a() -> Float:\n    return 1.0\nx = nested.a()'
        prepared = analyze_group_source(source,
            compilation_identity=GroupCompilationIdentity(None, 'ROOT/entry-test', 'ROOT/entry-test', 'ROOT/entry-test'),
            resolved_environment=environment,
            source_callable_session=SourceCallableSession(resolved_environment=environment))
        assert [ref.name for ref in uninvoked_entries(prepared.source_call_graph, prepared.body)] == ['a']
    finally:
        packages.set_packages_dir_for_tests(None)
